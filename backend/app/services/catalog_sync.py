"""Pull `restaurant-manager`'s catalog into the local mirror.

The mirror (`catalog_product`, `catalog_price_point`) is a **projection**:
derived, disposable, rebuildable by re-syncing, and never hand-edited
(non-negotiable #3). Nothing in this app may hold data that exists only
here, which is what makes a full rebuild safe -- `DELETE` and re-pull is
always a valid repair.

**Idempotent by construction, not by care.** Products are keyed on
`internal_code` and price points on `(clave, numero_linea)` -- upstream's
own natural keys -- so running the same sync twice writes the same rows
twice and changes nothing the second time. No "have I seen this?" bookkeeping
and no dependence on the watermark being exactly right.

**A failed sync is not an error state for this app.** It records the failed
run and returns; the mirror simply stops advancing, costing keeps working
against what is already there, and every cost figure carries the mirror's
as-of date so stale reads as stale (PLAN.md §5.3). This function does not
raise for anything upstream does -- only a programming error escapes it.

**Money is parsed with `Decimal`, never `float`.** Upstream serializes every
monetary field as a decimal *string* precisely so it can cross the wire
exactly (its own D29); calling `float()` on the way in would throw that away
at the last possible moment and reintroduce the error both apps' schemas
exist to prevent.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CatalogPricePoint, CatalogProduct, CatalogSyncRun
from .catalog_client import CatalogClient, CatalogUnavailable, ContractMismatch

log = logging.getLogger(__name__)

_PAGE = 200


def _dec(value: object) -> Decimal | None:
    """A money/quantity field from the payload as a Decimal, or None.

    `None` in, `None` out -- and that is meaningful, not merely tolerated:
    a null `precio_efectivo_crc` is upstream saying "this USD line had no
    exchange rate", which drops the observation from the price series
    rather than defaulting it to zero (non-negotiable #7).

    A value that will not parse is logged and treated as missing rather
    than crashing the whole sync over one bad row.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        log.warning("Valor decimal ilegible en el catálogo: %r", value)
        return None


def _date(value: object) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        log.warning("Fecha ilegible en el catálogo: %r", value)
        return None


def _datetime(value: object) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value))
    except ValueError:
        log.warning("Timestamp ilegible en el catálogo: %r", value)
        return None


def last_watermark(db: Session) -> dt.datetime | None:
    """Where the next incremental pull should start: the newest
    `upstream_updated_at` already mirrored.

    Derived from the mirror itself rather than from the previous run's clock
    on purpose. A wall-clock watermark assumes the two machines agree about
    "now" and that nothing was written upstream mid-sync; this asks the only
    question that actually matters -- how new is the newest thing I hold --
    and re-fetching a boundary row costs nothing because the upsert is
    idempotent.
    """
    return db.scalar(select(func.max(CatalogProduct.upstream_updated_at)))


def mirror_as_of(db: Session) -> dt.datetime | None:
    """When the mirror last successfully advanced. Every cost figure is
    stamped with this so an old number reads as old.
    """
    return db.scalar(
        select(func.max(CatalogSyncRun.finished_at)).where(CatalogSyncRun.status == "ok")
    )


def _upsert_product(db: Session, payload: dict) -> tuple[CatalogProduct, bool]:
    """Insert or update one mirrored product, keyed on `internal_code`.

    Keyed on the code rather than on upstream's row id because it is the
    stable, human-meaningful identity ("{supplier_id}-{supplier_code}") --
    a mapping this app has confirmed survives anything that could renumber
    rows upstream.
    """
    code = payload["internal_code"]
    product = db.scalar(select(CatalogProduct).where(CatalogProduct.internal_code == code))
    created = product is None
    if product is None:
        product = CatalogProduct(internal_code=code)
        db.add(product)

    product.upstream_product_id = payload["id"]
    product.supplier_id = payload["supplier_id"]
    product.supplier_nombre = payload.get("supplier_nombre") or ""
    product.family_supplier_id = payload.get("family_supplier_id")
    product.description = payload.get("description") or ""
    product.cabys = payload.get("cabys")
    product.unidad_medida = payload.get("unidad_medida")
    product.unidad_comercial_base = payload.get("unidad_comercial_base")
    product.categoria_slug = payload.get("categoria_slug")
    product.ignored = bool(payload.get("ignored"))
    product.last_purchase_at = _date(payload.get("last_purchase_at"))
    product.upstream_updated_at = _datetime(payload.get("updated_at"))
    product.synced_at = dt.datetime.now(dt.UTC)
    return product, created


