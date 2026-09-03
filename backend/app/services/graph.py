"""The recipe graph: cycle detection, depth-limited traversal, and cost
aggregation over a DAG of recetas and insumos. Pure -- it operates on plain
dataclasses the DB layer builds, so the whole of PLAN.md §7.5 is testable
without a database.

A `receta` may contain other recetas (a plato uses a salsa, a salsa uses a
fondo). That makes the menu a directed graph, and two things follow that
the rest of the app depends on:

- **It must stay acyclic** (non-negotiable #5). A salsa that transitively
  contains itself has no finite cost; the traversal would recurse until the
  stack gives out. `would_create_cycle` rejects the edge at write time and
  names the path, and `cost_recipe` defends itself again on read -- a
  defensive check costs microseconds and a hung page costs an evening.
- **Incompleteness propagates, cost does not.** If any leaf has no price,
  the whole recipe's cost is `None` and the missing ingredients are named.
  A partial sum is never returned, not even alongside a warning flag: a
  number that reaches a caller reaches a screen, and a number on a screen
  is a number someone will use (D10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .costing import CostResult
from .units import IncompatibleUnits, convert

# The operator's real kitchen nests three levels -- fondo -> salsa -> plato
# (PLAN.md Q4, answered 2026-09-02). Five leaves two levels of headroom for
# a preparación nobody has modelled yet, while staying tight enough that a
# runaway nest surfaces as `RecipeTooDeep` at the fourth or fifth hop rather
# than the eighth. Raise it if a real recipe ever needs the room; do not
# raise it to make a suspicious graph load.
MAX_DEPTH = 5

_CENT = Decimal("0.01")


class RecipeCycle(ValueError):
    """A cycle in the receta graph. Carries the path so the operator sees
    *which* chain closed the loop -- "Salsa criolla -> Fondo -> Salsa
    criolla" is fixable; "referencia circular" is not.
    """

    def __init__(self, path: list[int]) -> None:
        self.path = path
        super().__init__(" -> ".join(str(n) for n in path))


class RecipeTooDeep(ValueError):
    """Nesting past `MAX_DEPTH`. Distinct from `RecipeCycle` on purpose: a
    cycle is a data error to reject, a too-deep graph is a legitimate
    structure this app declines to cost, and the operator-facing message
    differs.
    """


@dataclass(frozen=True)
class Linea:
    """One line of a receta: a quantity of either an insumo or another
    receta. Mirrors `receta_linea`, minus the persistence concerns.
    """

    componente_tipo: str  # "insumo" | "receta"
    componente_id: int
    cantidad: Decimal
    unidad: str
    orden: int = 0


@dataclass(frozen=True)
class Receta:
    """A receta as the costing engine needs it. `rendimiento_*` is set for
    a `preparacion` (what it yields, e.g. 2 500 g of salsa) and `porciones`
    for a `plato` -- the pair is what lets a preparación be consumed by the
    gram like any insumo (PLAN.md §7.5).
    """

    id: int
    nombre: str
    tipo: str  # "preparacion" | "plato"
    lineas: list[Linea] = field(default_factory=list)
    rendimiento_cantidad: Decimal | None = None
    rendimiento_unidad: str | None = None
    porciones: int | None = None


@dataclass(frozen=True)
class Insumo:
    """An insumo as the costing engine needs it: its unit of use and the
    already-computed cost per that unit (`services/costing.py`).
    """

    id: int
    nombre: str
    unidad_base: str
    costo: CostResult


@dataclass
class LineaCosteada:
    linea: Linea
    nombre: str
    costo_unitario: Decimal | None
    costo: Decimal | None
    # Populated only for a receta line, so the builder screen can expand a
    # preparación in place without a second traversal.
    desglose: RecetaCosteada | None = None
    faltante: bool = False


@dataclass
class RecetaCosteada:
    receta_id: int
    nombre: str
    costo_total: Decimal | None
    costo_por_porcion: Decimal | None
    costo_por_unidad_rendimiento: Decimal | None
    lineas: list[LineaCosteada]
    faltantes: list[str]  # names of insumos with no usable price
    estado: str  # "completo" | "incompleto"


def build_adjacency(recetas: dict[int, Receta]) -> dict[int, set[int]]:
    """{receta_id: set of receta ids it directly contains}. Insumo lines are
    leaves and never appear -- only receta-to-receta edges can form a cycle.
    """
    return {
        rid: {ln.componente_id for ln in r.lineas if ln.componente_tipo == "receta"}
        for rid, r in recetas.items()
    }


def find_cycle(adjacency: dict[int, set[int]]) -> list[int] | None:
    """The first cycle in the graph as a path (first node repeated at the
    end), or None. Iterative rather than recursive: this runs as a guard,
    and a guard that can itself blow the stack on the input it is guarding
    against is not a guard.
    """
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[int, int] = dict.fromkeys(adjacency, WHITE)

    for raiz in list(adjacency):
        if color.get(raiz, WHITE) != WHITE:
            continue
        stack: list[tuple[int, list[int]]] = [(raiz, [raiz])]
        while stack:
            nodo, camino = stack.pop()
            if color.get(nodo, WHITE) == GREY:
                color[nodo] = BLACK
                continue
            color[nodo] = GREY
            # Re-push the node so it is marked BLACK once its whole subtree
            # is done -- this is what makes GREY mean "on the current path"
            # rather than merely "visited".
            stack.append((nodo, camino))
            for vecino in adjacency.get(nodo, set()):
                if color.get(vecino, WHITE) == GREY:
                    return camino[camino.index(vecino) :] + [vecino]
                if color.get(vecino, WHITE) == WHITE:
                    stack.append((vecino, camino + [vecino]))
    return None


def would_create_cycle(
    adjacency: dict[int, set[int]], padre: int, hijo: int
) -> list[int] | None:
    """The path that would close if `padre` gained a line containing
    `hijo`, or None if the edge is safe. Called before the insert, so the
    row is refused rather than written and later discovered.
    """
    if padre == hijo:
        return [padre, hijo]
    tentativo = {k: set(v) for k, v in adjacency.items()}
    tentativo.setdefault(padre, set()).add(hijo)
    tentativo.setdefault(hijo, set())
    ciclo = find_cycle(tentativo)
    return ciclo


def cost_recipe(
    receta_id: int,
    recetas: dict[int, Receta],
    insumos: dict[int, Insumo],
    _profundidad: int = 0,
    _en_camino: frozenset[int] = frozenset(),
    _memo: dict[int, RecetaCosteada] | None = None,
) -> RecetaCosteada:
    """Cost one receta, recursing into any preparación it contains.

    Memoized per call tree: a salsa used by six platos on one page is costed
    once. `_en_camino` carries the ancestors of the current node so a cycle
    that survived the write-time guard is caught here instead of recursing
    forever.
    """
    memo = _memo if _memo is not None else {}
    if receta_id in memo:
        return memo[receta_id]
    if receta_id in _en_camino:
        raise RecipeCycle(list(_en_camino) + [receta_id])
    if _profundidad > MAX_DEPTH:
        raise RecipeTooDeep(
            f"La receta anida más de {MAX_DEPTH} niveles; revisá si hay una preparación repetida"
        )

    receta = recetas.get(receta_id)
    if receta is None:
        raise KeyError(f"Receta {receta_id} no encontrada")

    lineas: list[LineaCosteada] = []
    faltantes: list[str] = []
    total = Decimal(0)
    completo = True

    for linea in sorted(receta.lineas, key=lambda ln: ln.orden):
        if linea.componente_tipo == "insumo":
            insumo = insumos.get(linea.componente_id)
            if insumo is None:
                raise KeyError(f"Insumo {linea.componente_id} no encontrado")
            unitario = insumo.costo.valor
            nombre = insumo.nombre
            unidad_componente = insumo.unidad_base
            desglose = None
        else:
            sub = cost_recipe(
                linea.componente_id,
                recetas,
                insumos,
                _profundidad + 1,
                _en_camino | {receta_id},
                memo,
            )
            unitario = sub.costo_por_unidad_rendimiento
            nombre = sub.nombre
            unidad_componente = recetas[linea.componente_id].rendimiento_unidad or "unidad"
            desglose = sub
            if sub.estado == "incompleto":
                faltantes.extend(sub.faltantes)

        if unitario is None:
            completo = False
            if linea.componente_tipo == "insumo":
                faltantes.append(nombre)
            lineas.append(
                LineaCosteada(
                    linea=linea,
                    nombre=nombre,
                    costo_unitario=None,
                    costo=None,
                    desglose=desglose,
                    faltante=True,
                )
            )
            continue

        # A unit mismatch is a data error, not a costing outcome -- it means
        # a line was written that the save-time validation should have
        # refused, so it surfaces as an exception rather than as a missing
        # price the operator would go looking for in the wrong place.
        try:
            cantidad = convert(linea.cantidad, linea.unidad, unidad_componente)
        except IncompatibleUnits as exc:
            raise IncompatibleUnits(exc.desde, exc.hacia) from exc

        costo = (cantidad * unitario).quantize(_CENT, rounding=ROUND_HALF_UP)
        total += costo
        lineas.append(
            LineaCosteada(
                linea=linea,
                nombre=nombre,
                costo_unitario=unitario,
                costo=costo,
                desglose=desglose,
            )
        )

    costo_total = total if completo else None
    por_porcion = None
    por_unidad_rendimiento = None
    if costo_total is not None:
        if receta.porciones:
            por_porcion = (costo_total / Decimal(receta.porciones)).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
        if receta.rendimiento_cantidad:
            por_unidad_rendimiento = costo_total / receta.rendimiento_cantidad

    resultado = RecetaCosteada(
        receta_id=receta_id,
        nombre=receta.nombre,
        costo_total=costo_total,
        costo_por_porcion=por_porcion,
        costo_por_unidad_rendimiento=por_unidad_rendimiento,
        lineas=lineas,
        faltantes=sorted(set(faltantes)),
        estado="completo" if completo else "incompleto",
    )
    memo[receta_id] = resultado
    return resultado
