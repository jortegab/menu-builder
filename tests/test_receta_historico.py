"""`services/receta_historico.py` -- the stored daily cost.

The point of storing rather than recomputing (D13) is that the past must
not move when today's inputs change, so most of these tests are about
exactly that: a merma edited today does not rewrite yesterday's point, a
re-run of the snapshot replaces only today's, and a backfill never
overwrites a day that was actually observed.
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

from app.models import (  # noqa: E402
    CatalogPricePoint,
    CatalogProduct,
    InsumoFuente,
    RecetaCostoHistorico,
)
from app.services import insumos as insumos_svc  # noqa: E402
from app.services import receta_historico as hist  # noqa: E402
from app.services import recetas as svc  # noqa: E402

HOY = dt.date(2026, 9, 2)


class HistoricoTestCase(DbTestCase):
    def setUp(self):
        super().setUp()
        self._n = 0
        self.pechuga = self.insumo_con_precios(
            "Pechuga", "g", [("4", 3), ("6", 120)]
        )
        self.plato = svc.create(self.db, nombre="Pollo", tipo="plato", porciones="2")
        svc.agregar_linea(
            self.db, self.plato, tipo_componente="insumo",
            componente_id=self.pechuga.id, cantidad="100", unidad="g",
        )

    def insumo_con_precios(self, nombre, unidad_base, precios, *, merma="0"):
        """An insumo priced at `precio` as of `dias` days before HOY."""
        self._n += 1
        insumo = insumos_svc.create(
            self.db, nombre=nombre, unidad_base=unidad_base, merma_pct=merma
        )
        producto = CatalogProduct(
            internal_code=f"c-{self._n}", upstream_product_id=self._n,
            supplier_id=self._n, supplier_nombre="Proveedor", description=nombre.upper(),
        )
        self.db.add(producto)
        self.db.flush()
        for i, (precio, dias) in enumerate(precios):
            self.db.add(
                CatalogPricePoint(
                    catalog_product_id=producto.id,
                    clave=f"{self._n:03d}" + "0" * 44 + f"{i:03d}", numero_linea=i,
                    fecha=HOY - dt.timedelta(days=dias), cantidad=Decimal(1),
                    precio_efectivo=Decimal(precio),
                    precio_efectivo_crc=Decimal(precio),
                    precio_efectivo_normalizado_crc=Decimal(precio),
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

    def filas(self, receta=None):
        return list(
            self.db.query(RecetaCostoHistorico)
            .filter_by(receta_id=(receta or self.plato).id)
            .order_by(RecetaCostoHistorico.fecha)
            .all()
        )


class Instantanea(HistoricoTestCase):
    def test_guarda_una_fila_por_receta_activa(self):
        n = hist.instantanea(self.db, fecha=HOY)
        self.assertEqual(n, 1)
        (fila,) = self.filas()
        self.assertEqual(fila.fecha, HOY)
        self.assertEqual(fila.costo_total, Decimal("400.00"))
        self.assertEqual(fila.costo_por_porcion, Decimal("200.00"))
        self.assertEqual(fila.estado, "completo")

    def test_registra_el_metodo_y_la_ventana_del_dia(self):
        """A figure has to be readable back with the assumptions that
        produced it, or the chart is six months of unlabelled numbers."""
        hist.instantanea(self.db, fecha=HOY)
        (fila,) = self.filas()
        self.assertEqual(fila.metodo_costeo, "median_window")
        self.assertEqual(fila.ventana_dias, 90)

    def test_es_idempotente_en_el_dia(self):
        hist.instantanea(self.db, fecha=HOY)
        hist.instantanea(self.db, fecha=HOY)
        self.assertEqual(len(self.filas()), 1)

    def test_la_ultima_corrida_del_dia_gana(self):
        """A recipe edited at 4pm is what that day records."""
        hist.instantanea(self.db, fecha=HOY)
        svc.agregar_linea(
            self.db, self.plato, tipo_componente="insumo",
            componente_id=self.pechuga.id, cantidad="100", unidad="g",
        )
        hist.instantanea(self.db, fecha=HOY)
        (fila,) = self.filas()
        self.assertEqual(fila.costo_total, Decimal("800.00"))

    def test_una_receta_archivada_no_se_guarda(self):
        svc.set_activo(self.db, self.plato, False)
        self.assertEqual(hist.instantanea(self.db, fecha=HOY), 0)

    def test_una_receta_incompleta_guarda_sin_costo_no_cero(self):
        """D10 all the way into storage: a zero would draw a plate that
        looks free on the chart."""
        sal = insumos_svc.create(self.db, nombre="Sal", unidad_base="g")
        svc.agregar_linea(
            self.db, self.plato, tipo_componente="insumo",
            componente_id=sal.id, cantidad="2", unidad="g",
        )
        hist.instantanea(self.db, fecha=HOY)
        (fila,) = self.filas()
        self.assertIsNone(fila.costo_total)
        self.assertEqual(fila.estado, "incompleto")


class ElPasadoNoSeMueve(HistoricoTestCase):
    def test_editar_hoy_no_reescribe_ayer(self):
        """The whole reason D13 stores instead of recomputing. If a merma
        edited today changed yesterday's point, the chart could not answer
        the one question it exists for."""
        ayer = HOY - dt.timedelta(days=1)
        hist.instantanea(self.db, fecha=ayer)
        (antes,) = self.filas()
        valor_ayer = antes.costo_total

        insumos_svc.update(
            self.db, self.pechuga, nombre="Pechuga", unidad_base="g", merma_pct="50"
        )
        hist.instantanea(self.db, fecha=HOY)

        filas = self.filas()
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0].costo_total, valor_ayer)
        self.assertGreater(filas[1].costo_total, valor_ayer)


class Backfill(HistoricoTestCase):
    def test_siembra_puntos_semanales(self):
        escritas = hist.backfill(self.db, dias=28, paso_dias=7, hasta=HOY)
        self.assertEqual(escritas, 4)
        self.assertEqual([f.fecha for f in self.filas()],
                         [HOY - dt.timedelta(days=d) for d in (28, 21, 14, 7)])

    def test_usa_los_precios_de_cada_dia_no_los_de_hoy(self):
        """The prices behind a seeded point are real and dated: `costing.py`
        already windows on `hoy`. A backfill that shared one cost cache
        across dates would flatten the whole chart to one value."""
        escritas = hist.backfill(self.db, dias=150, paso_dias=140, hasta=HOY)
        self.assertEqual(escritas, 2)
        valores = {f.costo_total for f in self.filas()}
        self.assertEqual(len(valores), 2, f"todos los puntos dieron lo mismo: {valores}")

    def test_no_pisa_un_dia_ya_observado(self):
        """A day that was actually recorded stays recorded -- re-running the
        seed cannot turn real history into estimates."""
        fecha = HOY - dt.timedelta(days=7)
        hist.instantanea(self.db, fecha=fecha)
        (real,) = self.filas()
        real.costo_total = Decimal("1.00")
        self.db.commit()

        hist.backfill(self.db, dias=14, paso_dias=7, hasta=HOY)
        conservada = [f for f in self.filas() if f.fecha == fecha][0]
        self.assertEqual(conservada.costo_total, Decimal("1.00"))

    def test_los_puntos_sembrados_se_marcan_como_estimados(self):
        hist.backfill(self.db, dias=14, paso_dias=7, hasta=HOY)
        self.assertTrue(all(hist._es_estimado(f) for f in self.filas()))

    def test_un_punto_tomado_su_propio_dia_no_es_estimado(self):
        hist.instantanea(self.db, fecha=dt.date.today())
        fila = [f for f in self.filas() if f.fecha == dt.date.today()][0]
        self.assertFalse(hist._es_estimado(fila))


class Tarjeta(HistoricoTestCase):
    def test_la_tarjeta_arma_puntos_stats_y_variacion_de_la_misma_serie(self):
        for dias in (14, 7, 0):
            hist.instantanea(self.db, fecha=HOY - dt.timedelta(days=dias))
        t = hist.tarjeta(self.db, self.plato)
        self.assertEqual(len(t["puntos"]), 3)
        self.assertIsNotNone(t["svg"])
        self.assertIsNotNone(t["stats"])
        self.assertEqual(t["stats"]["max"], max(p.valor for p in t["puntos"]))

    def test_un_plato_grafica_por_porcion(self):
        hist.instantanea(self.db, fecha=HOY)
        t = hist.tarjeta(self.db, self.plato)
        self.assertEqual(t["puntos"][0].valor, Decimal("200.00"))

    def test_una_preparacion_grafica_el_lote_entero(self):
        """Its per-unit figure would need `rendimiento_cantidad`, which the
        history row does not store -- dividing an old cost by today's yield
        is the retroactive rewrite D13 exists to prevent."""
        salsa = svc.create(
            self.db, nombre="Salsa", tipo="preparacion",
            rendimiento_cantidad="500", rendimiento_unidad="g",
        )
        svc.agregar_linea(
            self.db, salsa, tipo_componente="insumo",
            componente_id=self.pechuga.id, cantidad="100", unidad="g",
        )
        hist.instantanea(self.db, fecha=HOY)
        t = hist.tarjeta(self.db, salsa)
        self.assertEqual(t["puntos"][0].valor, Decimal("400.00"))

    def test_un_dia_sin_costo_no_dibuja_un_cero(self):
        sal = insumos_svc.create(self.db, nombre="Sal", unidad_base="g")
        hist.instantanea(self.db, fecha=HOY - dt.timedelta(days=7))
        svc.agregar_linea(
            self.db, self.plato, tipo_componente="insumo",
            componente_id=sal.id, cantidad="2", unidad="g",
        )
        hist.instantanea(self.db, fecha=HOY)
        t = hist.tarjeta(self.db, self.plato)
        self.assertEqual(len(t["puntos"]), 1)
        self.assertEqual(t["sin_costo"], 1)

    def test_sin_filas_la_tarjeta_no_se_cae(self):
        t = hist.tarjeta(self.db, self.plato)
        self.assertEqual(t["puntos"], [])
        self.assertIsNone(t["svg"])
        self.assertIsNone(t["stats"])
        self.assertIsNone(t["variacion"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