def _sync_prices(db: Session, client, product: CatalogProduct) -> int:
    """Every price point upstream has for this product that the mirror does
    not, inserted. Returns how many were new.

    Existing rows are left untouched rather than updated: a
    `(clave, numero_linea)` identifies one line of one issued invoice, and
    an issued invoice does not change. If upstream ever *restates* a line,
    that is a rebuild (`DELETE` + re-pull), not an update path to maintain
    here.
    """
    known = {
        (clave, linea)
        for clave, linea in db.execute(
            select(CatalogPricePoint.clave, CatalogPricePoint.numero_linea).where(
                CatalogPricePoint.catalog_product_id == product.id
            )
        )
    }
    inserted = 0
    cursor = 0
    while True:
        page = client.compras(product.upstream_product_id, cursor=cursor, limite=_PAGE)
        for row in page.get("items", []):
            key = (row["clave"], row["numero_linea"])
            if key in known:
                continue
            known.add(key)
            db.add(
                CatalogPricePoint(
                    catalog_product_id=product.id,
                    clave=row["clave"],
                    numero_linea=row["numero_linea"],
                    fecha=_date(row.get("fecha")),
                    cantidad=_dec(row.get("cantidad")) or Decimal(0),
                    unidad_comercial=row.get("unidad_comercial"),
                    moneda=row.get("moneda"),
                    precio_efectivo=_dec(row.get("precio_efectivo")) or Decimal(0),
                    precio_efectivo_crc=_dec(row.get("precio_efectivo_crc")),
                    unit_ratio=_dec(row.get("unit_ratio")),
                    precio_efectivo_normalizado_crc=_dec(
                        row.get("precio_efectivo_normalizado_crc")
                    ),
                    iva_pct=_dec(row.get("iva_pct")),
                    descuento_pct=_dec(row.get("descuento_pct")),
                )
            )
            inserted += 1
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return inserted


def sync_catalog(db: Session, client, *, full: bool = False) -> CatalogSyncRun:
    """Pull the catalog and its price history into the mirror.

    `full=True` ignores the watermark and re-walks everything -- the repair
    path, and cheap enough to reach for: the upserts are idempotent, so a
    full re-sync is only slower, never destructive.

    Always returns a `CatalogSyncRun`, committed, whose `status` is `ok` or
    `error`. It never raises for anything upstream did.
    """
    watermark = None if full else last_watermark(db)
    run = CatalogSyncRun(status="running", watermark=watermark)
    db.add(run)
    db.commit()

    productos = precios = 0
    try:
        # Before a single row is written: refuse a contract whose meaning
        # this app was not written against, rather than parsing hopefully.
        client.version()

        cursor = 0
        while True:
            page = client.productos(
                desde=cursor, limite=_PAGE, modificados_desde=watermark
            )
            items = page.get("items", [])
            for payload in items:
                product, _created = _upsert_product(db, payload)
                # Flushed so a brand-new product has an id for its price
                # points to hang off -- the session is autoflush=False,
                # mirroring upstream's, so this has to be explicit.
                db.flush()
                precios += _sync_prices(db, client, product)
                productos += 1
            db.commit()
            cursor = page.get("next_cursor")
            if not cursor:
                break

        run.status = "ok"
    except (CatalogUnavailable, ContractMismatch) as exc:
        # The mirror keeps whatever it already had. Costing carries on
        # against slightly older data and says so; this is a stalled sync,
        # not a broken app.
        db.rollback()
        run = db.get(CatalogSyncRun, run.id)
        run.status = "error"
        run.error = str(exc)
        log.warning("Sincronización del catálogo fallida: %s", exc)
    finally:
        run.productos_upserted = productos
        run.precios_inserted = precios
        run.finished_at = dt.datetime.now(dt.UTC)
        db.commit()

    return run
