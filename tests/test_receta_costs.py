"""`services/receta_costs.py` -- loading the receta graph and costing it.

`graph.py` owns the arithmetic and is tested against dataclasses in
`test_graph.py`. What is tested here is the wiring: that a real insumo's
cost reaches a real recipe line, that a preparación is priced per unit of
its own yield, that incompleteness propagates up without a partial sum ever
appearing, and that the three data errors that *can* reach the traversal
land as a legible message rather than as a 500 on the menu list.
"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbcase import DbTestCase  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models import CatalogPricePoint, CatalogProduct, InsumoFuente, RecetaLinea  # noqa: E402
from app.services import insumos as insumos_svc  # noqa: E402
from app.services import receta_costs  # noqa: E402
from app.services import recetas as svc  # noqa: E402

HOY = dt.date(2026, 9, 2)


class CosteoTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self._n = 0

    def insumo_con_precio(self, nombre, unidad_base, precio_por_base, *, merma="0"):
        """An insumo with one confirmed source and one purchase, priced so
        that `precio_por_base` is exactly its cost per base unit before
        merma (factor 1, so the normalized price *is* the per-unit price).
        """
        self._n += 1
        insumo = insumos_svc.create(
            self.db, nombre=nombre, unidad_base=unidad_base, merma_pct=merma
        )
        producto = CatalogProduct(
            internal_code=f"c-{self._n}", upstream_product_id=self._n,
            supplier_id=self._n, supplier_nombre=f"Proveedor {self._n}",
            description=nombre.upper(),
        )
        self.db.add(producto)
        self.db.flush()
        self.db.add(
            CatalogPricePoint(
                catalog_product_id=producto.id,
                clave=f"{self._n:03d}" + "0" * 47, numero_linea=1,
                fecha=HOY - dt.timedelta(days=3), cantidad=Decimal(1),
                precio_efectivo=Decimal(str(precio_por_base)),
                precio_efectivo_crc=Decimal(str(precio_por_base)),
                precio_efectivo_normalizado_crc=Decimal(str(precio_por_base)),
            )
        )
        self.db.add(
            InsumoFuente(
                insumo_id=insumo.id, catalog_product_id=producto.id,
                factor_conversion=Decimal(1), factor_origen="manual",
                status="confirmed", mapped_by="human", activo=True,
            )
        )
        self.db.commit()
        return insumo

    def insumo_sin_precio(self, nombre="Sal", unidad_base="g"):
        return insumos_svc.create(self.db, nombre=nombre, unidad_base=unidad_base)

    def costear(self, receta):
        return receta_costs.costear(self.db, receta, hoy=HOY)


class UnPlatoSimple(CosteoTestCase):
    def test_suma_las_lineas_convirtiendo_la_unidad(self):
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="2")
        pechuga = self.insumo_con_precio("Pechuga", "g", "4")     # 4 por g
        svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=pechuga.id,
            cantidad="0.18", unidad="kg",
        )
        c = self.costear(plato)
        self.assertEqual(c.estado, "completo")
        self.assertEqual(c.costo_total, Decimal("720.00"))
        self.assertEqual(c.costo_por_porcion, Decimal("360.00"))

    def test_dos_lineas_del_mismo_insumo_en_unidades_distintas(self):
        """D30 end to end: one recipe, one insumo, two units."""
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        pechuga = self.insumo_con_precio("Pechuga", "g", "4")
        svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=pechuga.id,
            cantidad="0.1", unidad="kg",
        )
        svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=pechuga.id,
            cantidad="50", unidad="g",
        )
        self.assertEqual(self.costear(plato).costo_total, Decimal("600.00"))

    def test_la_merma_del_insumo_llega_a_la_linea(self):
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        pechuga = self.insumo_con_precio("Pechuga", "g", "4", merma="20")
        svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=pechuga.id,
            cantidad="100", unidad="g",
        )
        # 4 / 0.8 = 5 per usable gram.
        self.assertEqual(self.costear(plato).costo_total, Decimal("500.00"))


class Incompletitud(CosteoTestCase):
    def test_un_insumo_sin_precio_deja_el_total_en_none_y_lo_nombra(self):
        """D10: no partial sum, not even greyed. The screen names the
        ingredient instead."""
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        pechuga = self.insumo_con_precio("Pechuga", "g", "4")
        sal = self.insumo_sin_precio()
        for insumo in (pechuga, sal):
            svc.agregar_linea(
                self.db, plato, tipo_componente="insumo",
                componente_id=insumo.id, cantidad="10", unidad="g",
            )
        c = self.costear(plato)
        self.assertEqual(c.estado, "incompleto")
        self.assertIsNone(c.costo_total)
        self.assertIsNone(c.costo_por_porcion)
        self.assertEqual(c.faltantes, ["Sal"])

    def test_la_incompletitud_sube_por_una_preparacion_anidada(self):
        salsa = svc.create(
            self.db, nombre="Salsa", tipo="preparacion",
            rendimiento_cantidad="1000", rendimiento_unidad="g",
        )
        sal = self.insumo_sin_precio()
        svc.agregar_linea(
            self.db, salsa, tipo_componente="insumo", componente_id=sal.id,
            cantidad="10", unidad="g",
        )
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        svc.agregar_linea(
            self.db, plato, tipo_componente="receta", componente_id=salsa.id,
            cantidad="45", unidad="g",
        )
        c = self.costear(plato)
        self.assertIsNone(c.costo_total)
        self.assertIn("Sal", c.faltantes)

    def test_una_receta_vacia_cuesta_cero_no_none(self):
        """Nothing is missing -- there is simply nothing in it. Distinct
        from incompleto, and the list says "vacía" rather than "—"."""
        plato = svc.create(self.db, nombre="Vacío", tipo="plato", porciones="1")
        c = self.costear(plato)
        self.assertEqual(c.estado, "completo")
        self.assertEqual(c.costo_total, Decimal(0))


class Preparaciones(CosteoTestCase):
    def test_una_salsa_se_cobra_por_unidad_de_su_rendimiento(self):
        """§7.5: cooking loss lives in the yield and only there. 1 000 g of
        input declared as a 500 g yield doubles the price per gram."""
        salsa = svc.create(
            self.db, nombre="Salsa criolla", tipo="preparacion",
            rendimiento_cantidad="500", rendimiento_unidad="g",
        )
        tomate = self.insumo_con_precio("Tomate", "g", "2")
        svc.agregar_linea(
            self.db, salsa, tipo_componente="insumo", componente_id=tomate.id,
            cantidad="1000", unidad="g",
        )
        c = self.costear(salsa)
        self.assertEqual(c.costo_total, Decimal("2000.00"))
        self.assertEqual(c.costo_por_unidad_rendimiento, Decimal(4))

    def test_la_misma_salsa_en_dos_platos_cuesta_lo_mismo_en_ambos(self):
        salsa = svc.create(
            self.db, nombre="Salsa criolla", tipo="preparacion",
            rendimiento_cantidad="500", rendimiento_unidad="g",
        )
        tomate = self.insumo_con_precio("Tomate", "g", "2")
        svc.agregar_linea(
            self.db, salsa, tipo_componente="insumo", componente_id=tomate.id,
            cantidad="1000", unidad="g",
        )
        platos = []
        for nombre in ("Pollo", "Cerdo"):
            p = svc.create(self.db, nombre=nombre, tipo="plato", porciones="1")
            svc.agregar_linea(
                self.db, p, tipo_componente="receta", componente_id=salsa.id,
                cantidad="45", unidad="g",
            )
            platos.append(p)
        costeos = receta_costs.costear_muchas(self.db, platos, hoy=HOY)
        totales = {c.costo_total for c in costeos.values()}
        self.assertEqual(totales, {Decimal("180.00")})

    def test_la_linea_de_una_preparacion_convierte_su_unidad(self):
        salsa = svc.create(
            self.db, nombre="Salsa", tipo="preparacion",
            rendimiento_cantidad="1000", rendimiento_unidad="g",
        )
        tomate = self.insumo_con_precio("Tomate", "g", "2")
        svc.agregar_linea(
            self.db, salsa, tipo_componente="insumo", componente_id=tomate.id,
            cantidad="1000", unidad="g",
        )
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        svc.agregar_linea(
            self.db, plato, tipo_componente="receta", componente_id=salsa.id,
            cantidad="0.25", unidad="kg",
        )
        # 250 g of a salsa costing 2 per g.
        self.assertEqual(self.costear(plato).costo_total, Decimal("500.00"))

    def test_el_desglose_viene_con_la_linea(self):
        salsa = svc.create(
            self.db, nombre="Salsa", tipo="preparacion",
            rendimiento_cantidad="1000", rendimiento_unidad="g",
        )
        tomate = self.insumo_con_precio("Tomate", "g", "2")
        svc.agregar_linea(
            self.db, salsa, tipo_componente="insumo", componente_id=tomate.id,
            cantidad="100", unidad="g",
        )
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        svc.agregar_linea(
            self.db, plato, tipo_componente="receta", componente_id=salsa.id,
            cantidad="45", unidad="g",
        )
        linea = self.costear(plato).lineas[0]
        self.assertIsNotNone(linea.desglose)
        self.assertEqual(linea.desglose.lineas[0].nombre, "Tomate")


class ErroresDeDatos(CosteoTestCase):
    """The three things `cost_recipe` raises for. All of them mean a row
    exists that write-time validation should have refused, and none of them
    may take down the menu list."""

    def test_un_ciclo_que_sobrevivio_a_la_escritura_se_reporta_con_nombres(self):
        a = svc.create(
            self.db, nombre="Salsa criolla", tipo="preparacion",
            rendimiento_cantidad="100", rendimiento_unidad="g",
        )
        b = svc.create(
            self.db, nombre="Fondo oscuro", tipo="preparacion",
            rendimiento_cantidad="100", rendimiento_unidad="g",
        )
        svc.agregar_linea(
            self.db, a, tipo_componente="receta", componente_id=b.id,
            cantidad="10", unidad="g",
        )
        # Written straight to the table, bypassing the guard -- which is
        # exactly the state the read-time defence exists for.
        self.db.add(
            RecetaLinea(
                receta_id=b.id, orden=0, tipo_componente="receta",
                componente_receta_id=a.id, cantidad=Decimal(10), unidad="g",
            )
        )
        self.db.commit()
        c = self.costear(a)
        self.assertEqual(c.estado, "error")
        self.assertIn("Salsa criolla", c.error)
        self.assertIn("Fondo oscuro", c.error)

    def test_una_unidad_incompatible_se_reporta_como_dato_malo(self):
        """Not as "no price": that would send the operator looking at the
        mapping queue for a problem that is in the recipe line."""
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        aceite = self.insumo_con_precio("Aceite", "ml", "2")
        self.db.add(
            RecetaLinea(
                receta_id=plato.id, orden=0, tipo_componente="insumo",
                insumo_id=aceite.id, cantidad=Decimal(6), unidad="g",
            )
        )
        self.db.commit()
        c = self.costear(plato)
        self.assertEqual(c.estado, "error")
        self.assertIn("g", c.error)
        self.assertIn("ml", c.error)

    def test_un_anidamiento_demasiado_profundo_se_reporta(self):
        preps = []
        for i in range(8):
            preps.append(
                svc.create(
                    self.db, nombre=f"Nivel {i}", tipo="preparacion",
                    rendimiento_cantidad="100", rendimiento_unidad="g",
                )
            )
        for padre, hijo in zip(preps, preps[1:], strict=False):
            self.db.add(
                RecetaLinea(
                    receta_id=padre.id, orden=0, tipo_componente="receta",
                    componente_receta_id=hijo.id, cantidad=Decimal(1), unidad="g",
                )
            )
        self.db.commit()
        c = self.costear(preps[0])
        self.assertEqual(c.estado, "error")
        self.assertIn("niveles", c.error)


class LecturaDespuesDeEscribir(CosteoTestCase):
    """The session is `expire_on_commit=False` on purpose, so a relationship
    loaded before a write stays loaded without it. A cost read through
    `Receta.lineas` right after adding a line was therefore the cost of the
    recipe as it was a moment ago -- a perfectly plausible number, one line
    out of date, with nothing on screen to give it away.
    """

    def test_una_linea_agregada_cuenta_de_inmediato(self):
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        pechuga = self.insumo_con_precio("Pechuga", "g", "4")
        svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=pechuga.id,
            cantidad="100", unidad="g",
        )
        # Warm the relationship, then write again without refreshing.
        self.assertEqual(len(plato.lineas), 1)
        svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=pechuga.id,
            cantidad="100", unidad="g",
        )
        self.assertEqual(self.costear(plato).costo_total, Decimal("800.00"))

    def test_una_linea_borrada_deja_de_contar_de_inmediato(self):
        plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="1")
        pechuga = self.insumo_con_precio("Pechuga", "g", "4")
        linea = svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=pechuga.id,
            cantidad="100", unidad="g",
        )
        self.assertEqual(self.costear(plato).costo_total, Decimal("400.00"))
        svc.eliminar_linea(self.db, linea)
        self.assertEqual(self.costear(plato).costo_total, Decimal(0))


class Cache(CosteoTestCase):
    def test_costear_muchas_precia_cada_insumo_una_sola_vez(self):
        """Nine plates using pechuga price pechuga once. `explicar` is a
        handful of queries per insumo and that adds up across a menu."""
        pechuga = self.insumo_con_precio("Pechuga", "g", "4")
        platos = []
        for i in range(9):
            p = svc.create(self.db, nombre=f"Plato {i}", tipo="plato", porciones="1")
            svc.agregar_linea(
                self.db, p, tipo_componente="insumo", componente_id=pechuga.id,
                cantidad="100", unidad="g",
            )
            platos.append(p)

        from app.services import insumo_costs

        llamadas = []
        original = insumo_costs.explicar

        def contar(db, insumo, **kw):
            llamadas.append(insumo.id)
            return original(db, insumo, **kw)

        insumo_costs.explicar = contar
        try:
            costeos = receta_costs.costear_muchas(self.db, platos, hoy=HOY)
        finally:
            insumo_costs.explicar = original

        self.assertEqual(llamadas, [pechuga.id])
        self.assertEqual({c.costo_total for c in costeos.values()}, {Decimal("400.00")})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class ModoSimulador(CosteoTestCase):
    """§3.9 / D42. The mode's whole promise is that it writes nothing, and
    the failure it must never have is a simulated cost surviving into a
    table the rest of the app reads as truth."""

    def setUp(self):
        super().setUp()
        self.pechuga = self.insumo_con_precio("Pechuga", "g", "4")
        self.plato = svc.create(
            self.db, nombre="Pollo", tipo="plato", porciones="1"
        )
        self.linea = svc.agregar_linea(
            self.db, self.plato, tipo_componente="insumo",
            componente_id=self.pechuga.id, cantidad="180",
        )

    def sim(self, cantidades):
        return receta_costs.simular(self.db, self.plato, cantidades, hoy=HOY)

    def test_devuelve_los_dos_numeros_lado_a_lado(self):
        s = self.sim({self.linea.id: Decimal(160)})
        self.assertEqual(s.actual.costo_total, Decimal("720.00"))
        self.assertEqual(s.simulada.costo_total, Decimal("640.00"))

    def test_no_escribe_absolutamente_nada(self):
        """Not "does not write if you are careful": the substitution happens
        on frozen dataclasses between `cargar_grafo` and `cost_recipe`, so
        there is no write path in there to get wrong."""
        self.sim({self.linea.id: Decimal(999)})
        self.db.expire_all()
        fresca = self.db.get(RecetaLinea, self.linea.id)
        self.assertEqual(fresca.cantidad, Decimal("180.0000"))

    def test_una_simulacion_no_puede_llegar_al_historico(self):
        """D42's third constraint, and the one whose violation would be
        invisible on screen. `receta_costo_historico` is the app's record of
        what a plate actually cost on a given day; a snapshot that picked up
        somebody's half-finished "what if" would be a wrong number that
        looks exactly like a right one."""
        from sqlalchemy import select

        from app.models import RecetaCostoHistorico
        from app.services import receta_historico

        self.sim({self.linea.id: Decimal(999)})
        receta_historico.instantanea(self.db, fecha=HOY)

        filas = list(self.db.scalars(select(RecetaCostoHistorico)))
        self.assertEqual(len(filas), 1)
        # 180 g at ₡4, not the 999 g anybody simulated.
        self.assertEqual(filas[0].costo_total, Decimal("720.00"))

    def test_el_grafo_cargado_no_queda_modificado(self):
        """The caller costs the unmodified graph too, to show both figures
        side by side -- so the substitution has to build new objects rather
        than mutate the ones it was handed."""
        s = self.sim({self.linea.id: Decimal(1)})
        self.assertNotEqual(s.actual.costo_total, s.simulada.costo_total)
        # And the real thing is still the real thing afterwards.
        self.assertEqual(self.costear(self.plato).costo_total, Decimal("720.00"))

    def test_simular_una_preparacion_nombra_a_quien_arrastra(self):
        """«Esta salsa sale 8% más barata» is not the decision anyone is
        making; «estos platos bajan esto» is."""
        salsa = svc.create(
            self.db, nombre="Salsa", tipo="preparacion",
            rendimiento_cantidad="1000", rendimiento_unidad="g",
        )
        interna = svc.agregar_linea(
            self.db, salsa, tipo_componente="insumo",
            componente_id=self.pechuga.id, cantidad="500",
        )
        svc.agregar_linea(
            self.db, self.plato, tipo_componente="receta",
            componente_id=salsa.id, cantidad="100", unidad="g",
        )

        s = receta_costs.simular(
            self.db, salsa, {interna.id: Decimal(250)}, hoy=HOY
        )
        nombres = [r.nombre for r, _antes, _despues in s.afectadas]
        self.assertEqual(nombres, ["Pollo"])
        antes, despues = s.afectadas[0][1], s.afectadas[0][2]
        self.assertLess(despues.costo_total, antes.costo_total)
