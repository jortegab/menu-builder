"""`services/catalog_sync.py` -- pulling upstream's catalog into the mirror.

Weighted toward the quiet failures, since the mirror is what every cost is
ultimately computed from: a price parsed as a float, a null that should
have stayed null, a re-sync that duplicates rows, or a failed sync that
takes the app down with it instead of just aging.
"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbcase import DbTestCase, FakeCatalogClient, compra, producto  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models import (  # noqa: E402
    CatalogCategoria,
    CatalogPricePoint,
    CatalogProduct,
    CatalogSyncRun,
)
from app.services import catalog_sync  # noqa: E402
from app.services.catalog_client import SUPPORTED_MAJOR  # noqa: E402


class TestBasicSync(DbTestCase):
    def test_mirrors_a_product_and_its_prices(self):
        client = FakeCatalogClient(
            [producto(1, description="PEREJIL")],
            {1: [compra(numero_linea=1), compra(numero_linea=2)]},
        )
        run = catalog_sync.sync_catalog(self.db, client)

        self.assertEqual(run.status, "ok")
        self.assertEqual(run.productos_upserted, 1)
        self.assertEqual(run.precios_inserted, 2)

        p = self.db.scalar(select_one(CatalogProduct))
        self.assertEqual(p.internal_code, "11-0001")
        self.assertEqual(p.description, "PEREJIL")
        self.assertEqual(p.categoria_slug, "alimentos_verduras")
        self.assertEqual(p.supplier_nombre, "Inversiones Hevar")
        self.assertEqual(p.last_purchase_at, dt.date(2026, 8, 27))

    def test_money_is_parsed_as_decimal_not_float(self):
        # Upstream sends money as a decimal string precisely so it can cross
        # the wire exactly; float() here would undo that at the last step.
        client = FakeCatalogClient(
            [producto(1)],
            {1: [compra(precio="1039.6040", crc="1039.6040", normalizado="1039.60")]},
        )
        catalog_sync.sync_catalog(self.db, client)

        pp = self.db.scalar(select_one(CatalogPricePoint))
        self.assertEqual(pp.precio_efectivo, Decimal("1039.6040"))
        self.assertEqual(pp.precio_efectivo_crc, Decimal("1039.6040"))
        self.assertEqual(pp.precio_efectivo_normalizado_crc, Decimal("1039.60"))

    def test_a_null_crc_price_stays_null(self):
        # A USD line whose date had no exchange rate. Never 0 -- that would
        # make the ingredient look free (non-negotiable #7).
        client = FakeCatalogClient(
            [producto(1)],
            {1: [compra(moneda="USD", precio="2.5000", crc=None, normalizado=None)]},
        )
        catalog_sync.sync_catalog(self.db, client)

        pp = self.db.scalar(select_one(CatalogPricePoint))
        self.assertIsNone(pp.precio_efectivo_crc)
        self.assertIsNone(pp.precio_efectivo_normalizado_crc)
        self.assertEqual(pp.precio_efectivo, Decimal("2.5"))

    def test_ignored_products_are_mirrored_with_the_flag(self):
        client = FakeCatalogClient([producto(1, ignored=True)], {1: []})
        catalog_sync.sync_catalog(self.db, client)
        self.assertTrue(self.db.scalar(select_one(CatalogProduct)).ignored)

    def test_an_unparseable_price_degrades_instead_of_failing_the_sync(self):
        client = FakeCatalogClient(
            [producto(1)], {1: [compra(crc="no-es-un-numero")]}
        )
        run = catalog_sync.sync_catalog(self.db, client)
        self.assertEqual(run.status, "ok")
        self.assertIsNone(self.db.scalar(select_one(CatalogPricePoint)).precio_efectivo_crc)


class TestIdempotence(DbTestCase):
    def test_running_twice_writes_nothing_the_second_time(self):
        client = FakeCatalogClient(
            [producto(1)], {1: [compra(numero_linea=1), compra(numero_linea=2)]}
        )
        catalog_sync.sync_catalog(self.db, client)
        second = catalog_sync.sync_catalog(self.db, client, full=True)

        self.assertEqual(second.precios_inserted, 0)
        self.assertEqual(count(self.db, CatalogProduct), 1)
        self.assertEqual(count(self.db, CatalogPricePoint), 2)

    def test_a_product_is_updated_in_place_not_duplicated(self):
        client = FakeCatalogClient([producto(1, description="PEREJIL")], {1: []})
        catalog_sync.sync_catalog(self.db, client)

        renamed = FakeCatalogClient(
            [producto(1, description="PEREJIL FRESCO", categoria_slug="alimentos_frutas")],
            {1: []},
        )
        catalog_sync.sync_catalog(self.db, renamed, full=True)

        self.assertEqual(count(self.db, CatalogProduct), 1)
        p = self.db.scalar(select_one(CatalogProduct))
        self.assertEqual(p.description, "PEREJIL FRESCO")
        self.assertEqual(p.categoria_slug, "alimentos_frutas")

    def test_the_join_key_is_internal_code_not_upstream_id(self):
        # A mapping this app has confirmed must survive anything that could
        # renumber rows upstream.
        catalog_sync.sync_catalog(
            self.db, FakeCatalogClient([producto(1, internal_code="11-0122")], {1: []})
        )
        catalog_sync.sync_catalog(
            self.db,
            FakeCatalogClient([producto(999, internal_code="11-0122")], {999: []}),
            full=True,
        )
        self.assertEqual(count(self.db, CatalogProduct), 1)
        self.assertEqual(self.db.scalar(select_one(CatalogProduct)).upstream_product_id, 999)

    def test_new_prices_are_added_to_an_existing_product(self):
        first = FakeCatalogClient([producto(1)], {1: [compra(numero_linea=1)]})
        catalog_sync.sync_catalog(self.db, first)

        later = FakeCatalogClient(
            [producto(1)], {1: [compra(numero_linea=1), compra(numero_linea=2)]}
        )
        run = catalog_sync.sync_catalog(self.db, later, full=True)

        self.assertEqual(run.precios_inserted, 1)
        self.assertEqual(count(self.db, CatalogPricePoint), 2)


class TestWatermark(DbTestCase):
    def test_first_sync_asks_for_everything(self):
        client = FakeCatalogClient([producto(1)], {1: []})
        catalog_sync.sync_catalog(self.db, client)
        self.assertIsNone(client.productos_params[0]["modificados_desde"])

    def test_second_sync_asks_only_for_what_changed(self):
        client = FakeCatalogClient(
            [producto(1, updated_at="2026-09-01T00:00:00+00:00")], {1: []}
        )
        catalog_sync.sync_catalog(self.db, client)

        again = FakeCatalogClient(
            [
                producto(1, updated_at="2026-09-01T00:00:00+00:00"),
                producto(2, updated_at="2026-09-02T00:00:00+00:00"),
            ],
            {1: [], 2: []},
        )
        run = catalog_sync.sync_catalog(self.db, again)

        self.assertIsNotNone(again.productos_params[0]["modificados_desde"])
        # Product 1 is at the boundary and gets re-fetched, which is
        # harmless (the upsert is idempotent); product 2 is genuinely new.
        self.assertEqual(count(self.db, CatalogProduct), 2)
        self.assertGreaterEqual(run.productos_upserted, 1)

    def test_full_ignores_the_watermark(self):
        client = FakeCatalogClient([producto(1)], {1: []})
        catalog_sync.sync_catalog(self.db, client)
        again = FakeCatalogClient([producto(1)], {1: []})
        catalog_sync.sync_catalog(self.db, again, full=True)
        self.assertIsNone(again.productos_params[0]["modificados_desde"])

    def test_watermark_is_the_newest_mirrored_timestamp(self):
        client = FakeCatalogClient(
            [
                producto(1, updated_at="2026-08-01T00:00:00+00:00"),
                producto(2, updated_at="2026-09-01T00:00:00+00:00"),
            ],
            {1: [], 2: []},
        )
        catalog_sync.sync_catalog(self.db, client)
        wm = catalog_sync.last_watermark(self.db)
        self.assertEqual(wm.replace(tzinfo=None), dt.datetime(2026, 9, 1))


class TestPagination(DbTestCase):
    def test_follows_next_cursor_across_pages(self):
        productos = [producto(i, internal_code=f"11-{i:04d}") for i in range(1, 6)]
        client = FakeCatalogClient(productos, {i: [] for i in range(1, 6)})
        catalog_sync._PAGE, original = 2, catalog_sync._PAGE
        try:
            run = catalog_sync.sync_catalog(self.db, client)
        finally:
            catalog_sync._PAGE = original

        self.assertEqual(run.status, "ok")
        self.assertEqual(count(self.db, CatalogProduct), 5)
        self.assertEqual(run.productos_upserted, 5)


class TestCategorias(DbTestCase):
    """The category tree, whose parent link crosses the two apps' different
    keys: upstream numbers its rows, this mirror keys on the slug."""

    def test_the_parent_is_resolved_from_upstreams_id_to_a_slug(self):
        catalog_sync.sync_catalog(self.db, FakeCatalogClient([producto(1)], {1: []}))
        por_slug = {c.slug: c for c in self.db.scalars(select_all(CatalogCategoria))}
        self.assertEqual(por_slug["alimentos_verduras"].parent_slug, "alimentos")
        self.assertIsNone(por_slug["alimentos"].parent_slug)

    def test_an_unresolvable_parent_lands_as_null_not_as_the_raw_id(self):
        """A leaf whose family is not in the payload. `parent_slug` is a
        slug column and an id written into it would compare equal to
        nothing and read as a real value -- silence is the honest answer.
        """
        client = FakeCatalogClient(
            [producto(1)],
            {1: []},
            categorias=[{"id": 9, "slug": "alimentos_verduras", "parent_id": 1}],
        )
        catalog_sync.sync_catalog(self.db, client)
        self.assertIsNone(self.db.scalar(select_one(CatalogCategoria)).parent_slug)


class TestFailureIsNotAnErrorState(DbTestCase):
    def test_upstream_down_records_the_run_and_leaves_the_mirror_alone(self):
        catalog_sync.sync_catalog(
            self.db, FakeCatalogClient([producto(1)], {1: [compra()]})
        )
        self.assertEqual(count(self.db, CatalogProduct), 1)

        run = catalog_sync.sync_catalog(
            self.db, FakeCatalogClient(fail_on="productos"), full=True
        )
        self.assertEqual(run.status, "error")
        self.assertIn("no disponible", run.error)
        # The mirror still holds what it had -- costing carries on.
        self.assertEqual(count(self.db, CatalogProduct), 1)
        self.assertEqual(count(self.db, CatalogPricePoint), 1)

    def test_sync_never_raises_when_upstream_is_unreachable(self):
        run = catalog_sync.sync_catalog(self.db, FakeCatalogClient(fail_on="version"))
        self.assertEqual(run.status, "error")

    def test_a_wrong_major_version_refuses_before_writing_anything(self):
        client = FakeCatalogClient(
            [producto(1)], {1: [compra()]}, api=f"{SUPPORTED_MAJOR + 1}.0"
        )
        run = catalog_sync.sync_catalog(self.db, client)

        self.assertEqual(run.status, "error")
        self.assertEqual(client.calls["productos"], 0, "must refuse before fetching")
        self.assertEqual(count(self.db, CatalogProduct), 0)

    def test_a_failed_run_does_not_move_the_mirror_as_of_date(self):
        catalog_sync.sync_catalog(self.db, FakeCatalogClient([producto(1)], {1: []}))
        good = catalog_sync.mirror_as_of(self.db)
        catalog_sync.sync_catalog(self.db, FakeCatalogClient(fail_on="version"))
        self.assertEqual(catalog_sync.mirror_as_of(self.db), good)

    def test_every_run_is_recorded_either_way(self):
        catalog_sync.sync_catalog(self.db, FakeCatalogClient([producto(1)], {1: []}))
        catalog_sync.sync_catalog(self.db, FakeCatalogClient(fail_on="version"))
        estados = sorted(r.status for r in self.db.scalars(select_all(CatalogSyncRun)))
        self.assertEqual(estados, ["error", "ok"])


# -- small query helpers, kept out of the tests themselves ----------------

def select_one(model):
    from sqlalchemy import select

    return select(model).limit(1)


def select_all(model):
    from sqlalchemy import select

    return select(model)


def count(db, model) -> int:
    from sqlalchemy import func, select

    return db.scalar(select(func.count()).select_from(model))


if __name__ == "__main__":
    unittest.main()
