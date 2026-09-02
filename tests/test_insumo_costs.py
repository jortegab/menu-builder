"""`services/insumo_costs.py` -- turning confirmed sources into a cost.

The arithmetic is `costing.py`'s and tested there. What is tested here is
the *gathering*: which sources count, which observations are dropped, and
whether every exclusion is reported rather than silently applied. An insumo
that costs less than it should because half its sources were skipped is the
failure this module exists to make visible.
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

from app.models import CatalogPricePoint, CatalogProduct, InsumoFuente  # noqa: E402
from app.services import insumo_costs as costs  # noqa: E402
from app.services import insumos as insumos_svc  # noqa: E402

HOY = dt.date(2026, 9, 2)


class CostsTestCase(DbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_uso="g")
        self._n = 0

    def fuente(self, *, factor="1000", status="confirmed", activo=True, precios=(), dias=(0,)):
        """A confirmed source with `precios` (normalized CRC, per base unit)
        observed `dias` days ago.
        """
        self._n += 1
        producto = CatalogProduct(
            internal_code=f"c-{self._n}", upstream_product_id=self._n, supplier_id=self._n,
            supplier_nombre=f"Proveedor {self._n}", description=f"PECHUGA {self._n}",
        )
        self.db.add(producto)
        self.db.flush()
        for i, (precio, dia) in enumerate(zip(precios, dias, strict=False)):
            self.db.add(
                CatalogPricePoint(
                    catalog_product_id=producto.id,
                    clave=f"{self._n:03d}" + "0" * 44 + f"{i:03d}",
                    numero_linea=i,
                    fecha=HOY - dt.timedelta(days=dia),
                    cantidad=Decimal(1),
                    precio_efectivo=Decimal(str(precio)) if precio is not None else Decimal(0),
                    precio_efectivo_crc=Decimal(str(precio)) if precio is not None else None,
                    precio_efectivo_normalizado_crc=(
                        Decimal(str(precio)) if precio is not None else None
                    ),
                )
            )
        self.db.add(
            InsumoFuente(
                insumo_id=self.insumo.id, catalog_product_id=producto.id,
                factor_conversion=Decimal(factor) if factor is not None else None,
                factor_origen="default_categoria" if factor else "sin_resolver",
                status=status, mapped_by="human", activo=activo,
            )
        )
        self.db.commit()
        return producto


class TestGathering(CostsTestCase):
    def test_divides_the_normalized_price_by_the_factor(self):
        # CRC 4000 per kg, factor 1000 g per kg -> CRC 4 per gram.
        self.fuente(precios=("4000",), dias=(1,))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.costo.valor, Decimal(4))

    def test_unions_several_suppliers_into_one_series(self):
        # The whole point of an insumo: what would this cost me today,
        # from whoever I actually buy it from.
        self.fuente(precios=("4000",), dias=(1,))
        self.fuente(precios=("6000",), dias=(2,))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.costo.observaciones_usadas, 2)
        self.assertEqual(exp.costo.valor, Decimal(5))  # median of 4 and 6

    def test_a_source_without_a_factor_is_excluded_and_counted(self):
        # Never treated as 1: that would cost the insumo per package.
        self.fuente(precios=("4000",), dias=(1,))
        self.fuente(factor=None, precios=("9999",), dias=(1,))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.costo.valor, Decimal(4))
        self.assertEqual(exp.fuentes_sin_factor, 1)
        self.assertTrue(exp.hay_advertencias)

    def test_an_unconfirmed_source_never_contributes(self):
        # Non-negotiable #4: a suggestion pending review is not evidence.
        self.fuente(status="suggested", precios=("4000",), dias=(1,))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertIsNone(exp.costo.valor)
        self.assertEqual(exp.fuentes[0].motivo, "sin confirmar")

    def test_a_deactivated_source_never_contributes(self):
        # A supplier you stopped buying from; keeping their old prices would
        # quietly hold the cost down.
        self.fuente(precios=("4000",), dias=(1,))
        self.fuente(activo=False, precios=("1000",), dias=(1,))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.costo.valor, Decimal(4))
        self.assertIn("desactivada", [f.motivo for f in exp.fuentes])

    def test_an_observation_without_a_crc_price_is_dropped_and_counted(self):
        # A USD line whose date had no exchange rate upstream. Substituting
        # the USD figure would understate it by ~500x.
        self.fuente(precios=("4000", None), dias=(1, 2))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.costo.observaciones_usadas, 1)
        self.assertEqual(exp.descartadas_sin_tipo_cambio, 1)

    def test_no_sources_means_no_cost_never_zero(self):
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertIsNone(exp.costo.valor)
        self.assertTrue(exp.sin_precio)
        self.assertEqual(exp.fuentes, [])


class TestConfig(CostsTestCase):
    def test_uses_the_insumos_own_method_and_window(self):
        self.fuente(precios=("2000", "4000", "9000"), dias=(1, 2, 3))
        insumos_svc.update(
            self.db, self.insumo, nombre="Pechuga deshuesada", unidad_uso="g",
            metodo_costeo="max_window", ventana_dias="90",
        )
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.metodo, "max_window")
        self.assertEqual(exp.costo.valor, Decimal(9))

    def test_falls_back_to_the_app_default(self):
        self.fuente(precios=("4000",), dias=(1,))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.metodo, "median_window")
        self.assertEqual(exp.ventana_dias, 90)

    def test_merma_raises_the_cost(self):
        self.fuente(precios=("4000",), dias=(1,))
        insumos_svc.update(
            self.db, self.insumo, nombre="Pechuga deshuesada", unidad_uso="g", merma_pct="20",
        )
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.costo.valor, Decimal(5))  # 4 / 0.8
        self.assertEqual(exp.merma_pct, Decimal(20))

    def test_observations_outside_the_window_are_marked_not_hidden(self):
        # They still appear in the panel -- "why is this only using two
        # purchases?" is exactly the question the table has to answer.
        self.fuente(precios=("4000", "8000"), dias=(1, 200))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(len(exp.observaciones), 2)
        self.assertEqual([o.en_ventana for o in exp.observaciones], [True, False])
        self.assertEqual(exp.costo.observaciones_usadas, 1)

    def test_an_empty_window_falls_back_and_flags_it(self):
        self.fuente(precios=("4000",), dias=(200,))
        exp = costs.explicar(self.db, self.insumo, hoy=HOY)
        self.assertEqual(exp.costo.valor, Decimal(4))
        self.assertTrue(exp.costo.precio_viejo)
        self.assertTrue(exp.hay_advertencias)


class TestExplicacion(CostsTestCase):
    def test_every_observation_traces_back_to_its_invoice(self):
        producto = self.fuente(precios=("4000",), dias=(1,))
        obs = costs.explicar(self.db, self.insumo, hoy=HOY).observaciones[0]
        self.assertEqual(obs.proveedor, producto.supplier_nombre)
        self.assertEqual(obs.descripcion, producto.description)
        self.assertTrue(obs.clave)
        self.assertEqual(obs.precio_normalizado, Decimal(4000))
        self.assertEqual(obs.factor, Decimal(1000))
        self.assertEqual(obs.precio_por_unidad_uso, Decimal(4))

    def test_observations_are_newest_first(self):
        self.fuente(precios=("1000", "2000", "3000"), dias=(30, 1, 15))
        fechas = [o.fecha for o in costs.explicar(self.db, self.insumo, hoy=HOY).observaciones]
        self.assertEqual(fechas, sorted(fechas, reverse=True))

    def test_costos_batches_a_list(self):
        self.fuente(precios=("4000",), dias=(1,))
        otro = insumos_svc.create(self.db, nombre="Sal", unidad_uso="g")
        result = costs.costos(self.db, [self.insumo, otro], hoy=HOY)
        self.assertEqual(result[self.insumo.id].valor, Decimal(4))
        self.assertIsNone(result[otro.id].valor)


if __name__ == "__main__":
    unittest.main()
