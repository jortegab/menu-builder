"""`services/recetas.py` -- writing recetas and the guards that run first.

Weighted toward the quiet failures, because the happy path is what gets
exercised by hand. What is tested here is everything that must be *refused*:
a cycle (direct, transitive, and self), a nest one level past the cap, a
plato used as an ingredient, a unit that crosses dimensions, an archived
insumo, and the two edits to a preparación that would break its consumers
without touching them.
"""

from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbcase import DbTestCase  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models import RecetaLinea  # noqa: E402
from app.services import graph  # noqa: E402
from app.services import insumos as insumos_svc  # noqa: E402
from app.services import recetas as svc  # noqa: E402
from app.services.insumos import ValidationError  # noqa: E402


class RecetasTestCase(DbTestCase):
    def plato(self, nombre="Pollo a la plancha", porciones="1"):
        return svc.create(self.db, nombre=nombre, tipo="plato", porciones=porciones)

    def prep(self, nombre="Salsa criolla", cantidad="2500", unidad="g"):
        return svc.create(
            self.db, nombre=nombre, tipo="preparacion",
            rendimiento_cantidad=cantidad, rendimiento_unidad=unidad,
        )

    def insumo(self, nombre="Pechuga deshuesada", unidad_base="g"):
        return insumos_svc.create(self.db, nombre=nombre, unidad_base=unidad_base)


class Validacion(RecetasTestCase):
    def test_plato_necesita_porciones(self):
        with self.assertRaises(ValidationError) as ctx:
            svc.create(self.db, nombre="Pollo", tipo="plato", porciones="")
        self.assertIn("porciones", ctx.exception.errors)

    def test_preparacion_necesita_rendimiento(self):
        with self.assertRaises(ValidationError) as ctx:
            svc.create(self.db, nombre="Salsa", tipo="preparacion", rendimiento_cantidad="")
        self.assertIn("rendimiento_cantidad", ctx.exception.errors)

    def test_rendimiento_acepta_coma_decimal(self):
        r = svc.create(
            self.db, nombre="Fondo", tipo="preparacion",
            rendimiento_cantidad="2,5", rendimiento_unidad="ml",
        )
        self.assertEqual(r.rendimiento_cantidad, Decimal("2.5"))

    def test_rendimiento_cero_se_rechaza(self):
        # Dividing the batch cost by a yield of zero is what makes it
        # infinite rather than an error, so it is refused as input.
        with self.assertRaises(ValidationError):
            svc.create(
                self.db, nombre="Salsa", tipo="preparacion",
                rendimiento_cantidad="0", rendimiento_unidad="g",
            )

    def test_cambiar_de_tipo_limpia_el_otro_lado(self):
        """The table's CHECK forbids carrying both. A plato that kept a
        stale rendimiento from before its tipo was flipped would fail at
        commit with an IntegrityError and lose the form."""
        r = self.prep()
        svc.update(self.db, r, nombre="Salsa", tipo="plato", porciones="4")
        self.assertEqual(r.tipo, "plato")
        self.assertIsNone(r.rendimiento_cantidad)
        self.assertIsNone(r.rendimiento_unidad)
        self.assertEqual(r.porciones, 4)

    def test_slug_unico_por_nombre_repetido(self):
        a = self.plato(nombre="Salsa")
        b = self.prep(nombre="Salsa")
        self.assertNotEqual(a.slug, b.slug)

    def test_slug_sigue_al_nombre_solo_si_cambia(self):
        r = self.plato()
        antes = r.slug
        svc.update(self.db, r, nombre=r.nombre, tipo="plato", porciones="2")
        self.assertEqual(r.slug, antes)


