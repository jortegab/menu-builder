"""`services/catalog_sync.py` -- pulling upstream's catalog into the mirror.

Weighted toward the quiet failures, since the mirror is what every cost is
ultimately computed from: a price parsed as a float, a null that should
have stayed null, a re-sync that duplicates rows, or a failed sync that
takes the app down with it instead of just aging.

Since upstream's v3 (D53): a line typed on a factura física stored on its
factura beside the electronic ones, and a served line kept as the current
figure -- restated in place, deleted once upstream stops serving it for a
product, following its product when an edit moves it whichever product is
walked first, and never counted as restated for decimals its column does
not keep. And the client: the key in its header, a 401 that says which
setting is missing, v3 spoken.

Those confirmed against mutants, each turning the run red: restated lines
left as they were; lines no longer served kept; figures compared
unrounded; a typed line keyed by its missing clave; a moved line never
looked up; no key sent; still speaking v2.
"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dbcase import DbTestCase, FakeCatalogClient, compra, compra_fisica, producto  # noqa: E402

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


class TestV3Lines(DbTestCase):
    """v3 (D53): a typed line stored on its factura, and a served line as a
    current figure."""

    def _sync(self, compras, productos=None):
        productos = productos or [producto(1)]
        return catalog_sync.sync_catalog(self.db, FakeCatalogClient(productos, compras), full=True)

    def _puntos(self) -> list[CatalogPricePoint]:
        from sqlalchemy import select

        return list(self.db.scalars(select(CatalogPricePoint).order_by(CatalogPricePoint.id)))

    def _dos_productos(self):
        return [producto(1, internal_code="11-0001"), producto(2, internal_code="11-0002")]

    def _codigo_de(self, punto) -> str:
        return self.db.get(CatalogProduct, punto.catalog_product_id).internal_code

    def test_a_typed_line_is_stored_on_its_factura_beside_an_electronic_one(self):
        run = self._sync({1: [compra(numero_linea=1), compra_fisica(88, 1)]})
        self.assertEqual(run.precios_inserted, 2)
        por_origen = {p.origen: p for p in self._puntos()}
        fisica = por_origen["fisica"]
        self.assertEqual(
            (fisica.clave, fisica.factura_fisica_id, fisica.numero_linea), (None, 88, 1)
        )
        self.assertIsNone(por_origen["electronica"].factura_fisica_id)

    def test_lines_of_one_number_on_different_documents_all_stand(self):
        self._sync({1: [compra(numero_linea=1), compra_fisica(88, 1), compra_fisica(89, 1)]})
        self.assertEqual(len(self._puntos()), 3)

    def test_a_restated_line_is_updated_in_place(self):
        self._sync({1: [compra_fisica(88, 1, precio="1000.0000", crc="1000.0000")]})
        [antes] = self._puntos()
        run = self._sync({1: [compra_fisica(88, 1, precio="1100.0000", crc="1100.0000")]})
        [despues] = self._puntos()
        self.assertEqual(despues.id, antes.id)
        self.assertEqual(despues.precio_efectivo_normalizado_crc, Decimal("1100"))
        cuenta = (run.precios_inserted, run.precios_actualizados, run.precios_borrados)
        self.assertEqual(cuenta, (0, 1, 0))

    def test_a_line_no_longer_served_is_deleted(self):
        self._sync({1: [compra(numero_linea=1), compra_fisica(88, 1)]})
        run = self._sync({1: [compra(numero_linea=1)]})
        self.assertEqual([p.origen for p in self._puntos()], ["electronica"])
        self.assertEqual(run.precios_borrados, 1)

    def test_a_line_moved_off_the_product_walked_first_lands_on_the_other(self):
        self._sync({1: [compra_fisica(88, 2)], 2: []}, self._dos_productos())
        run = self._sync({1: [], 2: [compra_fisica(88, 2)]}, self._dos_productos())
        [punto] = self._puntos()
        self.assertEqual(self._codigo_de(punto), "11-0002")
        self.assertEqual((run.precios_borrados, run.precios_inserted), (1, 1))

    def test_a_line_moved_onto_the_product_walked_first_is_taken_in_place(self):
        self._sync({1: [], 2: [compra_fisica(88, 2)]}, self._dos_productos())
        [antes] = self._puntos()
        run = self._sync({1: [compra_fisica(88, 2)], 2: []}, self._dos_productos())
        [despues] = self._puntos()
        self.assertEqual((despues.id, self._codigo_de(despues)), (antes.id, "11-0001"))
        cuenta = (run.precios_actualizados, run.precios_borrados, run.precios_inserted)
        self.assertEqual(cuenta, (1, 0, 0))

    def test_decimals_the_column_does_not_keep_are_no_restatement(self):
        self._sync({1: [compra(numero_linea=1, cantidad="3.4567")]})
        run = self._sync({1: [compra(numero_linea=1, cantidad="3.4567")]})
        self.assertEqual(run.precios_actualizados, 0)
        [punto] = self._puntos()
        self.assertEqual(punto.cantidad, Decimal("3.457"))


class TestClient(unittest.TestCase):
    """The key upstream's `/api` opens to (its D107), and the contract
    spoken."""

    def test_the_key_travels_in_its_header_and_nowhere_when_blank(self):
        from app.services.catalog_client import CatalogClient

        with CatalogClient("http://upstream/api/v3", api_key="rm_abc") as client:
            self.assertEqual(client._client.headers.get("X-Api-Key"), "rm_abc")
        with CatalogClient("http://upstream/api/v3") as client:
            self.assertNotIn("X-Api-Key", client._client.headers)

    def test_a_401_names_the_missing_key(self):
        import httpx
        from app.services.catalog_client import CatalogClient, CatalogUnavailable

        def responde_401(url, params=None):
            return httpx.Response(401, request=httpx.Request("GET", url))

        with CatalogClient("http://upstream/api/v3") as client:
            client._client.get = responde_401
            with self.assertRaises(CatalogUnavailable) as ctx:
                client.version()
        self.assertIn("UPSTREAM_API_KEY", str(ctx.exception))

    def test_the_app_speaks_v3(self):
        from app.config import Settings

        self.assertEqual(SUPPORTED_MAJOR, 3)
        self.assertTrue(Settings.model_fields["upstream_api_url"].default.endswith("/api/v3"))


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
