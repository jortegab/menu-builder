"""A real Session for the DB-backed half of the suite, and a fake catalog
client to drive it.

Same rationale as `restaurant-manager`'s own `tests/dbcase.py`: "tests must
not need infrastructure" does not mean "only pure functions get tested".
An in-memory SQLite database needs no services, starts in milliseconds, and
keeps `python -m unittest discover -s tests` runnable anywhere -- which is
what that rule was protecting. The modules that *write* are exactly where
the risk is.

**Two SQLite caveats that matter here**, both inherited:

- No native decimal type: SQLAlchemy round-trips `Numeric` through float,
  so keep fixtures on round numbers and compare money by `Decimal` value
  rather than by its rendered scale.
- Dialect-specific DDL is ignored. `InsumoFuente`'s partial unique index is
  declared `postgresql_where=status <> 'rejected'`, so on SQLite it becomes
  an *unconditional* unique index on `catalog_product_id`. A test that
  needs a rejected mapping alongside a live one for the same producto will
  not behave like production -- that belongs in a Postgres test, not here.

Subclass `DbTestCase` and use `self.db`. Each test gets its own empty
database; nothing leaks between them.
"""

from __future__ import annotations

import logging
import sys
import unittest
from unittest import mock
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models import Base  # noqa: E402
from app.services.catalog_client import (  # noqa: E402
    SUPPORTED_MAJOR,
    CatalogUnavailable,
)


class DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        # **No test may reach the Anthropic API, and none may depend on
        # whether a key happens to be configured.**
        #
        # Found the hard way, 2026-09-06: the moment a real key landed in
        # `.env`, four tests started failing and the whole suite started
        # making billed network calls -- `mapeo.sugerir` calls Claude, and
        # `test_routes_smoke` renders the mapping queue. A unit test that
        # costs money and answers differently depending on the environment
        # is not a unit test.
        #
        # Stubbed here rather than per-module because "the suite is
        # hermetic" is a property of the whole suite. A test that genuinely
        # wants the LLM shape re-patches `sugerir` with its own fake.
        from app.services import llm_insumo

        for nombre, valor in (("sugerir", None), ("disponible", False)):
            parche = mock.patch.object(
                llm_insumo, nombre, return_value=valor, autospec=True
            )
            parche.start()
            self.addCleanup(parche.stop)

        # Several tests exercise degraded paths on purpose (upstream down,
        # an unparseable price); muting keeps a passing run readable, and
        # `assertLogs` still works where a test asserts on a warning.
        logging.getLogger("app").setLevel(logging.ERROR)
        # TestClient logs one INFO line per request (tests/test_routes_smoke.py
        # makes dozens), which buries a real failure in traffic.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )

        @event.listens_for(self.engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        # Mirrors db.py exactly -- autoflush=False in particular, since
        # catalog_sync's explicit flush before writing price points exists
        # precisely because of it.
        self.SessionLocal = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, class_=Session
        )
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def override_get_db(self, request: Request) -> Session:
        """A `dependency_overrides[get_db]` that matches the real
        dependency's whole contract, not just its return value.

        `app.db.get_db` publishes its Session on `request.state.db` so
        template code can reach the same one -- base.html's rail does,
        through `templating.pendientes_mapeo`. An override that only
        returned `self.db` would leave that global with no session and
        silently exercise its fallback, so a page-rendering test would
        cover a path production never takes.

        The `Request` annotation is load-bearing: FastAPI reads the
        *override's* own signature, so an unannotated `request` parameter
        is treated as a required query parameter and every request 422s.
        Ported verbatim from restaurant-manager's own harness.
        """
        request.state.db = self.db
        return self.db