class Lineas(RecetasTestCase):
    def test_agregar_insumo_usa_su_unidad_base_por_defecto(self):
        plato, insumo = self.plato(), self.insumo()
        linea = svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=insumo.id, cantidad="180"
        )
        self.assertEqual(linea.unidad, "g")
        self.assertEqual(linea.orden, 0)

    def test_la_linea_elige_su_propia_unidad_dentro_de_la_dimension(self):
        """D30: an insumo costed per gram may be asked for in kg on one
        line and in g on the next, in the same recipe."""
        plato, insumo = self.plato(), self.insumo()
        a = svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=insumo.id,
            cantidad="0.18", unidad="kg",
        )
        b = svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=insumo.id,
            cantidad="20", unidad="g",
        )
        self.assertEqual((a.unidad, b.unidad), ("kg", "g"))

    def test_cruzar_masa_y_volumen_se_rechaza_nombrando_las_dos(self):
        plato, insumo = self.plato(), self.insumo()
        with self.assertRaises(ValidationError) as ctx:
            svc.agregar_linea(
                self.db, plato, tipo_componente="insumo", componente_id=insumo.id,
                cantidad="6", unidad="ml",
            )
        mensaje = ctx.exception.errors["unidad"]
        self.assertIn("ml", mensaje)
        self.assertIn("g", mensaje)

    def test_oz_es_volumen_y_no_sirve_para_un_insumo_en_gramos(self):
        """D31: "6 oz de aceite" is a fluid ounce. It must not silently
        land on an insumo costed per gram."""
        plato, insumo = self.plato(), self.insumo()
        with self.assertRaises(ValidationError):
            svc.agregar_linea(
                self.db, plato, tipo_componente="insumo", componente_id=insumo.id,
                cantidad="6", unidad="oz",
            )

    def test_oz_sirve_para_un_insumo_en_ml(self):
        plato = self.plato()
        aceite = self.insumo(nombre="Aceite de oliva", unidad_base="ml")
        linea = svc.agregar_linea(
            self.db, plato, tipo_componente="insumo", componente_id=aceite.id,
            cantidad="6", unidad="oz",
        )
        self.assertEqual(linea.unidad, "oz")

    def test_cantidad_cero_o_negativa_se_rechaza(self):
        plato, insumo = self.plato(), self.insumo()
        for malo in ("0", "-5"):
            with self.assertRaises(ValidationError, msg=malo):
                svc.agregar_linea(
                    self.db, plato, tipo_componente="insumo",
                    componente_id=insumo.id, cantidad=malo,
                )

    def test_insumo_archivado_se_rechaza_por_nombre(self):
        plato, insumo = self.plato(), self.insumo()
        insumos_svc.set_activo(self.db, insumo, False)
        with self.assertRaises(ValidationError) as ctx:
            svc.agregar_linea(
                self.db, plato, tipo_componente="insumo",
                componente_id=insumo.id, cantidad="1",
            )
        self.assertIn(insumo.nombre, ctx.exception.errors["componente"])

    def test_un_plato_no_puede_ser_ingrediente(self):
        """A plato declares portions, not a yield, so there is no cost per
        gram for a line to multiply by. Refused rather than left to produce
        a line that silently has no cost."""
        plato, otro = self.plato(), self.plato(nombre="Arroz con pollo")
        with self.assertRaises(ValidationError) as ctx:
            svc.agregar_linea(
                self.db, plato, tipo_componente="receta",
                componente_id=otro.id, cantidad="1", unidad="g",
            )
        self.assertIn("plato", ctx.exception.errors["componente"])

    def test_una_preparacion_se_reusa_en_dos_platos(self):
        """PLAN.md §11's own done-when for step 1.5."""
        salsa = self.prep()
        uno, dos = self.plato(nombre="Pollo"), self.plato(nombre="Cerdo")
        for plato in (uno, dos):
            svc.agregar_linea(
                self.db, plato, tipo_componente="receta",
                componente_id=salsa.id, cantidad="45", unidad="g",
            )
        self.assertEqual({r.id for r in svc.usos(self.db, salsa)}, {uno.id, dos.id})
        self.assertEqual(svc.usos_count(self.db, [salsa.id]), {salsa.id: 2})


