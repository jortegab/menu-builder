"""Jinja environment and the money/quantity filters.

`money` and `camount` mirror restaurant-manager's own filters so an amount
reads identically in both apps. `static_url` is mtime-versioned for the
same reason it is upstream: a changed stylesheet must not be served from
cache.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates
from sqlalchemy import select

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def money(value: Decimal | float | None, dash: str = "—") -> str:
    """A CRC amount, or `dash` for None.

    None renders as an em dash, never as ₡0 -- PLAN.md D10: a recipe with no
    usable cost shows nothing at all, because a number on screen is a number
    someone will use.
    """
    if value is None:
        return dash
    q = Decimal(value).quantize(Decimal("0.01"))
    entero, _, dec = f"{q:,.2f}".partition(".")
    return f"₡{entero.replace(',', ' ')}.{dec}"


def cantidad(value: Decimal | float | None) -> str:
    """A recipe quantity: up to 3 decimals, trailing zeros trimmed, so 180 g
    reads as "180" and 0.5 kg as "0.5" rather than "180.000"/"0.500".
    """
    if value is None:
        return "—"
    q = Decimal(value).quantize(Decimal("0.001")).normalize()
    return format(q, "f")


def pct(value: Decimal | float | None) -> str:
    if value is None:
        return "—"
    return f"{Decimal(value).quantize(Decimal('0.1')).normalize():f}%"


def static_url(path: str) -> str:
    f = _HERE / "static" / path
    version = int(f.stat().st_mtime) if f.exists() else 0
    return f"/static/{path}?v={version}"


# Spanish labels for the English enum values (D3). They live here, in the
# presentation layer, and never in a service -- a service that knows how a
# value is spelled on screen is a service that has to change when the
# wording does.
METODO_LABELS = {
    "last": "Último precio",
    "avg_window": "Promedio",
    "weighted_avg_window": "Promedio ponderado",
    "median_window": "Mediana",
    "max_window": "Máximo",
    "min_window": "Mínimo",
}

METODO_AYUDA = {
    "last": "Lo que costaría cocinarlo hoy. El más intuitivo y el más brincón.",
    "avg_window": "Promedio simple de la ventana.",
    "weighted_avg_window": "Promedio pesado por cantidad: lo que de verdad se pagó en promedio.",
    "median_window": "El precio típico. No se deja llevar por una factura rara.",
    "max_window": "El peor caso reciente. Para costear conservador.",
    "min_window": "El mejor caso reciente. Casi nunca es lo que conviene.",
}

# The base units an insumo may be *costed* in -- the canonical unit of each
# dimension, and nothing else. A cost has to be per something, and per-gram
# is the form that never needs a fraction.
UNIDAD_LABELS = {
    "g": "Gramos (g)",
    "ml": "Mililitros (ml)",
    "unidad": "Unidades",
}

# Every unit a *recipe line* may ask for. Deliberately a longer list than
# UNIDAD_LABELS: the unit belongs to the line, not to the insumo, so an
# aceite costed per ml can be called for in litres or fluid ounces and an
# insumo costed per gram in kilos, pounds or ounces. `units.compatible_units`
# decides which of these a given line may offer; this only names them.
UNIDAD_NOMBRES = {
    "mg": "mg",
    "g": "g",
    "kg": "kg",
    "lb": "lb",
    "ml": "ml",
    "l": "L",
    "oz": "oz",
    "gal": "galones",
    "unidad": "unidades",
}

templates.env.filters["money"] = money
templates.env.filters["cantidad"] = cantidad
templates.env.filters["pct"] = pct
templates.env.globals["static_url"] = static_url
templates.env.globals["METODO_LABELS"] = METODO_LABELS
templates.env.globals["METODO_AYUDA"] = METODO_AYUDA
templates.env.globals["UNIDAD_LABELS"] = UNIDAD_LABELS
templates.env.globals["UNIDAD_NOMBRES"] = UNIDAD_NOMBRES


def pendientes_mapeo(request) -> int:
    """The mapping queue's badge count, read from the Session `get_db`
    published on `request.state.db` -- template code never opens its own.

    Degrades to 0 rather than raising: the nav renders on every page,
    including error pages and any route that never touched the database,
    and a rail that can 500 is a rail that takes the whole app down with
    it.
    """
    db = getattr(getattr(request, "state", None), "db", None)
    if db is None:
        return 0
    try:
        from .services.mapeo import contar_pendientes

        return contar_pendientes(db)
    except Exception:  # pragma: no cover - the rail must never break a page
        return 0


templates.env.globals["pendientes_mapeo"] = pendientes_mapeo


def categoria_nombre(request, slug: str | None) -> str:
    """Upstream's display name for a category slug -- «Verduras», not
    `alimentos_verduras`.

    The whole map is loaded once and cached on `request.state`, because the
    callers are lists: a per-slug query would be one round trip per row on
    `/insumos`, for a table of a few dozen names that never changes within a
    request.

    **Falls back to the slug**, never to an error or an empty pill. The
    taxonomy is upstream's and it may add a leaf between syncs, so an
    unknown slug means "not synced yet", not "broken" -- and showing the raw
    slug is exactly what this replaces, so the failure mode is the old
    behaviour rather than a blank.
    """
    if not slug:
        return ""
    estado = getattr(request, "state", None)
    mapa = getattr(estado, "_categorias", None)
    if mapa is None:
        db = getattr(estado, "db", None)
        if db is None:
            return slug
        try:
            from .models import CatalogCategoria

            mapa = {
                fila.slug: fila.nombre
                for fila in db.scalars(select(CatalogCategoria))
            }
        except Exception:  # pragma: no cover - a label must never 500 a page
            mapa = {}
        if estado is not None:
            estado._categorias = mapa
    return mapa.get(slug, slug)


templates.env.globals["categoria_nombre"] = categoria_nombre
