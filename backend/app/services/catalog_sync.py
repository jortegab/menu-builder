"""Pull `restaurant-manager`'s catalog into the local mirror.

The mirror (`catalog_product`, `catalog_price_point`) is a **projection**:
derived, disposable, rebuildable by re-syncing, and never hand-edited
(non-negotiable #3). Nothing in this app may hold data that exists only
here, which is what makes a full rebuild safe -- `DELETE` and re-pull is
always a valid repair.

**Idempotent by construction, not by care.** Products are keyed on
`internal_code` and price points on the line they mirror -- `(clave,
numero_linea)` for an electronic line, `(factura_fisica_id, numero_linea)`
for one typed on a factura física (D53) -- upstream's own natural keys, so
running the same sync twice writes the same rows twice and changes nothing
the second time. No "have I seen this?" bookkeeping and no dependence on
the watermark being exactly right.

**A product's lines are reconciled, not appended** (D53, upstream's v3): a
served line is a current figure there, which an edit of its factura or an
Ajuste de cantidades restates, moves to another product or removes, every
such edit stamping the products it touches. So each product upstream
reports as modified has its compras walked whole and its points made those
lines: new ones inserted, restated or moved ones updated in place, and the
ones no longer served deleted.

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
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CatalogCategoria, CatalogPricePoint, CatalogProduct, CatalogSyncRun
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


@dataclass
class Cambios:
    """What one product's reconcile wrote: lines inserted, lines updated in
    place (restated upstream, or moved here from another product), and
    lines deleted because upstream no longer serves them for it."""

    insertados: int = 0
    actualizados: int = 0
    borrados: int = 0


def _linea(
    origen: str | None, clave: str | None, factura_fisica_id: int | None, numero: int
) -> tuple:
    """The line a point mirrors: a typed one by its factura, an electronic
    one by its clave -- each with its line number (D53)."""
    if origen == "fisica":
        return ("fisica", factura_fisica_id, numero)
    return ("electronica", clave, numero)


def _escala(campo: str) -> int | None:
    return getattr(CatalogPricePoint.__table__.c[campo].type, "scale", None)


def _valores(row: dict) -> dict:
    """A served line's figures, each at the scale its column keeps. Upstream
    may send more decimals than a column holds (a quantity weighed to four
    places, here three); compared unrounded, such a line would read as
    restated on every sync."""
    valores = {
        "fecha": _date(row.get("fecha")),
        "cantidad": _dec(row.get("cantidad")) or Decimal(0),
        "unidad_comercial": row.get("unidad_comercial"),
        "moneda": row.get("moneda"),
        "precio_efectivo": _dec(row.get("precio_efectivo")) or Decimal(0),
        "precio_efectivo_crc": _dec(row.get("precio_efectivo_crc")),
        "unit_ratio": _dec(row.get("unit_ratio")),
        "precio_efectivo_normalizado_crc": _dec(row.get("precio_efectivo_normalizado_crc")),
        "iva_pct": _dec(row.get("iva_pct")),
        "descuento_pct": _dec(row.get("descuento_pct")),
    }
    for campo, valor in valores.items():
        escala = _escala(campo)
        if isinstance(valor, Decimal) and escala is not None:
            valores[campo] = valor.quantize(Decimal(1).scaleb(-escala), rounding=ROUND_HALF_UP)
    return valores


def _en_otro_producto(db: Session, linea: tuple) -> CatalogPricePoint | None:
    """The mirror's point for this line under another product -- one an edit
    upstream moved -- or None."""
    origen, documento, numero = linea
    columna = CatalogPricePoint.factura_fisica_id if origen == "fisica" else CatalogPricePoint.clave
    return db.scalar(
        select(CatalogPricePoint).where(
            columna == documento, CatalogPricePoint.numero_linea == numero
        )
    )


def _sync_prices(db: Session, client, product: CatalogProduct) -> Cambios:
    """This product's price points made the lines upstream serves for it
    (D53): one the mirror lacks inserted; one upstream restated, or moved
    here from another product, updated in place; one upstream no longer
    serves for it deleted. Upstream's compras, walked whole, are the
    product's current lines -- its v3 contract.

    The caller flushes before each product, which is what settles a line
    moved between two products in one run, either order: walked first, the
    product it left deletes it and the other inserts it anew; walked
    second, the product it joined has already taken it, and the one it
    left no longer holds it.
    """
    propios = {
        _linea(p.origen, p.clave, p.factura_fisica_id, p.numero_linea): p
        for p in db.scalars(
            select(CatalogPricePoint).where(CatalogPricePoint.catalog_product_id == product.id)
        )
    }
    vistos: set[tuple] = set()
    cambios = Cambios()
    cursor = 0
    while True:
        page = client.compras(product.upstream_product_id, cursor=cursor, limite=_PAGE)
        for row in page.get("items", []):
            linea = _linea(
                row.get("origen"),
                row.get("clave"),
                row.get("factura_fisica_id"),
                row["numero_linea"],
            )
            if linea in vistos:
                continue
            vistos.add(linea)
            valores = _valores(row)
            punto = propios.get(linea) or _en_otro_producto(db, linea)
            if punto is None:
                origen, documento, numero = linea
                db.add(
                    CatalogPricePoint(
                        catalog_product_id=product.id,
                        origen=origen,
                        clave=documento if origen == "electronica" else None,
                        factura_fisica_id=documento if origen == "fisica" else None,
                        numero_linea=numero,
                        **valores,
                    )
                )
                cambios.insertados += 1
                continue
            if punto.catalog_product_id != product.id or any(
                getattr(punto, campo) != valor for campo, valor in valores.items()
            ):
                punto.catalog_product_id = product.id
                for campo, valor in valores.items():
                    setattr(punto, campo, valor)
                cambios.actualizados += 1
        cursor = page.get("next_cursor")
        if not cursor:
            break
    for linea, punto in propios.items():
        if linea not in vistos:
            db.delete(punto)
            cambios.borrados += 1
    return cambios


def _sync_categorias(db: Session, client) -> int:
    """Mirror upstream's category names. Idempotent, keyed on the slug.

    Rows are never deleted here. A category upstream retires is a category
    some mirrored product may still point at, and rendering that product's
    slug raw because the name was tidied away is exactly the regression
    this table exists to prevent. They cost nothing to keep.

    **The parent arrives as `parent_id` and is resolved to a slug here.**
    Upstream keys its tree on the row id; this mirror keys on the slug
    instead (see `CatalogCategoria`), because the slug is the stable thing
    a product points at and the only thing this app ever matches on. The
    whole tree comes in one unpaginated payload, so the id -> slug map is
    built from that same payload and costs no second request.
    """
    payload = client.categorias()
    items = [item for item in payload.get("items", []) if item.get("slug")]
    slug_por_id = {
        item["id"]: item["slug"] for item in items if item.get("id") is not None
    }
    n = 0
    for item in items:
        slug = item["slug"]
        fila = db.get(CatalogCategoria, slug)
        if fila is None:
            fila = CatalogCategoria(slug=slug)
            db.add(fila)
        fila.nombre = item.get("nombre") or slug
        fila.parent_slug = slug_por_id.get(item.get("parent_id"))
        n += 1
    db.commit()
    return n


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

    productos = 0
    precios = Cambios()
    try:
        # Before a single row is written: refuse a contract whose meaning
        # this app was not written against, rather than parsing hopefully.
        client.version()

        # The names first: they are what the UI shows in place of a slug,
        # they are a few dozen rows, and a product page that renders
        # `alimentos_verduras` because the taxonomy arrived after the
        # products would be a worse first impression than a slow sync.
        _sync_categorias(db, client)

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
                cambios = _sync_prices(db, client, product)
                precios.insertados += cambios.insertados
                precios.actualizados += cambios.actualizados
                precios.borrados += cambios.borrados
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
        run.precios_inserted = precios.insertados
        run.precios_actualizados = precios.actualizados
        run.precios_borrados = precios.borrados
        run.finished_at = dt.datetime.now(dt.UTC)
        db.commit()

    return run
