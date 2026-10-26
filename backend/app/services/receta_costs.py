"""The DB-facing half of recipe costing: load the receta graph out of the
database, cost every insumo it reaches, and hand both to `graph.py`.

The split is the same one `insumo_costs.py` makes against `costing.py`, and
for the same reason: `graph.py` owns the traversal and the arithmetic and
stays pure, so PLAN.md §7.5 is checkable without Postgres. This module owns
the *gathering*.

Three things worth knowing before touching it:

- **The whole receta graph is loaded, the insumos are not.** There are tens
  of recetas and 1 420 catalog products; loading every receta costs one
  query and makes the adjacency exact, while costing every insumo in the
  database to render one plate would be absurd. Only the insumos actually
  reachable from the receta being costed get priced.
- **Insumo costs are cached per call.** A menu list where nine plates use
  pechuga prices pechuga once. `insumo_costs.explicar` is a handful of
  queries per insumo, and that adds up across a whole menu.
- **Costing never raises out of this module.** A cycle that survived the
  write-time guard, a nest past `MAX_DEPTH`, a line whose unit no longer
  converts -- all three are data errors that must not take down the menu
  list. `Costeo.error` carries the Spanish message and the screen shows it
  where the number would have been. The exception is still raised inside
  `graph.py`; what changes here is that it lands as a legible state instead
  of a 500.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Insumo, Receta, RecetaLinea
from . import graph, insumo_costs
from .costing import CostResult
from .units import IncompatibleUnits, UnknownUnit


@dataclass
class Costeo:
    """One receta's cost, or the reason it has none.

    Deliberately three states, not two. `incompleto` means the recipe is
    fine and an ingredient has no price -- the operator's problem, and the
    screen names the ingredient. `error` means the *data* is wrong and the
    cost cannot be attempted at all; those are different sentences and they
    send the operator to different places.
    """

    receta: Receta
    resultado: graph.RecetaCosteada | None = None
    error: str | None = None
    costos_insumo: dict[int, CostResult] = field(default_factory=dict)
    # {nombre: insumo_id} for every insumo reachable from this receta that
    # has no usable price. `graph.py` reports the names -- it never saw an
    # id -- and the banner has to *link* to each one, because "Sal has no
    # price" is only actionable if the next click is Sal's own screen.
    faltantes_ids: dict[str, int] = field(default_factory=dict)

    @property
    def estado(self) -> str:
        if self.error is not None:
            return "error"
        return self.resultado.estado if self.resultado else "incompleto"

    @property
    def costo_total(self) -> Decimal | None:
        return self.resultado.costo_total if self.resultado else None

    @property
    def costo_por_porcion(self) -> Decimal | None:
        return self.resultado.costo_por_porcion if self.resultado else None

    @property
    def costo_por_unidad_rendimiento(self) -> Decimal | None:
        return self.resultado.costo_por_unidad_rendimiento if self.resultado else None

    @property
    def faltantes(self) -> list[str]:
        return self.resultado.faltantes if self.resultado else []

    @property
    def lineas(self) -> list[graph.LineaCosteada]:
        return self.resultado.lineas if self.resultado else []


def cargar_grafo(db: Session) -> dict[int, graph.Receta]:
    """Every receta in the app, as `graph.Receta` dataclasses.

    All of them, not just the subtree being costed: the adjacency has to be
    complete for `find_cycle`'s read-time defence to mean anything, and it
    is two queries either way.

    **The lines are read as columns, never through `Receta.lineas`.** The
    session is configured `expire_on_commit=False` (`db.py`, deliberately),
    so a collection loaded before a line was added stays loaded without it,
    and a cost computed right after a write would be a cost of the recipe as
    it was a moment ago. That is invisible on screen -- the number looks
    perfectly plausible, it is just one line out of date -- and it is
    exactly what `receta_historico.instantanea` does: write, then cost.
    Reading columns sidesteps the identity map entirely.
    """
    lineas: dict[int, list[graph.Linea]] = {}
    for lid, receta_id, orden, tipo_comp, insumo_id, comp_id, cantidad, unidad in db.execute(
        select(
            RecetaLinea.id, RecetaLinea.receta_id, RecetaLinea.orden,
            RecetaLinea.tipo_componente, RecetaLinea.insumo_id,
            RecetaLinea.componente_receta_id, RecetaLinea.cantidad, RecetaLinea.unidad,
        ).order_by(RecetaLinea.receta_id, RecetaLinea.orden, RecetaLinea.id)
    ).all():
        lineas.setdefault(receta_id, []).append(
            graph.Linea(
                componente_tipo=tipo_comp,
                componente_id=insumo_id if tipo_comp == "insumo" else comp_id,
                cantidad=cantidad,
                unidad=unidad,
                orden=orden,
                linea_id=lid,
            )
        )

    return {
        rid: graph.Receta(
            id=rid, nombre=nombre, tipo=tipo,
            rendimiento_cantidad=rc, rendimiento_unidad=ru, porciones=porciones,
            lineas=lineas.get(rid, []),
        )
        for rid, nombre, tipo, rc, ru, porciones in db.execute(
            select(
                Receta.id, Receta.nombre, Receta.tipo,
                Receta.rendimiento_cantidad, Receta.rendimiento_unidad, Receta.porciones,
            )
        ).all()
    }


def insumos_alcanzables(grafo: dict[int, graph.Receta], raices: list[int]) -> set[int]:
    """The insumo ids reachable from `raices`, walking receta lines.

    Depth-limited and visited-guarded for the same reason `cost_recipe` is:
    this runs *before* the traversal that would report a cycle, so it must
    survive one.
    """
    vistos: set[int] = set()
    insumos: set[int] = set()
    pendientes = [(rid, 0) for rid in raices]
    while pendientes:
        rid, profundidad = pendientes.pop()
        if rid in vistos or profundidad > graph.MAX_DEPTH + 1:
            continue
        vistos.add(rid)
        receta = grafo.get(rid)
        if receta is None:
            continue
        for linea in receta.lineas:
            if linea.componente_tipo == "insumo":
                insumos.add(linea.componente_id)
            else:
                pendientes.append((linea.componente_id, profundidad + 1))
    return insumos


def _cargar_insumos(
    db: Session,
    ids: set[int],
    *,
    ajustes,
    hoy: dt.date | None,
    cache: dict[int, CostResult],
) -> dict[int, graph.Insumo]:
    if not ids:
        return {}
    filas = list(db.scalars(select(Insumo).where(Insumo.id.in_(ids))))
    for insumo in filas:
        if insumo.id not in cache:
            cache[insumo.id] = insumo_costs.costo(db, insumo, ajustes=ajustes, hoy=hoy)
    return {
        i.id: graph.Insumo(
            id=i.id, nombre=i.nombre, unidad_base=i.unidad_base, costo=cache[i.id]
        )
        for i in filas
    }


def costear(
    db: Session,
    receta: Receta,
    *,
    ajustes=None,
    hoy: dt.date | None = None,
    grafo: dict[int, graph.Receta] | None = None,
    cache: dict[int, CostResult] | None = None,
) -> Costeo:
    """Cost one receta. Never raises for anything the data can do."""
    grafo = grafo if grafo is not None else cargar_grafo(db)
    cache = cache if cache is not None else {}

    ids = insumos_alcanzables(grafo, [receta.id])
    insumos = _cargar_insumos(db, ids, ajustes=ajustes, hoy=hoy, cache=cache)

    try:
        resultado = graph.cost_recipe(receta.id, grafo, insumos)
    except graph.RecipeCycle as exc:
        nombres = [grafo[n].nombre if n in grafo else f"receta {n}" for n in exc.path]
        return Costeo(
            receta=receta,
            error=(
                "Hay un círculo entre recetas: "
                + " → ".join(nombres)
                + ". Quitá una de esas líneas para poder costear."
            ),
        )
    except graph.RecipeTooDeep as exc:
        return Costeo(receta=receta, error=str(exc))
    except IncompatibleUnits as exc:
        # A line was written that save-time validation should have refused
        # -- most likely a preparación whose yield unit changed underneath
        # it. Named as the data error it is, not as a missing price.
        return Costeo(
            receta=receta,
            error=(
                f"Una línea pide {exc.desde} de algo que se costea en {exc.hacia}. "
                "Corregí la unidad de esa línea."
            ),
        )
    except (UnknownUnit, KeyError) as exc:
        return Costeo(
            receta=receta, error=f"No se pudo costear: hay un dato inconsistente ({exc})."
        )

    return Costeo(
        receta=receta,
        resultado=resultado,
        costos_insumo={i: cache[i] for i in ids if i in cache},
        faltantes_ids={
            i.nombre: i.id for i in insumos.values() if i.costo.valor is None
        },
    )


def costear_muchas(
    db: Session, recetas: list[Receta], *, ajustes=None, hoy: dt.date | None = None
) -> dict[int, Costeo]:
    """{receta_id: Costeo} for a whole list, sharing one graph load and one
    insumo cost cache across all of them -- which is what makes the menu
    list one pass over the insumos instead of one pass per plate.
    """
    if not recetas:
        return {}
    grafo = cargar_grafo(db)
    cache: dict[int, CostResult] = {}
    return {
        r.id: costear(db, r, ajustes=ajustes, hoy=hoy, grafo=grafo, cache=cache)
        for r in recetas
    }


def lineas_por_id(db: Session, receta: Receta) -> dict[int, RecetaLinea]:
    """{linea_id: row} for the receta being rendered, so the builder can put
    a costed line (which `graph.py` knows only as a dataclass) back beside
    its editable database row.

    Queried rather than read off `receta.lineas`, for the same reason
    `cargar_grafo` reads columns: the collection can be one write stale.
    """
    return {
        ln.id: ln
        for ln in db.scalars(
            select(RecetaLinea)
            .where(RecetaLinea.receta_id == receta.id)
            .order_by(RecetaLinea.orden, RecetaLinea.id)
        )
    }


# ── el modo simulador (DESIGN.md §3.9, D42) ───────────────────────────────


@dataclass
class Simulacion:
    """What a "what if" is worth, and what it would drag with it.

    `afectadas` is only ever populated for a preparación, and it is the
    reason this dataclass exists rather than the route just returning a
    `Costeo`: changing a salsa's quantities changes every plato that uses
    it, and "this salsa gets 8% cheaper" is not the decision anyone is
    actually making. "These four plates get cheaper by these amounts" is.
    """

    actual: Costeo
    simulada: Costeo
    # (receta, su costo hoy, su costo con el cambio), for a preparación.
    afectadas: list[tuple[Receta, Costeo, Costeo]]


def _grafo_con(grafo: dict[int, graph.Receta], cantidades: dict[int, Decimal]) -> dict:
    """A copy of the graph with some line quantities substituted.

    **This is the whole simulator, and it is why there is no second costing
    engine** (D16, the non-negotiable most at risk from "make it more
    interactive"). `costear` already accepts an injected `grafo`; the
    "what if" is that graph with different numbers in it, costed by exactly
    the same `graph.cost_recipe` that costs the real thing. No branch in
    the arithmetic, no parallel implementation, and nothing in the browser.

    Everything here is a frozen dataclass, so `replace` builds new objects
    and the loaded graph is not mutated -- which matters because the caller
    costs the *unmodified* graph too, to show both numbers side by side.
    """
    salida: dict[int, graph.Receta] = {}
    for rid, receta in grafo.items():
        lineas = [
            replace(ln, cantidad=cantidades[ln.linea_id])
            if ln.linea_id is not None and ln.linea_id in cantidades
            else ln
            for ln in receta.lineas
        ]
        salida[rid] = replace(receta, lineas=lineas)
    return salida


def simular(
    db: Session,
    receta: Receta,
    cantidades: dict[int, Decimal],
    *,
    ajustes=None,
    hoy: dt.date | None = None,
) -> Simulacion:
    """Cost `receta` as if those lines had those quantities. **Writes
    nothing.**

    Not "writes nothing if used carefully": there is no write path in
    here to get wrong. The substitution happens on in-memory dataclasses
    between `cargar_grafo` and `graph.cost_recipe`, so no `RecetaLinea` is
    touched, no transaction is opened, and `receta_costo_historico` --
    which reads committed rows and rides the catalog sync -- is
    structurally incapable of seeing any of it. A simulated cost landing in
    that table would be a wrong number that looks exactly like a right one,
    which is the precise failure this whole design exists to prevent.
    """
    grafo = cargar_grafo(db)
    grafo_sim = _grafo_con(grafo, cantidades)
    cache: dict[int, CostResult] = {}

    actual = costear(db, receta, ajustes=ajustes, hoy=hoy, grafo=grafo, cache=cache)
    simulada = costear(db, receta, ajustes=ajustes, hoy=hoy, grafo=grafo_sim, cache=cache)

    afectadas: list[tuple[Receta, Costeo, Costeo]] = []
    if receta.tipo == "preparacion":
        consumidores = list(
            db.scalars(
                select(Receta)
                .join(RecetaLinea, RecetaLinea.receta_id == Receta.id)
                .where(RecetaLinea.componente_receta_id == receta.id)
                .distinct()
                .order_by(Receta.nombre)
            )
        )
        for otra in consumidores:
            afectadas.append(
                (
                    otra,
                    costear(db, otra, ajustes=ajustes, hoy=hoy, grafo=grafo, cache=cache),
                    costear(db, otra, ajustes=ajustes, hoy=hoy, grafo=grafo_sim, cache=cache),
                )
            )

    return Simulacion(actual=actual, simulada=simulada, afectadas=afectadas)
