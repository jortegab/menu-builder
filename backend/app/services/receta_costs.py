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
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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
    is one query with one join.
    """
    filas = db.scalars(select(Receta).options(selectinload(Receta.lineas))).all()
    return {
        r.id: graph.Receta(
            id=r.id,
            nombre=r.nombre,
            tipo=r.tipo,
            rendimiento_cantidad=r.rendimiento_cantidad,
            rendimiento_unidad=r.rendimiento_unidad,
            porciones=r.porciones,
            lineas=[
                graph.Linea(
                    componente_tipo=ln.tipo_componente,
                    componente_id=(
                        ln.insumo_id if ln.tipo_componente == "insumo" else ln.componente_receta_id
                    ),
                    cantidad=ln.cantidad,
                    unidad=ln.unidad,
                    orden=ln.orden,
                    linea_id=ln.id,
                )
                for ln in sorted(r.lineas, key=lambda ln: ln.orden)
            ],
        )
        for r in filas
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
    """
    return {ln.id: ln for ln in receta.lineas}
