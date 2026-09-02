"""`services/mapeo.py` -- the mapping queue.

The tests that matter most here are the ones about what the queue *refuses*
to do on its own: nothing reaches `confirmed` without a person, a rejected
suggestion is never re-offered, and an unresolved factor stays None rather
than becoming a 1.
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

from app.models import CatalogPricePoint, CatalogProduct, Insumo, InsumoFuente  # noqa: E402
from app.services import insumos as insumos_svc  # noqa: E402
from app.services import mapeo as svc  # noqa: E402


class MapeoTestCase(DbTestCase):
    def producto(self, description="PECHUGA DESHUESADA MFS 1/5KG", **kw):
        p = CatalogProduct(
            internal_code=kw.pop("internal_code", f"8-{description[:6]}"),
            upstream_product_id=kw.pop("upstream_product_id", 1),
            supplier_id=kw.pop("supplier_id", 8),
            supplier_nombre=kw.pop("supplier_nombre", "Mayca"),
            description=description,
            categoria_slug=kw.pop("categoria_slug", "insumos_pollo"),
            unidad_medida=kw.pop("unidad_medida", "Unid"),
            last_purchase_at=kw.pop("last_purchase_at", dt.date.today()),
            **kw,
        )
        self.db.add(p)
        self.db.commit()
        return p

    def compras(self, producto, n):
        for i in range(n):
            self.db.add(
                CatalogPricePoint(
                    catalog_product_id=producto.id,
                    clave=f"{producto.id:03d}" + "0" * 44 + f"{i:03d}",
                    numero_linea=i,
                    fecha=dt.date.today(),
                    cantidad=Decimal(1),
                    precio_efectivo=Decimal(1000),
                    precio_efectivo_crc=Decimal(1000),
                )
            )
        self.db.commit()


class TestCola(MapeoTestCase):
    def test_lists_unmapped_products(self):
        p = self.producto()
        self.assertEqual([x.id for x, _ in svc.pendientes(self.db)], [p.id])

    def test_a_confirmed_product_leaves_the_queue(self):
        p = self.producto()
        svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga", factor=Decimal(5000))
        self.assertEqual(svc.pendientes(self.db), [])

    def test_ignored_products_never_appear(self):
        self.producto(description="DESCONTINUADO", ignored=True, internal_code="8-ign")
        self.assertEqual(svc.pendientes(self.db), [])

    def test_non_food_categories_are_filtered_by_default(self):
        # 415 products on the live catalog. Every one is a row the operator
        # would have to skip past to reach the food.
        self.producto(description="CLORO", categoria_slug="limpieza", internal_code="8-clo")
        self.assertEqual(svc.pendientes(self.db), [])
        self.assertEqual(len(svc.pendientes(self.db, solo_comida=False)), 1)

    def test_stale_products_are_filtered_by_default(self):
        viejo = dt.date.today() - dt.timedelta(days=svc.DIAS_RECIENTE + 10)
        self.producto(description="ALGO VIEJO", last_purchase_at=viejo, internal_code="8-old")
        self.assertEqual(svc.pendientes(self.db), [])
        self.assertEqual(len(svc.pendientes(self.db, solo_reciente=False)), 1)

    def test_busiest_products_come_first(self):
        # Mapping pays off in purchase-count order; alphabetical buries the
        # ingredient bought 98 times behind one bought once.
        poco = self.producto(description="AAA POCO", internal_code="8-a", upstream_product_id=1)
        mucho = self.producto(description="ZZZ MUCHO", internal_code="8-z", upstream_product_id=2)
        self.compras(poco, 1)
        self.compras(mucho, 5)
        self.assertEqual([p.id for p, _ in svc.pendientes(self.db)], [mucho.id, poco.id])

    def test_search_filters_by_description(self):
        self.producto(description="PECHUGA", internal_code="8-p", upstream_product_id=1)
        self.producto(description="ACEITE", internal_code="8-a", upstream_product_id=2)
        self.assertEqual([p.description for p, _ in svc.pendientes(self.db, q="pech")], ["PECHUGA"])


class TestSugerencia(MapeoTestCase):
    def test_proposes_a_new_insumo_when_nothing_matches(self):
        p = self.producto()
        s = svc.sugerir(self.db, p)
        self.assertIsNone(s.insumo_id)
        self.assertTrue(s.nombre_propuesto)
        self.assertEqual(s.origen, "ninguna")

    def test_matches_an_existing_insumo_by_similarity(self):
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_uso="g")
        s = svc.sugerir(self.db, self.producto())
        self.assertEqual(s.insumo_id, insumo.id)
        self.assertEqual(s.insumo_nombre, "Pechuga deshuesada")
        self.assertEqual(s.origen, "similitud")

    def test_reads_the_factor_from_the_description(self):
        insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_uso="g")
        s = svc.sugerir(self.db, self.producto())
        self.assertEqual(s.factor, Decimal(5000))
        self.assertEqual(s.factor_origen, "descripcion")

    def test_unresolved_factor_stays_none(self):
        # Never a 1: that would silently cost the insumo per package.
        p = self.producto(description="TORTILLA BLANCA", categoria_slug="insumos_abarrotes")
        s = svc.sugerir(self.db, p)
        self.assertIsNone(s.factor)
        self.assertEqual(s.factor_origen, "sin_resolver")

    def test_a_rejected_insumo_is_not_offered_again(self):
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_uso="g")
        p = self.producto()
        self.assertEqual(svc.sugerir(self.db, p).insumo_id, insumo.id)

        svc.rechazar(self.db, catalog_product_id=p.id, insumo_id=insumo.id)
        again = svc.sugerir(self.db, p)
        self.assertIsNone(again.insumo_id)
        self.assertTrue(again.nombre_propuesto)

    def test_archived_insumos_are_not_suggested(self):
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_uso="g")
        insumos_svc.set_activo(self.db, insumo, False)
        self.assertIsNone(svc.sugerir(self.db, self.producto()).insumo_id)

    def test_rejecting_twice_is_harmless(self):
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_uso="g")
        p = self.producto()
        svc.rechazar(self.db, catalog_product_id=p.id, insumo_id=insumo.id)
        svc.rechazar(self.db, catalog_product_id=p.id, insumo_id=insumo.id)
        self.assertIsNone(svc.sugerir(self.db, p).insumo_id)


class TestConfirmar(MapeoTestCase):
    def test_creates_the_insumo_and_the_source(self):
        p = self.producto()
        fuente = svc.confirmar(
            self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga deshuesada",
            unidad_uso="g", factor=Decimal(5000), factor_origen="descripcion",
        )
        insumo = self.db.get(Insumo, fuente.insumo_id)
        self.assertEqual(insumo.nombre, "Pechuga deshuesada")
        self.assertEqual(insumo.slug, "pechuga-deshuesada")
        self.assertEqual(fuente.factor_conversion, Decimal(5000))

    def test_confirmation_is_always_human_and_confirmed(self):
        # Non-negotiable #4: only a person reaches this function, and only
        # `confirmed` rows ever feed a price series.
        p = self.producto()
        fuente = svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga")
        self.assertEqual(fuente.status, "confirmed")
        self.assertEqual(fuente.mapped_by, "human")
        self.assertIsNotNone(fuente.confirmed_at)

    def test_attaches_a_second_product_to_the_same_insumo(self):
        # The whole point: two suppliers, one insumo.
        insumo = insumos_svc.create(self.db, nombre="Pechuga deshuesada", unidad_uso="g")
        a = self.producto(description="FILETE PECHUGA MFS 1/5KG", internal_code="8-a", upstream_product_id=1)
        b = self.producto(description="PECHUGA DESHUESADA", internal_code="11-b", upstream_product_id=2, supplier_id=11)
        svc.confirmar(self.db, catalog_product_id=a.id, insumo_id=insumo.id, factor=Decimal(5000))
        svc.confirmar(self.db, catalog_product_id=b.id, insumo_id=insumo.id, factor=Decimal(1000))
        fuentes = self.db.query(InsumoFuente).filter_by(insumo_id=insumo.id).all()
        self.assertEqual(len(fuentes), 2)
        self.assertEqual({f.factor_conversion for f in fuentes}, {Decimal(5000), Decimal(1000)})

    def test_a_missing_factor_records_sin_resolver(self):
        p = self.producto()
        fuente = svc.confirmar(
            self.db, catalog_product_id=p.id, nombre_nuevo="Algo",
            factor=None, factor_origen="descripcion",
        )
        self.assertIsNone(fuente.factor_conversion)
        self.assertEqual(fuente.factor_origen, "sin_resolver")

    def test_new_insumo_needs_a_name(self):
        p = self.producto()
        with self.assertRaises(ValueError):
            svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="   ")

    def test_confirming_again_updates_rather_than_duplicating(self):
        p = self.producto()
        svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga", factor=Decimal(1000))
        svc.confirmar(self.db, catalog_product_id=p.id, nombre_nuevo="Pechuga 2", factor=Decimal(5000))
        fuentes = self.db.query(InsumoFuente).filter_by(catalog_product_id=p.id).all()
        self.assertEqual(len(fuentes), 1)
        self.assertEqual(fuentes[0].factor_conversion, Decimal(5000))


class TestResumen(MapeoTestCase):
    def test_counts(self):
        a = self.producto(description="A", internal_code="8-a", upstream_product_id=1)
        self.producto(description="B", internal_code="8-b", upstream_product_id=2)
        r = svc.resumen(self.db)
        self.assertEqual((r["total"], r["mapeados"], r["pendientes"]), (2, 0, 2))

        svc.confirmar(self.db, catalog_product_id=a.id, nombre_nuevo="A")
        r = svc.resumen(self.db)
        self.assertEqual((r["mapeados"], r["pendientes"]), (1, 1))

    def test_llm_flag_reflects_configuration(self):
        # No key in the test environment, so the queue advertises string
        # similarity -- and still works.
        self.assertFalse(svc.resumen(self.db)["llm"])


if __name__ == "__main__":
    unittest.main()