class Ciclos(RecetasTestCase):
    def test_autoreferencia_se_rechaza(self):
        salsa = self.prep()
        with self.assertRaises(ValidationError) as ctx:
            svc.agregar_linea(
                self.db, salsa, tipo_componente="receta",
                componente_id=salsa.id, cantidad="1", unidad="g",
            )
        self.assertIn(salsa.nombre, ctx.exception.errors["componente"])

    def test_ciclo_transitivo_se_rechaza_con_la_ruta_nombrada(self):
        """Non-negotiable #5: the edge is refused *and* the chain that would
        have closed is spelled out. "referencia circular" is not fixable."""
        a = self.prep(nombre="Salsa criolla")
        b = self.prep(nombre="Fondo oscuro")
        svc.agregar_linea(
            self.db, a, tipo_componente="receta", componente_id=b.id,
            cantidad="100", unidad="g",
        )
        with self.assertRaises(ValidationError) as ctx:
            svc.agregar_linea(
                self.db, b, tipo_componente="receta", componente_id=a.id,
                cantidad="100", unidad="g",
            )
        mensaje = ctx.exception.errors["componente"]
        self.assertIn("Salsa criolla", mensaje)
        self.assertIn("Fondo oscuro", mensaje)
        # And nothing was written.
        self.assertEqual(
            self.db.query(RecetaLinea).filter_by(receta_id=b.id).count(), 0
        )

    def test_un_diamante_no_es_un_ciclo(self):
        base = self.prep(nombre="Fondo")
        a = self.prep(nombre="Salsa A")
        b = self.prep(nombre="Salsa B")
        plato = self.plato()
        for padre in (a, b):
            svc.agregar_linea(
                self.db, padre, tipo_componente="receta", componente_id=base.id,
                cantidad="50", unidad="g",
            )
        for hijo in (a, b):
            svc.agregar_linea(
                self.db, plato, tipo_componente="receta", componente_id=hijo.id,
                cantidad="30", unidad="g",
            )
        self.assertEqual(len(plato.lineas), 2)


class Profundidad(RecetasTestCase):
    def _cadena(self, n):
        """`n` preparaciones, each contained by the previous one."""
        preps = [self.prep(nombre=f"Nivel {i}") for i in range(n)]
        for padre, hijo in zip(preps, preps[1:], strict=False):
            svc.agregar_linea(
                self.db, padre, tipo_componente="receta", componente_id=hijo.id,
                cantidad="10", unidad="g",
            )
        return preps

    def test_la_cadena_del_tope_se_acepta(self):
        preps = self._cadena(graph.MAX_DEPTH + 1)
        self.assertEqual(len(preps), graph.MAX_DEPTH + 1)

    def test_un_nivel_mas_se_rechaza_en_la_escritura(self):
        """The read side would raise RecipeTooDeep and leave the operator
        with a plate they cannot open. Refusing the line keeps the failure
        where they can still fix it."""
        preps = self._cadena(graph.MAX_DEPTH + 1)
        extra = self.prep(nombre="Uno de más")
        with self.assertRaises(ValidationError) as ctx:
            svc.agregar_linea(
                self.db, preps[-1], tipo_componente="receta", componente_id=extra.id,
                cantidad="1", unidad="g",
            )
        self.assertIn(str(graph.MAX_DEPTH), ctx.exception.errors["componente"])

    def test_cuenta_los_dos_lados_del_borde_nuevo(self):
        """Neither half looks deep on its own: a 3-deep sub-tree hung under
        a receta that already sits 3 deep is what overflows."""
        arriba = self._cadena(3)          # arriba[2] sits 2 edges down
        abajo = self._cadena(4)           # abajo[0] has 3 edges below it
        with self.assertRaises(ValidationError):
            svc.agregar_linea(
                self.db, arriba[-1], tipo_componente="receta",
                componente_id=abajo[0].id, cantidad="1", unidad="g",
            )


