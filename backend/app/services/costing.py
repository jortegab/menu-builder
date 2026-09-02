"""The costing engine's pure half: reduce an insumo's price series to one
number, and spread that number over what survives merma. No DB, no
network, no settings -- same rationale as `units.py`.

PLAN.md §7.3 and §7.4 are the specification; this module is that
specification and nothing else. The DB-facing half (building the series
out of `catalog_price_point` rows through each fuente's own
`factor_conversion`) lives in `services/insumo_costs.py`, which imports
this.

**Three rules here are load-bearing and each exists because the obvious
alternative is silently wrong:**

- An insumo with no usable observation has **no cost** (`None`), never 0.
  A zero makes the ingredient we know least about look like the cheapest
  thing on the menu (PLAN.md non-negotiable #7).
- An **empty window is not an empty series**. Falling back to the newest
  observation outside the window, flagged and aged, beats both silence
  (which hides a delisted ingredient) and refusal (which breaks a whole
  menu over one seasonal item). See D12.
- Merma divides, it does not multiply. Cost is spread over what *survives*.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# DB values are English (PLAN.md D3); the Spanish labels the UI shows live
# in the templates, not here -- this module stays free of presentation.
METHODS = (
    "last",
    "avg_window",
    "weighted_avg_window",
    "median_window",
    "max_window",
    "min_window",
)

# Median, not the average, and the choice is evidence-based rather than
# taste (PLAN.md Q3, answered 2026-09-02 against the live catalog): 6% of
# the products with 3+ purchases in a 90-day window carry at least one bad
# invoice line -- PEREJIL priced at ₡120.79 among ₡850-1190 is the canonical
# one. A median ignores it; a mean absorbs it, and on an insumo with only
# three observations it absorbs it hard. For the 54% of products with <=2
# purchases per window the median is arithmetically identical to every
# other method anyway, so the robustness costs nothing where it is not
# needed. `weighted_avg_window` stays one setting away for the insumos
# where "what I actually paid on average" is the better question.
DEFAULT_METHOD = "median_window"
DEFAULT_WINDOW_DAYS = 90

# Above this, merma is a typo, not a yield. The divisor's asymptote turns a
# fat-fingered 995 into an astronomically expensive plate that still looks
# like a number, so it is rejected as input instead (PLAN.md §7.3).
MAX_MERMA_PCT = Decimal(95)

_CENT = Decimal("0.0001")


@dataclass(frozen=True)
class Observation:
    """One purchase of one producto, already converted to price-per-
    unidad-de-uso by its fuente's `factor_conversion`.

    `cantidad_unidad_uso` is that purchase's quantity in the same unit --
    needed only by `weighted_avg_window`, but carried on every observation
    so the series does not have to be rebuilt when the method changes.
    """

    fecha: dt.date
    precio_por_unidad_uso: Decimal
    cantidad_unidad_uso: Decimal
    fuente_id: int | None = None
    catalog_price_point_id: int | None = None


@dataclass(frozen=True)
class CostResult:
    """A cost, plus everything the "¿Por qué este costo?" panel needs to
    justify it (PLAN.md §7.6). Every field here appears on screen -- this
    is not debug metadata, it is the audit trail that makes the operator
    willing to trust a number they did not compute themselves.
    """

    valor: Decimal | None  # None == no usable price. Never 0 as a stand-in.
    metodo: str
    ventana_dias: int
    observaciones_usadas: int
    desde: dt.date | None  # window lower bound actually applied
    precio_viejo: bool = False  # D12's out-of-window fallback fired
    antiguedad_dias: int | None = None  # age of the newest observation used
    merma_pct: Decimal = Decimal(0)

    @property
    def sin_precio(self) -> bool:
        return self.valor is None


def _in_window(
    serie: list[Observation], ventana_dias: int, hoy: dt.date
) -> list[Observation]:
    desde = hoy - dt.timedelta(days=ventana_dias)
    return [o for o in serie if o.fecha >= desde]


def _reduce(serie: list[Observation], metodo: str) -> Decimal:
    """The method applied to a non-empty series. `last` is resolved by the
    caller (it ignores the window), so this only ever sees the aggregates.
    """
    precios = [o.precio_por_unidad_uso for o in serie]
    if metodo == "avg_window":
        return sum(precios, Decimal(0)) / Decimal(len(precios))
    if metodo == "median_window":
        return Decimal(statistics.median(precios))
    if metodo == "max_window":
        return max(precios)
    if metodo == "min_window":
        return min(precios)
    if metodo == "weighted_avg_window":
        peso_total = sum((o.cantidad_unidad_uso for o in serie), Decimal(0))
        # A series whose quantities are all zero or missing is not a
        # weighting failure worth surfacing to the operator -- it degrades
        # to the plain mean, which is what a weighted mean of equal weights
        # is anyway.
        if peso_total <= 0:
            return sum(precios, Decimal(0)) / Decimal(len(precios))
        return (
            sum((o.precio_por_unidad_uso * o.cantidad_unidad_uso for o in serie), Decimal(0))
            / peso_total
        )
    raise ValueError(f"Método de costeo desconocido: {metodo!r}")


def price_from_series(
    serie: list[Observation],
    metodo: str = DEFAULT_METHOD,
    ventana_dias: int = DEFAULT_WINDOW_DAYS,
    hoy: dt.date | None = None,
) -> CostResult:
    """Reduce a price series to one price per unidad de uso, before merma.

    `serie` is every observation from every confirmed, active fuente of one
    insumo -- the union across suppliers, not one supplier's history. That
    union is deliberate and it is what makes `last` volatile for an insumo
    with two active suppliers: it answers "what would this cost me to cook
    today", which is the most recent thing you actually paid, from whoever
    you actually bought it from.
    """
    if metodo not in METHODS:
        raise ValueError(f"Método de costeo desconocido: {metodo!r}")
    hoy = hoy or dt.date.today()

    if not serie:
        return CostResult(
            valor=None,
            metodo=metodo,
            ventana_dias=ventana_dias,
            observaciones_usadas=0,
            desde=None,
        )

    ordenada = sorted(serie, key=lambda o: o.fecha)

    if metodo == "last":
        ultima = ordenada[-1]
        return CostResult(
            valor=ultima.precio_por_unidad_uso,
            metodo=metodo,
            ventana_dias=ventana_dias,
            observaciones_usadas=1,
            desde=None,  # `last` ignores the window by definition
            antiguedad_dias=(hoy - ultima.fecha).days,
        )

    desde = hoy - dt.timedelta(days=ventana_dias)
    dentro = _in_window(ordenada, ventana_dias, hoy)

    if dentro:
        return CostResult(
            valor=_reduce(dentro, metodo),
            metodo=metodo,
            ventana_dias=ventana_dias,
            observaciones_usadas=len(dentro),
            desde=desde,
            antiguedad_dias=(hoy - dentro[-1].fecha).days,
        )

    # D12: the window is empty but the series is not. Fall back to the
    # newest observation there is, and say so loudly enough that the UI can
    # put an age on it -- an ingredient nobody has bought in six months is
    # a fact about the ingredient, not a reason to refuse to cost the menu.
    ultima = ordenada[-1]
    return CostResult(
        valor=ultima.precio_por_unidad_uso,
        metodo=metodo,
        ventana_dias=ventana_dias,
        observaciones_usadas=1,
        desde=desde,
        precio_viejo=True,
        antiguedad_dias=(hoy - ultima.fecha).days,
    )


def apply_merma(resultado: CostResult, merma_pct: Decimal) -> CostResult:
    """Spread a purchased price over the fraction that survives preparation.

        costo_por_unidad_uso = precio / (1 - merma_pct/100)

    Buy pechuga at ₡4 000/kg with 12% merma and the kitchen's real cost is
    ₡4 545 per usable kg. A `None` cost stays `None` -- merma on no price
    is still no price, and this is the one place that invariant could be
    lost by accident.
    """
    if merma_pct < 0 or merma_pct >= MAX_MERMA_PCT:
        raise ValueError(f"merma_pct fuera de rango [0, {MAX_MERMA_PCT}): {merma_pct}")
    if resultado.valor is None:
        return resultado
    if merma_pct == 0:
        # Re-stamped rather than returned as-is so `merma_pct` on the result
        # always reflects what was applied, including deliberately nothing.
        return CostResult(**{**resultado.__dict__, "merma_pct": Decimal(0)})
    rendimiento = (Decimal(100) - merma_pct) / Decimal(100)
    return CostResult(
        **{
            **resultado.__dict__,
            "valor": (resultado.valor / rendimiento).quantize(_CENT, rounding=ROUND_HALF_UP),
            "merma_pct": merma_pct,
        }
    )


def costo_unitario(
    serie: list[Observation],
    merma_pct: Decimal = Decimal(0),
    metodo: str = DEFAULT_METHOD,
    ventana_dias: int = DEFAULT_WINDOW_DAYS,
    hoy: dt.date | None = None,
) -> CostResult:
    """`price_from_series` then `apply_merma` -- the whole §7.3/§7.4 path in
    one call, which is how every caller actually wants it.
    """
    return apply_merma(price_from_series(serie, metodo, ventana_dias, hoy), merma_pct)
