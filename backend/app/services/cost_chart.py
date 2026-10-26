"""The histórico card's arithmetic and its SVG. Pure -- no DB, no network,
no settings, same rule as `units.py`, `costing.py` and `graph.py`.

Ported from restaurant-manager's `services/price_stats.py`, deliberately
keeping its shape so the two apps' histórico cards read as one system: the
same six statistics, the same Variación tile, and an inline SVG rather than
a charting library. **No Chart.js here** -- a line and six numbers do not
justify 200 kB of vendored JavaScript, and an `<svg>` the server rendered
needs no client code at all, which is one fewer thing to break with the
internet down.

Two rules the card depends on:

- **The statistics are computed over the same series the chart plots.** A
  tile that disagreed with the line right above it would read as a bug in
  one of them, and nobody could tell which.
- **`variacion` returning `None` means "nothing to compare", and exactly
  `0` means "it did not move".** Those are different answers and the caller
  renders them differently; collapsing them would put an arrow beside a
  cost that held.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from decimal import Decimal

_ALTO = 220
_ANCHO = 1280
_PAD_I, _PAD_D, _PAD_A, _PAD_B = 62, 16, 16, 26


@dataclass(frozen=True)
class Punto:
    """One stored cost, as the chart draws it.

    `estimado` is what makes a backfilled point legible as backfilled: it
    was computed later than the day it is stamped with, from today's recipe
    and today's mappings, so the price data behind it is real but the
    recipe behind it may not be what was actually cooked that day (D32).
    """

    fecha: dt.date
    valor: Decimal
    estimado: bool = False


def estadisticas(valores: list[Decimal]) -> dict[str, Decimal] | None:
    """Media, mediana, moda, desviación estándar poblacional, máximo,
    mínimo -- or None with nothing to summarize.
    """
    if not valores:
        return None
    return {
        "mean": statistics.mean(valores),
        "median": statistics.median(valores),
        "mode": statistics.mode(valores),
        "std": statistics.pstdev(valores),
        "max": max(valores),
        "min": min(valores),
    }


def variacion(valores: list[Decimal]) -> Decimal | None:
    """Percentage move of the last value against the one before it.

    `None` when there is nothing to compare (fewer than two points, or a
    base that is not positive -- a receta whose every ingredient lost its
    price legitimately stores a cost of zero, and a percentage against that
    base is a division by zero or a sign-flipped lie). Exactly `0` is a
    real answer: the cost held.
    """
    if len(valores) < 2:
        return None
    anterior, ultimo = valores[-2], valores[-1]
    if anterior <= 0:
        return None
    return (ultimo - anterior) / anterior * Decimal(100)


def svg(puntos: list[Punto]) -> str | None:
    """An inline SVG line chart, or None with fewer than two points to draw
    a line between.

    Estimated points get a hollow marker and the segments reaching them a
    dashed stroke, so a chart that is mostly backfill cannot be mistaken for
    a chart that is mostly observation.
    """
    if len(puntos) < 2:
        return None

    puntos = sorted(puntos, key=lambda p: p.fecha)
    fechas = [p.fecha for p in puntos]
    valores = [p.valor for p in puntos]
    f_min, f_max = min(fechas), max(fechas)
    v_min, v_max = min(valores), max(valores)
    span = (f_max - f_min).days or 1
    plano = v_min == v_max

    def x(f: dt.date) -> float:
        return _PAD_I + (f - f_min).days / span * (_ANCHO - _PAD_I - _PAD_D)

    def y(v: Decimal) -> float:
        if plano:
            return (_PAD_A + _ALTO - _PAD_B) / 2
        razon = float((v - v_min) / (v_max - v_min))
        return _ALTO - _PAD_B - razon * (_ALTO - _PAD_A - _PAD_B)

    coords = [(x(p.fecha), y(p.valor), p) for p in puntos]

    # One polyline per run of same-kind segments, so the dashes land on the
    # estimated stretch only. A segment counts as estimated when either end
    # is: the join between real and backfilled is itself not an observation.
    segmentos: list[str] = []
    for (x1, y1, p1), (x2, y2, p2) in zip(coords, coords[1:], strict=False):
        clase = "es-estimado" if (p1.estimado or p2.estimado) else ""
        segmentos.append(
            f'<polyline points="{x1:.1f},{y1:.1f} {x2:.1f},{y2:.1f}" '
            f'class="cost-chart__line {clase}" />'
        )

    marcas = "".join(
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" '
        f'class="cost-chart__pt{" cost-chart__pt--estimado" if p.estimado else ""}">'
        f"<title>{p.fecha.strftime('%d/%m/%Y')}: CRC {p.valor:,.2f}"
        f"{' (estimado)' if p.estimado else ''}</title></circle>"
        for cx, cy, p in coords
    )

    etiquetas = (
        f'<text x="4" y="{_PAD_A + 4:.1f}" class="cost-chart__label">₡{v_max:,.2f}</text>'
        f'<text x="4" y="{_ALTO - _PAD_B + 4:.1f}" class="cost-chart__label">₡{v_min:,.2f}</text>'
        f'<text x="{_PAD_I}" y="{_ALTO - 6:.1f}" class="cost-chart__label">'
        f'{f_min.strftime("%d/%m/%Y")}</text>'
        f'<text x="{_ANCHO - _PAD_D}" y="{_ALTO - 6:.1f}" class="cost-chart__label" '
        f'text-anchor="end">{f_max.strftime("%d/%m/%Y")}</text>'
    )

    return (
        f'<svg viewBox="0 0 {_ANCHO} {_ALTO}" class="cost-chart" role="img" '
        f'aria-label="Histórico de costo">'
        f"{''.join(segmentos)}{marcas}{etiquetas}"
        f"</svg>"
    )


# ── la chispa del tablero (DESIGN.md §3.6) ────────────────────────────────
_SPARK_ANCHO = 120
_SPARK_ALTO = 26


def variacion_ventana(valores: list[Decimal]) -> Decimal | None:
    """Percentage move across the **whole series**: first against last.

    Deliberately not `variacion`, which compares the last two points. The
    two answer different questions and the difference is not cosmetic: the
    history stores one point per day and rides the catalog sync, so
    yesterday-against-today is almost always exactly 0 even for a plate
    that has drifted 9% over the quarter. `variacion` is right for the
    histórico card, which is showing a series already on screen;
    this is right for the board, whose question is "which plates have
    moved?"

    `None` when there is nothing to compare -- fewer than two points, or a
    base that is not positive, for the same reason `variacion` refuses it.
    """
    if len(valores) < 2:
        return None
    base, ultimo = valores[0], valores[-1]
    if base <= 0:
        return None
    return (ultimo - base) / base * Decimal(100)


def spark(valores: list[Decimal]) -> str | None:
    """A bare sparkline: one polyline, no axes, no labels, no markers.

    Returns None with fewer than two points, and **None when the series is
    flat**, which is the case that matters. Today's real history is 27
    identical points per receta -- four insumos with stable prices produce
    a straight line -- and nine cards each drawing a flat line is nine
    pieces of ink saying nothing, which teaches the operator to stop
    looking. The card decides *whether* to ask for one by its own
    threshold; this refuses the degenerate case regardless, so a flat line
    can never be drawn by any caller.

    The *presence* of a sparkline is therefore itself the signal (§3.6).
    """
    if len(valores) < 2:
        return None
    v_min, v_max = min(valores), max(valores)
    if v_max == v_min:
        return None

    paso = _SPARK_ANCHO / (len(valores) - 1)
    puntos = " ".join(
        f"{i * paso:.1f},"
        f"{_SPARK_ALTO - float((v - v_min) / (v_max - v_min)) * _SPARK_ALTO:.1f}"
        for i, v in enumerate(valores)
    )
    return (
        f'<svg viewBox="0 0 {_SPARK_ANCHO} {_SPARK_ALTO}" class="spark" '
        f'preserveAspectRatio="none" role="img" aria-hidden="true">'
        f'<polyline points="{puntos}" class="spark__line" /></svg>'
    )


# ── la barra de composición (DESIGN.md §3.3) ──────────────────────────────
# Twelve categorical colours, reused from restaurant-manager's palette
# rather than invented here (D37). That set was arrived at by an actual
# CVD-constrained search -- upstream's app.css records the deltaE figures --
# so a second palette would be both wasteful and measurably worse.
#
# These are custom-property NAMES, not hex. Unlike upstream's charts (which
# feed Chart.js through a canvas, where var() does not resolve and the token
# has to be looked up in JS) this bar is plain HTML: `background: var(--chart-cat-3)`
# in an inline style resolves against :root like any other declaration. So
# the one place the colours are written down stays app.css.
_CAT = tuple(f"--chart-cat-{n}" for n in range(1, 13))


@dataclass(frozen=True)
class Segmento:
    """One line's share of a receta's cost, as the bar draws it."""

    nombre: str
    costo: Decimal
    pct: Decimal
    color: str  # a CSS custom-property name, e.g. "--chart-cat-3"


def composicion(pares: list[tuple[str, Decimal | None]]) -> list[Segmento]:
    """Cost share per line, in **recipe order**.

    Deliberately not sorted by size. The order of a receta's lines is the
    order the kitchen works in, the drag handle exists to change it, and a
    bar that reordered itself under the rows it labels would be unreadable.

    Since DESIGN.md D39 this is the *only* expression of cost share on the
    builder -- there is no per-row fill and no `%` column any more, because
    three renderings of one fact is two too many. That makes this function
    load-bearing rather than decorative: if it returns nothing, the screen
    stops answering "what is actually expensive here", which §10.1 says is
    the question it exists for.

    A line with no cost contributes no segment and is not an error: an
    insumo with no usable price has no cost (non-negotiable #7), the receta
    is already flagged `incompleto`, and the banner names it. Its share is
    genuinely unknown, and a zero-width segment would claim it is nothing.
    """
    con_costo = [(n, c) for n, c in pares if c is not None]
    total = sum((c for _, c in con_costo), Decimal("0"))
    if total <= 0:
        return []
    return [
        Segmento(
            nombre=nombre,
            costo=costo,
            pct=(costo / total * 100).quantize(Decimal("0.1")),
            color=_CAT[i % len(_CAT)],
        )
        for i, (nombre, costo) in enumerate(con_costo)
    ]