class Orden(RecetasTestCase):
    def setUp(self):
        super().setUp()
        self.plato_ = self.plato()
        self.insumos = [self.insumo(nombre=f"Insumo {i}") for i in range(3)]
        self.lineas = [
            svc.agregar_linea(
                self.db, self.plato_, tipo_componente="insumo",
                componente_id=i.id, cantidad="1",
            )
            for i in self.insumos
        ]

    def _orden(self):
        return [
            ln.id
            for ln in sorted(
                self.db.query(RecetaLinea).filter_by(receta_id=self.plato_.id).all(),
                key=lambda ln: ln.orden,
            )
        ]

    def test_mover_abajo_y_arriba(self):
        svc.mover_linea(self.db, self.lineas[0], 1)
        self.assertEqual(self._orden()[:2], [self.lineas[1].id, self.lineas[0].id])
        svc.mover_linea(self.db, self.lineas[0], -1)
        self.assertEqual(self._orden(), [ln.id for ln in self.lineas])

    def test_mover_fuera_del_borde_no_hace_nada(self):
        svc.mover_linea(self.db, self.lineas[0], -1)
        svc.mover_linea(self.db, self.lineas[-1], 1)
        self.assertEqual(self._orden(), [ln.id for ln in self.lineas])

    def test_reordenar_ignora_ids_ajenos_y_conserva_los_no_mencionados(self):
        """A drop that raced a delete in another tab must not drop rows or
        renumber someone else's recipe."""
        otro = self.plato(nombre="Otro")
        ajena = svc.agregar_linea(
            self.db, otro, tipo_componente="insumo",
            componente_id=self.insumos[0].id, cantidad="1",
        )
        svc.reordenar(self.db, self.plato_, [self.lineas[2].id, ajena.id, 99999])
        orden = self._orden()
        self.assertEqual(orden[0], self.lineas[2].id)
        self.assertEqual(sorted(orden), sorted(ln.id for ln in self.lineas))
        self.assertEqual(ajena.orden, 0)  # untouched, still first in its own receta

    def test_borrar_compacta_el_orden(self):
        svc.eliminar_linea(self.db, self.lineas[0])
        restantes = sorted(
            self.db.query(RecetaLinea).filter_by(receta_id=self.plato_.id).all(),
            key=lambda ln: ln.orden,
        )
        self.assertEqual([ln.orden for ln in restantes], [0, 1])


class EdicionQueRompeConsumidores(RecetasTestCase):
    def setUp(self):
        super().setUp()
        self.salsa = self.prep()
        self.plato_ = self.plato()
        svc.agregar_linea(
            self.db, self.plato_, tipo_componente="receta",
            componente_id=self.salsa.id, cantidad="45", unidad="g",
        )

    def test_no_se_convierte_en_plato_mientras_la_usen(self):
        """Taking away the yield would leave every consumer `incompleto`
        without anyone touching them."""
        with self.assertRaises(ValidationError) as ctx:
            svc.update(self.db, self.salsa, nombre="Salsa", tipo="plato", porciones="4")
        self.assertIn(self.plato_.nombre, ctx.exception.errors["tipo"])

    def test_no_se_cambia_la_dimension_del_rendimiento_mientras_la_usen(self):
        with self.assertRaises(ValidationError) as ctx:
            svc.update(
                self.db, self.salsa, nombre="Salsa criolla", tipo="preparacion",
                rendimiento_cantidad="2500", rendimiento_unidad="ml",
            )
        self.assertIn(self.plato_.nombre, ctx.exception.errors["rendimiento_unidad"])

    def test_cambiar_la_cantidad_del_rendimiento_si_se_permite(self):
        svc.update(
            self.db, self.salsa, nombre="Salsa criolla", tipo="preparacion",
            rendimiento_cantidad="3000", rendimiento_unidad="g",
        )
        self.assertEqual(self.salsa.rendimiento_cantidad, Decimal("3000"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
