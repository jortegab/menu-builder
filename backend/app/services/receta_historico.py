"""Stored daily cost per receta -- the DB half of the histórico card.

**Stored, never recomputed at render time** (D13). Deriving the chart from
today's mappings would rewrite the past every time a mapping, a merma or a
costing method changed, and a cost chart that changes shape retroactively
is worse than no chart: the one question it exists to answer is "did this
plate get more expensive", and it cannot answer that if the old points move
too.

So there are exactly two ways a row gets written, and they are different
things:

- **`instantanea`** takes today's cost and stores it. It runs after a
  successful catalog sync -- which is when prices can actually have moved
  -- and from a button in Ajustes. It is an upsert on `(receta_id, fecha)`,
  so running it five times in a day is the same as running it once, and
  the last run of the day wins (a recipe edited at 4pm is what that day
  records).
- **`backfill`** fills days that have already passed, from today's recipe
  and today's mappings (D32). Every row it writes is marked by having a
  `computed_at` later than its own `fecha`, and the card draws those points
  hollow and dashed and says so. It is a seeding tool, not a source of
  truth -- see the argument in PLAN.md §7.7.

There is no scheduler in this app, deliberately: the sync is already
manual and synchronous (`routers/ajustes.py` explains why), and a cron that
nobody watches is a worse answer than a button beside the sync that
produced the prices.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Receta, RecetaCostoHistorico
from . import catalog_sync, cost_chart, receta_costs
from .costing import DEFAULT_METHOD, DEFAULT_WINDOW_DAYS


def _config(ajustes) -> tuple[str, int]:
    """The app-wide method and window, recorded on every stored row so a
    figure can be read back with the assumptions that produced it. The
    per-insumo overrides are not representable in one column and are not
    tried -- this records the app's stance on that day, not each insumo's.
    """
    return (
        getattr(ajustes, "metodo_costeo_default", None) or DEFAULT_METHOD,
        getattr(ajustes, "ventana_dias_default", None) or DEFAULT_WINDOW_DAYS,
    )


def registrar(
    db: Session,
    receta: Receta,
    *,
    fecha: dt.date,
    ajustes=None,
    mirror_as_of: dt.datetime | None = None,
    grafo=None,
    cache=None,
    hoy: dt.date | None = None,
) -> RecetaCostoHistorico:
    """Upsert one receta's cost for one date.

    `hoy` defaults to `fecha`, which is what makes a backfill honest about
    *prices*: costing for 2026-06-01 sees only observations up to that day,
    because that is what `costing.py`'s window does with `hoy`. It is the
    recipe and the mappings that are today's, not the price history.
    """
    metodo, ventana = _config(ajustes)
    costeo = receta_costs.costear(
        db, receta, ajustes=ajustes, hoy=hoy or fecha, grafo=grafo, cache=cache
    )

    fila = db.scalar(
        select(RecetaCostoHistorico).where(
            RecetaCostoHistorico.receta_id == receta.id,
            RecetaCostoHistorico.fecha == fecha,
        )
    )
    if fila is None:
        fila = RecetaCostoHistorico(receta_id=receta.id, fecha=fecha)
        db.add(fila)

    fila.costo_total = costeo.costo_total
    fila.costo_por_porcion = costeo.costo_por_porcion
    # A data error (`Costeo.error`) stores as `incompleto` with no cost: it
    # has no number, which is the only thing the chart cares about, and the
    # screen that can explain *why* is the receta's own.
    fila.estado = "completo" if costeo.estado == "completo" else "incompleto"
    fila.metodo_costeo = metodo
    fila.ventana_dias = ventana
    fila.mirror_as_of = mirror_as_of
    # Local time, not UTC. `_es_estimado` compares this against `fecha`, and
    # Costa Rica is UTC-6: a snapshot taken at 20:00 stamped in UTC would
    # land on tomorrow's date and mark a perfectly real observation as an
    # estimate every single evening.
    fila.computed_at = dt.datetime.now().astimezone()
    db.commit()
    return fila


def instantanea(
    db: Session, *, fecha: dt.date | None = None, ajustes=None
) -> int:
    """Store today's cost for every active receta. Returns how many.

    One graph load and one insumo-cost cache for the whole run -- the same
    reason `costear_muchas` exists, and it matters more here because this
    walks the entire menu.
    """
    fecha = fecha or dt.date.today()
    recetas = list(db.scalars(select(Receta).where(Receta.activo.is_(True))))
    if not recetas:
        return 0
    grafo = receta_costs.cargar_grafo(db)
    cache: dict[int, object] = {}
    as_of = catalog_sync.mirror_as_of(db)
    for receta in recetas:
        registrar(
            db, receta, fecha=fecha, ajustes=ajustes, mirror_as_of=as_of,
            grafo=grafo, cache=cache,
        )
    return len(recetas)


def backfill(
    db: Session,
    *,
    dias: int = 180,
    paso_dias: int = 7,
    hasta: dt.date | None = None,
    ajustes=None,
) -> int:
    """Seed the chart with past days, computed from today's recipe (D32).

    **Never overwrites a row that already exists** -- a day that was
    actually observed stays observed, and re-running this cannot turn real
    history into estimates.

    Weekly by default rather than daily: the underlying prices move on
    purchase dates, not on calendar days, so 26 points over six months draw
    the same shape as 180 and cost a seventh of the costing work.
    """
    hasta = hasta or dt.date.today()
    recetas = list(db.scalars(select(Receta).where(Receta.activo.is_(True))))
    if not recetas:
        return 0

    existentes = {
        (rid, f)
        for rid, f in db.execute(
            select(RecetaCostoHistorico.receta_id, RecetaCostoHistorico.fecha)
        ).all()
    }
    grafo = receta_costs.cargar_grafo(db)
    escritas = 0
    for offset in range(dias, 0, -paso_dias):
        fecha = hasta - dt.timedelta(days=offset)
        # One cache per date: an insumo's cost is a function of `hoy`, so
        # sharing it across dates would flatten the whole chart into one
        # value -- the exact bug this loop exists to avoid.
        cache: dict[int, object] = {}
        for receta in recetas:
            if (receta.id, fecha) in existentes:
                continue
            registrar(
                db, receta, fecha=fecha, ajustes=ajustes, grafo=grafo, cache=cache
            )
            escritas += 1
    return escritas


def serie(db: Session, receta: Receta, *, limite: int = 400) -> list[RecetaCostoHistorico]:
    filas = list(
        db.scalars(
            select(RecetaCostoHistorico)
            .where(RecetaCostoHistorico.receta_id == receta.id)
            .order_by(RecetaCostoHistorico.fecha.desc())
            .limit(limite)
        )
    )
    return list(reversed(filas))


def _es_estimado(fila: RecetaCostoHistorico) -> bool:
    """A row whose `computed_at` is later than the day it is stamped with
    was backfilled. No extra column: `computed_at` already carries it, and
    adding one would mean a hand-written ALTER TABLE (D15) for information
    the table already has.
    """
    if fila.computed_at is None:  # pragma: no cover - server default
        return False
    # Compared in the app's own timezone, so a row Postgres stamped in UTC
    # and one this app wrote locally answer the same question.
    return fila.computed_at.astimezone().date() > fila.fecha


def tarjeta(db: Session, receta: Receta) -> dict:
    """Everything the histórico card renders: the points, the chart, the six
    statistics and the Variación tile -- all off the *same* series, so a
    tile can never disagree with the line above it.
    """
    filas = serie(db, receta)
    puntos = [
        cost_chart.Punto(fecha=f.fecha, valor=_valor_fila(receta, f), estimado=_es_estimado(f))
        for f in filas
        if _valor_fila(receta, f) is not None
    ]
    valores = [p.valor for p in puntos]
    return {
        "filas": filas,
        "puntos": puntos,
        "svg": cost_chart.svg(puntos),
        "stats": cost_chart.estadisticas(valores),
        "variacion": cost_chart.variacion(valores),
        "estimados": sum(1 for p in puntos if p.estimado),
        "sin_costo": sum(1 for f in filas if _valor_fila(receta, f) is None),
    }


def _valor_fila(receta: Receta, fila: RecetaCostoHistorico) -> Decimal | None:
    """What the chart plots: cost per portion for a plato, cost of the whole
    batch for a preparación.

    A preparación's per-unit-of-yield figure would be the more useful
    number, but it depends on `rendimiento_cantidad`, which is *not* stored
    on the history row -- dividing an old cost by today's yield would be
    exactly the retroactive rewrite D13 exists to prevent.
    """
    if receta.tipo == "plato":
        return fila.costo_por_porcion
    return fila.costo_total


# How much a plate has to have moved across its stored window before the
# board draws its line. Below this the card shows the figure alone, and the
# *presence* of a sparkline is what says something happened (§3.6).
UMBRAL_TABLERO = Decimal("2")


def resumen_muchas(
    db: Session, recetas: list[Receta], *, umbral: Decimal = UMBRAL_TABLERO
) -> dict[int, dict]:
    """Per-receta `{variacion, spark}` for the menu board, in ONE query.

    The per-receta `tarjeta` would be a query apiece, which is forty on a
    real menu -- the same reason `receta_costs.costear_muchas` exists. This
    reads every history row for the whole list at once and groups in
    Python.

    The sparkline is drawn only when the window variación clears `umbral`.
    That is not decoration policy, it is signal policy: today's real data
    is 27 identical points per receta, and nine flat lines are nine pieces
    of ink saying nothing. `cost_chart.spark` refuses a flat series anyway,
    so this threshold only ever removes lines that would be technically
    non-flat and practically noise.
    """
    if not recetas:
        return {}
    por_id = {r.id: r for r in recetas}
    filas = db.scalars(
        select(RecetaCostoHistorico)
        .where(RecetaCostoHistorico.receta_id.in_(list(por_id)))
        .order_by(RecetaCostoHistorico.receta_id, RecetaCostoHistorico.fecha)
    )

    series: dict[int, list[Decimal]] = {}
    for fila in filas:
        valor = _valor_fila(por_id[fila.receta_id], fila)
        if valor is not None:
            series.setdefault(fila.receta_id, []).append(valor)

    out: dict[int, dict] = {}
    for rid, valores in series.items():
        var = cost_chart.variacion_ventana(valores)
        out[rid] = {
            "variacion": var,
            "puntos": len(valores),
            "spark": cost_chart.spark(valores) if var is not None and abs(var) >= umbral else None,
        }
    return out