class FakeCatalogClient:
    """An in-memory stand-in for `CatalogClient`.

    Counts its own calls so a test can assert on *how* the sync talked to
    upstream, not just on what landed -- "did the incremental pull actually
    skip the unchanged products" is a question about the calls.
    """

    def __init__(
        self,
        productos: list[dict] | None = None,
        compras: dict[int, list[dict]] | None = None,
        *,
        # Tracks the contract this app declares rather than pinning a
        # literal: hardcoding "1.0" meant every sync test refused the
        # moment SUPPORTED_MAJOR moved to 2, which is a fixture drifting
        # from the code, not a real failure. A test that wants a
        # *mismatch* passes one explicitly.
        api: str = f"{SUPPORTED_MAJOR}.0",
        fail_on: str | None = None,
        categorias: list[dict] | None = None,
    ) -> None:
        self._productos = productos or []
        self._compras = compras or {}
        self._api = api
        self._fail_on = fail_on  # "version" | "productos" | "compras"
        self.calls: dict[str, int] = {
            "version": 0, "productos": 0, "compras": 0, "categorias": 0,
        }
        self.productos_params: list[dict] = []
        # Upstream's category names, in upstream's own shape -- the parent
        # is an `id` reference, NOT a slug, and the family row travels with
        # its children so that reference resolves. This fake said
        # `parent_slug` until 2026-09-07 and upstream has never sent that
        # key: the mirror's `parent_slug` column was NULL on all 36 live
        # rows while the suite happily asserted otherwise. A fake that
        # invents a friendlier payload than the real one is worse than no
        # fake at all, which is the whole reason the client and the sync
        # are separate modules.
        #
        # Defaults to the family the fixtures use plus its two leaves, so a
        # test that does not care about labels needs no setup.
        self.categorias_items: list[dict] = categorias or [
            {"id": 1, "slug": "alimentos", "nombre": "Alimentos", "parent_id": None},
            {"id": 2, "slug": "alimentos_verduras", "nombre": "Verduras", "parent_id": 1},
            {"id": 3, "slug": "alimentos_carnes", "nombre": "Carnes", "parent_id": 1},
        ]

    def _maybe_fail(self, what: str) -> None:
        if self._fail_on == what:
            raise CatalogUnavailable(f"fingido: {what} no disponible")

    def categorias(self) -> dict:
        self.calls["categorias"] += 1
        self._maybe_fail("categorias")
        return {"items": list(self.categorias_items)}

    def version(self) -> dict:
        self.calls["version"] += 1
        self._maybe_fail("version")
        from app.services.catalog_client import SUPPORTED_MAJOR, ContractMismatch

        if self._api.split(".")[0] != str(SUPPORTED_MAJOR):
            raise ContractMismatch(f"versión {self._api}")
        return {"app": "restaurant-manager", "api": self._api}

    def productos(self, desde=0, limite=200, modificados_desde=None) -> dict:
        self.calls["productos"] += 1
        self.productos_params.append(
            {"desde": desde, "limite": limite, "modificados_desde": modificados_desde}
        )
        self._maybe_fail("productos")
        rows = self._productos
        if modificados_desde is not None:
            rows = [
                r for r in rows
                if r.get("updated_at") and r["updated_at"] >= modificados_desde.isoformat()
            ]
        rows = [r for r in rows if r["id"] > desde]
        page = rows[:limite]
        return {
            "items": page,
            "next_cursor": page[-1]["id"] if len(page) == limite and page else None,
        }

    def compras(self, product_id: int, cursor: int = 0, limite: int = 200) -> dict:
        self.calls["compras"] += 1
        self._maybe_fail("compras")
        rows = self._compras.get(product_id, [])
        return {"items": rows, "next_cursor": None}


def producto(
    pid: int = 1,
    *,
    internal_code: str | None = None,
    description: str = "PEREJIL",
    updated_at: str = "2026-09-01T00:00:00+00:00",
    **kw,
) -> dict:
    """One `/productos` item, with only the fields a caller asserts on
    spelled out."""
    payload = {
        "id": pid,
        "internal_code": internal_code or f"11-{pid:04d}",
        "supplier_id": kw.pop("supplier_id", 11),
        "supplier_nombre": kw.pop("supplier_nombre", "Inversiones Hevar"),
        "family_supplier_id": kw.pop("family_supplier_id", 11),
        "description": description,
        "cabys": kw.pop("cabys", "0121999010600"),
        "unidad_medida": kw.pop("unidad_medida", "Unid"),
        "unidad_comercial_base": kw.pop("unidad_comercial_base", None),
        "categoria_slug": kw.pop("categoria_slug", "alimentos_verduras"),
        "ignored": kw.pop("ignored", False),
        "last_purchase_at": kw.pop("last_purchase_at", "2026-08-27"),
        "updated_at": updated_at,
    }
    payload.update(kw)
    return payload


def compra(
    clave: str = "506" + "1" * 47,
    numero_linea: int = 1,
    *,
    fecha: str = "2026-08-01",
    precio: str = "1000.0000",
    crc: str | None = "1000.0000",
    **kw,
) -> dict:
    """One `/compras` item. Money arrives as strings, exactly as upstream
    sends it -- that is the contract this app parses with `Decimal`. An
    electronic line, as v3 serves it: its `origen` and no factura.
    """
    payload = {
        "origen": "electronica",
        "clave": clave,
        "factura_fisica_id": None,
        "numero_linea": numero_linea,
        "fecha": fecha,
        "cantidad": kw.pop("cantidad", "3.000"),
        "unidad_comercial": kw.pop("unidad_comercial", None),
        "moneda": kw.pop("moneda", "CRC"),
        "precio_efectivo": precio,
        "precio_efectivo_crc": crc,
        "unit_ratio": kw.pop("unit_ratio", "1"),
        "precio_efectivo_normalizado_crc": kw.pop("normalizado", crc),
        "iva_pct": kw.pop("iva_pct", "1.00"),
        "descuento_pct": kw.pop("descuento_pct", None),
    }
    payload.update(kw)
    return payload


def compra_fisica(factura_fisica_id: int = 88, numero_linea: int = 1, **kw) -> dict:
    """A line typed on a factura física, as v3 serves it (upstream's D204):
    no clave, keyed on its factura."""
    return compra(
        numero_linea=numero_linea, origen="fisica", clave=None,
        factura_fisica_id=factura_fisica_id, **kw,
    )
