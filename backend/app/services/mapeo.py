"""The mapping queue: catalog products that are not yet any insumo, with a
suggestion for each, and the confirm/reject actions that drain it.

**The queue is the UX** (PLAN.md §8). 1 420 products is not a sitting, so it
arrives filtered by default (D20): food categories only, bought within the
last 180 days, ordered by how much the kitchen actually buys them. That is
~400 rows instead of 1 420, and the rest stays behind a filter rather than
being hidden.

**Nothing here activates a mapping.** Every suggestion lands as
`status='suggested'` and only a person's confirmation makes it
`'confirmed'`, which is the only status a price series reads
(non-negotiable #4). A mis-merged insumo is invisible once it is inside a
cost, so the confirmation step is the whole safety mechanism, not a
formality.

A rejected suggestion is *remembered* (`InsumoMapeoRechazo`), because a
queue that re-offers what the operator already rejected stops being
drained.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AppSetting,
    CatalogAgrupacion,
    CatalogParecido,
    CatalogPricePoint,
    CatalogProduct,
    Insumo,
    InsumoFuente,
    InsumoMapeoRechazo,
    CatalogDescarte,
)
from . import costing, insumo_match, llm_insumo
from .insumos import unique_slug
from .insumos import validate as validar_insumo

# The category families whose products can legitimately end up costed into
# a plate. Everything else -- menaje, limpieza, higiene, oficina,
# utilidades -- is a row the operator would only skip past to reach the
# real work, so the default queue leaves it out.
#
# **An allowlist by prefix, not a blocklist of leaves.** Upstream owns this
# taxonomy and adds leaves to it (`alimentos_aceites` appeared on
# 2026-09-02); a blocklist silently admits every new leaf, while a prefix
# allowlist admits exactly the new leaves of families already chosen. It
# also survives upstream renaming the family, which it has now done twice.
#
# **`empaques` is in here on purpose.** A plate that leaves the kitchen in
# a container pays for that container, so the packaging has to be mappable
# to an insumo like anything else. It is the one non-food family that
# belongs in a recipe cost.
CATEGORIAS_EN_RECETA = ("alimentos", "bebidas", "empaques")

DIAS_RECIENTE = 180


@dataclass(frozen=True)
class Alcance:
    """What the queue is scoped to -- app policy, not a per-visit choice.

    These were two checkboxes on the queue screen itself, and that was
    wrong twice over: it dressed a decision about how this restaurant works
    as something to re-take on every visit, and it put a filter one stray
    click could flip directly above a button that dismisses everything
    matching it. They live in `app_setting` now and the queue only
    *reports* them.

    `dias_reciente == 0` means no date limit at all.
    """

    solo_receta: bool = True
    dias_reciente: int = DIAS_RECIENTE

    @property
    def solo_reciente(self) -> bool:
        return self.dias_reciente > 0


def _fila_ajustes(db: Session) -> AppSetting:
    """The single settings row, created on first read.

    `AppSetting` has claimed to be "seeded on first boot" since it was
    written and nothing ever seeded it -- the table was empty on the live
    database. Creating it lazily is the honest version of that promise: the
    column defaults ARE the app defaults, so a fresh install and a
    two-year-old one answer the same thing.
    """
    fila = db.get(AppSetting, 1)
    if fila is None:
        fila = AppSetting(id=1)
        db.add(fila)
        db.commit()
    return fila


def alcance(db: Session) -> Alcance:
    """The saved scope. Read by the queue, by the gate AND by the nav
    badge -- a badge counting work the queue does not show is a badge that
    never clears."""
    fila = _fila_ajustes(db)
    return Alcance(
        solo_receta=bool(fila.mapeo_solo_receta),
        dias_reciente=max(0, int(fila.mapeo_dias_reciente or 0)),
    )


def guardar_alcance(db: Session, *, solo_receta: bool, dias_reciente: int) -> Alcance:
    """Write the scope. A negative number of days is clamped to 0 (no
    limit) rather than refused: there is no reading of "-30 days" worth
    guessing at, and a settings form that 500s is worse than one that
    rounds."""
    fila = _fila_ajustes(db)
    fila.mapeo_solo_receta = bool(solo_receta)
    fila.mapeo_dias_reciente = max(0, int(dias_reciente))
    db.commit()
    return alcance(db)

log = logging.getLogger(__name__)


@dataclass
class Sugerencia:
    """What the queue offers for one product. Every field is shown, because
    the operator confirming it needs to see *why* it is being suggested --
    especially the full description, since a name alone is exactly how a
    wrong merge slips through.
    """

    producto: CatalogProduct
    compras: int
    insumo_id: int | None          # an existing insumo, when one matched
    insumo_nombre: str | None      # that insumo's name, for the row to show
    nombre_propuesto: str | None   # a new insumo's name, when nothing matched
    unidad_sugerida: str           # preselected unidad base for a new insumo
    confianza: Decimal | None
    origen: str                    # "llm" | "similitud" | "ninguna"
    factor: Decimal | None
    factor_origen: str


def _mapeados(db: Session) -> set[int]:
    """Products already spoken for -- a non-rejected fuente exists."""
    return set(
        db.scalars(
            select(InsumoFuente.catalog_product_id).where(InsumoFuente.status != "rejected")
        )
    )


def _filtros(
    db: Session,
    q: str,
    solo_receta: bool,
    solo_reciente: bool,
    categorias: Sequence[str] = (),
    dias_reciente: int = DIAS_RECIENTE,
):
    """The WHERE clauses that define "pending, under these filters".

    Factored out because three callers need exactly the same set and must
    not drift apart: the queue itself, the gate's count, and the gate's
    bulk dismissal. A count that did not match what the button then
    dismissed would be the worst kind of bug on this screen -- the operator
    would approve one number and get another.

    `categorias` is the leaf-level picker and it is a *set*, not a single
    value: "verduras y frutas" is one pass through the queue, and forcing
    it into two is how the second one never happens. Empty means no
    restriction at all -- never "no categories" -- because the filter's
    resting state has to be the whole catalog.
    """
    clausulas = [CatalogProduct.ignored.is_(False)]
    ya = _mapeados(db) | _descartados(db)
    if ya:
        clausulas.append(CatalogProduct.id.not_in(ya))
    if solo_receta:
        slug = func.coalesce(CatalogProduct.categoria_slug, "")
        # The family itself, or any leaf under it. A NULL categoria_slug
        # coalesces to "" and matches nothing here, which is the right
        # reading: a product upstream has not categorised yet is not
        # evidence that it belongs in a recipe.
        clausulas.append(
            or_(*[
                or_(slug == familia, slug.startswith(f"{familia}_"))
                for familia in CATEGORIAS_EN_RECETA
            ])
        )
    if categorias:
        # Compared through `coalesce`, so the empty slug is a real bucket
        # the operator can tick: an uncategorised product is exactly the
        # kind that hides from every filter and is never decided.
        clausulas.append(
            func.coalesce(CatalogProduct.categoria_slug, "").in_(tuple(categorias))
        )
    if solo_reciente and dias_reciente > 0:
        corte = dt.date.today() - dt.timedelta(days=dias_reciente)
        clausulas.append(CatalogProduct.last_purchase_at >= corte)
    if q.strip():
        clausulas.append(
            func.lower(CatalogProduct.description).like(f"%{q.strip().lower()}%")
        )
    return clausulas


def pendientes(
    db: Session,
    *,
    q: str = "",
    solo_receta: bool = True,
    solo_reciente: bool = True,
    categorias: Sequence[str] = (),
    dias_reciente: int = DIAS_RECIENTE,
    limite: int = 50,
    desde: int = 0,
) -> list[tuple[CatalogProduct, int]]:
    """Unmapped products as (producto, número de compras), busiest first.

    Ordered by purchase count rather than alphabetically because that is the
    order in which mapping pays off: the ingredient bought 98 times matters
    more than the one bought once, and a queue sorted by name buries it.

    `desde` is the triage screen's «saltar»: a product the operator does not
    want to decide about right now steps out of the way without being recorded as a
    decision. It comes back on the next pass, which is the honest
    behaviour -- skipping is not rejecting, and only a person rejecting
    something should be remembered.
    """
    compras = (
        select(
            CatalogPricePoint.catalog_product_id.label("pid"),
            func.count().label("n"),
        )
        .group_by(CatalogPricePoint.catalog_product_id)
        .subquery()
    )

    stmt = (
        select(CatalogProduct, func.coalesce(compras.c.n, 0).label("n"))
        .join(compras, compras.c.pid == CatalogProduct.id, isouter=True)
        .where(*_filtros(db, q, solo_receta, solo_reciente, categorias,
                         dias_reciente))
        .order_by(func.coalesce(compras.c.n, 0).desc(), CatalogProduct.description)
    )
    return [(p, n) for p, n in db.execute(stmt.offset(desde).limit(limite)).all()]


def _orden_de_cola(
    db: Session,
    q: str,
    solo_receta: bool,
    solo_reciente: bool,
    categorias: Sequence[str] = (),
    dias_reciente: int = DIAS_RECIENTE,
):
    """The pending ids in queue order -- `pendientes` without the objects."""
    compras = (
        select(
            CatalogPricePoint.catalog_product_id.label("pid"),
            func.count().label("n"),
        )
        .group_by(CatalogPricePoint.catalog_product_id)
        .subquery()
    )
    return (
        select(CatalogProduct.id)
        .join(compras, compras.c.pid == CatalogProduct.id, isouter=True)
        .where(*_filtros(db, q, solo_receta, solo_reciente, categorias,
                         dias_reciente))
        .order_by(func.coalesce(compras.c.n, 0).desc(), CatalogProduct.description)
    )


def posicion_tras_decidir(
    db: Session,
    *,
    desde: int,
    decididos: set[int],
    q: str = "",
    solo_receta: bool = True,
    solo_reciente: bool = True,
    categorias: Sequence[str] = (),
    dias_reciente: int = DIAS_RECIENTE,
) -> int:
    """Where the cursor belongs once `decididos` have left the queue.

    **Call this before the write, while those rows are still pending.**
    It counts how many of them sit *before* the cursor, because that is the
    only thing that moves it: the product being decided is at the cursor,
    so its own departure pulls the next one into that exact slot and the
    offset should not change at all.

    That is what the old "always go back to `desde=0`" was really guarding
    against. Confirming takes the ticked look-alikes too, and one of those
    can sit anywhere in the queue -- including behind the cursor, which
    shifts everything down by one more and would make a kept offset skip a
    product silently. Resetting to the first card avoided the skip by
    throwing away the operator's place; subtracting the ones that were
    actually behind them keeps both.
    """
    if desde <= 0 or not decididos:
        return max(0, desde)
    prefijo = db.scalars(
        _orden_de_cola(
            db, q, solo_receta, solo_reciente, categorias, dias_reciente
        ).limit(desde)
    ).all()
    return max(0, desde - sum(1 for pid in prefijo if pid in decididos))


def _rechazos(db: Session, product_id: int) -> set[int]:
    return set(
        db.scalars(
            select(InsumoMapeoRechazo.insumo_id).where(
                InsumoMapeoRechazo.catalog_product_id == product_id
            )
        )
    )


def sugerir(db: Session, producto: CatalogProduct, compras: int = 0) -> Sugerencia:
    """One product's suggestion: the LLM if it is configured and answers,
    otherwise deterministic similarity, otherwise a proposed new insumo.

    The factor is resolved from the description either way -- the LLM's
    reading is preferred when it gave one, since it can read shapes the
    regex cannot, but `insumo_match.resolve_factor` covers 76% of the live
    catalog on its own and is what runs when there is no key.
    """
    insumos = list(db.execute(select(Insumo.id, Insumo.slug, Insumo.nombre).where(Insumo.activo)).all())
    rechazados = _rechazos(db, producto.id)
    candidatos = [(i, nombre) for i, slug, nombre in insumos if i not in rechazados]
    por_slug = {slug: (i, nombre) for i, slug, nombre in insumos if i not in rechazados}

    insumo_id: int | None = None
    nombre_propuesto: str | None = None
    confianza: Decimal | None = None
    origen = "ninguna"
    unidad_base_probable = "g"
    llm_presentacion: tuple[Decimal, str] | None = None

    respuesta = llm_insumo.sugerir(
        producto.description, [(slug, nombre) for _, slug, nombre in insumos if _ not in rechazados]
    )
    if respuesta is not None:
        destino, cantidad, unidad = respuesta
        origen = "llm"
        # Confidence is for ordering the queue only. It never crosses a
        # threshold into applying anything.
        confianza = Decimal("0.90")
        if destino.startswith("nuevo:"):
            nombre_propuesto = destino.split(":", 1)[1]
        elif destino in por_slug:
            insumo_id = por_slug[destino][0]
        if cantidad is not None and unidad is not None:
            llm_presentacion = (cantidad, unidad)

    if origen == "ninguna":
        match = insumo_match.mejor_match(producto.description, candidatos)
        if match is not None:
            insumo_id, score = match
            confianza = Decimal(str(score))
            origen = "similitud"
        else:
            nombre_propuesto = _nombre_sugerido(producto.description)

    # The unit of use to resolve the factor against: an existing insumo's
    # own, or the dimension the description itself implies.
    if insumo_id is not None:
        insumo = db.get(Insumo, insumo_id)
        if insumo is not None:
            unidad_base_probable = insumo.unidad_base
    else:
        presentacion = llm_presentacion or insumo_match.parse_presentacion(producto.description)
        if presentacion is not None:
            from .units import CANONICAL, dimension_of

            unidad_base_probable = CANONICAL[dimension_of(presentacion[1])]

    if llm_presentacion is not None:
        from .units import UnknownUnit, convert

        try:
            factor = convert(llm_presentacion[0], llm_presentacion[1], unidad_base_probable)
            factor_origen = "llm_descripcion"
        except (UnknownUnit, ValueError):
            factor, factor_origen = None, "sin_resolver"
    else:
        factor, factor_origen = insumo_match.resolve_factor(
            descripcion=producto.description,
            unidad_base=unidad_base_probable,
            unidad_medida=producto.unidad_medida,
            categoria_slug=producto.categoria_slug,
        )

    insumo_nombre = None
    if insumo_id is not None:
        insumo_nombre = next((n for i, _s, n in insumos if i == insumo_id), None)

    return Sugerencia(
        producto=producto,
        compras=compras,
        insumo_id=insumo_id,
        insumo_nombre=insumo_nombre,
        nombre_propuesto=nombre_propuesto,
        unidad_sugerida=unidad_base_probable,
        confianza=confianza,
        origen=origen,
        factor=factor,
        factor_origen=factor_origen,
    )


def _nombre_sugerido(descripcion: str) -> str:
    """A readable insumo name proposed from a description: the words that
    say what it is, capitalized. "FILETE DE PECHUGA DESHUESADA MFS 1/5KG"
    becomes "Filete pechuga deshuesada" -- imperfect, and editable in the
    form, which is the point.
    """
    tokens = insumo_match.tokens_significativos(descripcion)[:4]
    return " ".join(tokens).capitalize() if tokens else descripcion[:60].capitalize()


def confirmar(
    db: Session,
    *,
    catalog_product_id: int,
    insumo_id: int | None = None,
    nombre_nuevo: str = "",
    unidad_base: str = "g",
    merma_pct: str = "",
    metodo_costeo: str = "",
    ventana_dias: str = "",
    factor: Decimal | None = None,
    factor_origen: str = "manual",
) -> InsumoFuente:
    """Attach one catalog product to an insumo, creating the insumo if this
    is a "nuevo" confirmation.

    Always writes `status='confirmed'` and `mapped_by='human'`: this
    function is only ever reached from a person clicking confirm. The
    suggestion path never calls it.

    **A new insumo is built through `insumos.validate`, not by hand.** It
    used to be constructed straight from the form here, which quietly meant
    the mapping queue was the one door into the table that skipped every
    rule the insumo form enforces -- a merma of 995 or a made-up método
    would have gone in unchallenged. Now the two doors share the rules, and
    the queue raises the same field-keyed `ValidationError` the form does.
    """
    if insumo_id is None:
        campos = validar_insumo(
            nombre=nombre_nuevo,
            unidad_base=unidad_base,
            merma_pct=merma_pct,
            metodo_costeo=metodo_costeo,
            ventana_dias=ventana_dias,
        )
        insumo = Insumo(slug=unique_slug(db, campos["nombre"]), **campos)
        db.add(insumo)
        db.flush()
        insumo_id = insumo.id

    fuente = db.scalar(
        select(InsumoFuente).where(InsumoFuente.catalog_product_id == catalog_product_id)
    )
    if fuente is None:
        fuente = InsumoFuente(catalog_product_id=catalog_product_id)
        db.add(fuente)

    fuente.insumo_id = insumo_id
    fuente.factor_conversion = factor
    fuente.factor_origen = factor_origen if factor is not None else "sin_resolver"
    fuente.status = "confirmed"
    fuente.mapped_by = "human"
    fuente.activo = True
    fuente.confirmed_at = dt.datetime.now(dt.UTC)
    db.commit()
    # This product has left the pending set, so its cached look-alikes are
    # stale in both directions -- and a stale pair is not harmless here: it
    # would have the queue offer, and pre-tick, a product that is already
    # mapped. Pure cache, so dropping it costs one grouping pass.
    olvidar_parecidos(db, [catalog_product_id])
    return fuente


def rechazar(db: Session, *, catalog_product_id: int, insumo_id: int) -> None:
    """Remember that this product is *not* that insumo, so the suggestion is
    not offered again on the next pass through the queue.
    """
    ya = db.scalar(
        select(InsumoMapeoRechazo).where(
            InsumoMapeoRechazo.catalog_product_id == catalog_product_id,
            InsumoMapeoRechazo.insumo_id == insumo_id,
        )
    )
    if ya is None:
        db.add(
            InsumoMapeoRechazo(catalog_product_id=catalog_product_id, insumo_id=insumo_id)
        )
        db.commit()


def contar_pendientes(db: Session) -> int:
    """How many rows the default queue holds -- the nav badge's number.

    A dedicated `COUNT` rather than `len(pendientes(...))`: this runs on
    every page render in the app, and materialising four hundred ORM
    objects to count them would be a real cost paid on screens that have
    nothing to do with mapping.

    Shares `_filtros` with the queue itself, so the badge can never promise
    work the screen does not then show -- which is what would happen the
    first time the gate dismissed four hundred rows and only one of the two
    knew about it.
    """
    _alc = alcance(db)
    return db.scalar(
        select(func.count())
        .select_from(CatalogProduct)
        # The SAVED scope, not a hardcoded one. The badge and the grouping
        # pass both have to see what the queue sees: a badge counting rows
        # the screen filters out is a badge that never clears, and a
        # grouping pass over a wider set spends calls on products nobody
        # is going to be shown.
        .where(*_filtros(db, "", _alc.solo_receta, _alc.solo_reciente,
                         dias_reciente=_alc.dias_reciente))
    ) or 0


def resumen(db: Session) -> dict:
    """Counts for the queue's header and the nav badge."""
    total = db.scalar(
        select(func.count()).select_from(CatalogProduct).where(CatalogProduct.ignored.is_(False))
    ) or 0
    mapeados = len(_mapeados(db))
    descartados = len(_descartados(db))
    return {
        "total": total,
        "mapeados": mapeados,
        "descartados": descartados,
        # What is genuinely left to decide: neither mapped nor dismissed.
        "pendientes": total - mapeados - descartados,
        "en_cola": contar_pendientes(db),
        "llm": llm_insumo.disponible(),
    }


# ── el portón: descartar en bloque (DESIGN.md §3.7) ───────────────────────
# A large share of a 1 420-product catalog will never be an ingredient, and
# a queue that makes the operator say so one row at a time is a page that
# punishes them for having work to do. The gate takes the same filters the
# queue uses and dismisses the whole matching set in one action.


def _descartados(db: Session) -> set[int]:
    return set(db.scalars(select(CatalogDescarte.catalog_product_id)))


def contar_filtro(
    db: Session,
    *,
    q: str = "",
    solo_receta: bool = True,
    solo_reciente: bool = True,
    categorias: Sequence[str] = (),
    dias_reciente: int = DIAS_RECIENTE,
) -> int:
    """How many pending products the current filters match.

    The number goes on the gate's own button, because "descartar" without a
    count is an action whose size is invisible until after it has been
    taken. Counted rather than listed: the answer can be four hundred.
    """
    return db.scalar(
        select(func.count())
        .select_from(CatalogProduct)
        .where(*_filtros(db, q, solo_receta, solo_reciente, categorias,
                         dias_reciente))
    ) or 0


def facetas_categoria(
    db: Session,
    *,
    q: str = "",
    solo_receta: bool = True,
    solo_reciente: bool = True,
    dias_reciente: int = DIAS_RECIENTE,
) -> list[dict]:
    """Every category with something still pending, and how much.

    **Counted WITHOUT the category filter applied**, which is the whole
    point of a facet: ticking «Verduras» must not empty the list it was
    ticked from, or a second category can never be added. The other three
    filters DO apply, so the numbers are the ones the operator would
    actually get.

    The count is what makes this usable at all -- «Verduras 83» says where
    an afternoon goes, and a category showing 0 is simply not offered. Names
    are left to the template: `categoria_nombre` already owns the
    slug-to-name map and its cache, and UI text does not belong in a
    service.
    """
    slug = func.coalesce(CatalogProduct.categoria_slug, "")
    filas = db.execute(
        select(slug.label("slug"), func.count().label("n"))
        .where(*_filtros(db, q, solo_receta, solo_reciente,
                         dias_reciente=dias_reciente))
        .group_by(slug)
    ).all()
    # Busiest first, for the same reason the queue itself is: that is the
    # order in which deciding pays off. The uncategorised bucket sorts last
    # regardless of size -- it is a fallback, not a category.
    filas = sorted(filas, key=lambda f: (f.slug == "", -f.n, f.slug))
    return [{"slug": f.slug, "n": f.n} for f in filas]


def descartar_filtro(
    db: Session,
    *,
    q: str = "",
    solo_receta: bool = True,
    solo_reciente: bool = True,
    categorias: Sequence[str] = (),
    dias_reciente: int = DIAS_RECIENTE,
) -> tuple[str, int]:
    """Mark every pending product matching the filters as "not an insumo".

    Returns `(lote, cuántos)`. The lote is what makes it undoable as one
    action -- four hundred rows dismissed by one click should be
    recoverable by one click, not by hunting each row down.

    Nothing here touches `CatalogProduct`: the mirror is derived and
    disposable (non-negotiable #3) and `ignored` is upstream's own column,
    rewritten by the next sync.
    """
    ids = list(
        db.scalars(
            select(CatalogProduct.id).where(
                *_filtros(db, q, solo_receta, solo_reciente, categorias,
                          dias_reciente)
            )
        )
    )
    if not ids:
        return "", 0
    lote = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S%f")
    db.add_all(
        CatalogDescarte(catalog_product_id=pid, lote=lote) for pid in ids
    )
    db.commit()
    # Same reason as `confirmar`: these are out of the queue now, so their
    # cached pairs would offer rows nobody can act on. An undo brings the
    # products back without their grouping, and the next sync recomputes it.
    olvidar_parecidos(db, ids)
    return lote, len(ids)


def deshacer_descarte(db: Session, lote: str) -> int:
    """Undo one bulk dismissal. Returns how many rows came back."""
    if not lote:
        return 0
    filas = list(
        db.scalars(select(CatalogDescarte).where(CatalogDescarte.lote == lote))
    )
    for fila in filas:
        db.delete(fila)
    db.commit()
    return len(filas)


def restaurar_todo(db: Session) -> int:
    """Bring every dismissed product back into the queue."""
    filas = list(db.scalars(select(CatalogDescarte)))
    for fila in filas:
        db.delete(fila)
    db.commit()
    return len(filas)


# ── lo que hace la cola tolerable a 1 435 productos ───────────────────────


def cobertura(db: Session) -> dict:
    """How much of the kitchen's actual buying is already mapped.

    **Rows are the wrong denominator on this screen.** "0 de 1 435" reads as
    hopeless and is the number the queue used to lead with; it is also the
    number that matters least. Purchases are steeply concentrated -- on the
    live catalog the top 50 products are **49% of all purchase lines**, the
    top 100 are 75% and the top 200 are 94% -- so an operator who maps the
    busiest fifty has costed half the kitchen and can stop whenever the
    tail stops being worth it.

    This is the figure that says when to stop, which a row count never can.
    """
    corte = dt.date.today() - dt.timedelta(days=DIAS_RECIENTE)
    lineas = (
        select(
            CatalogPricePoint.catalog_product_id.label("pid"),
            func.count().label("n"),
        )
        .group_by(CatalogPricePoint.catalog_product_id)
        .subquery()
    )
    base = (
        select(func.coalesce(func.sum(lineas.c.n), 0))
        .select_from(CatalogProduct)
        .join(lineas, lineas.c.pid == CatalogProduct.id)
        .where(
            CatalogProduct.ignored.is_(False),
            CatalogProduct.last_purchase_at >= corte,
        )
    )
    total = db.scalar(base) or 0
    ya = _mapeados(db)
    hechas = db.scalar(base.where(CatalogProduct.id.in_(ya))) if ya else 0
    return {
        "compras_total": total,
        "compras_mapeadas": hechas or 0,
        "pct": round(100 * (hechas or 0) / total, 1) if total else 0.0,
    }


@dataclass
class Parecido:
    """Another *pending* product that looks like the same insumo."""

    producto: CatalogProduct
    compras: int
    score: float
    # Pre-ticked only when the reading is near-certain. Everything weaker is
    # shown unticked with its score, because the operator deciding is the
    # entire safety mechanism (non-negotiable #4).
    sugerido: bool
    # Which rule produced this row -- "llm" or "similitud". On screen as a
    # word, not as debug metadata: an operator ticking a box for a bulk
    # confirm is entitled to know whether Claude read the pair or whether
    # two strings merely overlapped. `score` stays the deterministic
    # similarity either way, so the two proposers can be compared.
    origen: str = "similitud"


# Above this, two descriptions are treated as confidently the same insumo
# and the checkbox arrives ticked. Chosen against the live catalog: the
# DONA/DOÑA pair and the two LEPRINO pack sizes both score 1.0, the
# 16 MESES/16M parmesano pair 0.72, while AZUCAR BLANCO vs AZUCAR CRUDO,
# SALSA DE CHOCOLATE vs SALSA DE SOYA and PAPA AMARILLA vs PAPA GAJO are
# refused outright by `similitud`'s conflict guard and never appear here.
UMBRAL_SUGERIDO = 0.70


def _familia(producto: CatalogProduct) -> str:
    """The top-level category family a product belongs to, or "" for none."""
    slug = producto.categoria_slug or ""
    return slug.split("_", 1)[0]


def _misma_familia(a: CatalogProduct, b: CatalogProduct) -> bool:
    """Whether two products could be the same insumo at all.

    **`empaques` never groups with food, in either direction.** A bag is
    not an ingredient, and once packaging joined the queue (D52) the two
    started colliding on shared adjectives: `BOLSAS VIKINGO GRANDE` scores
    0.45 against `PEPINOS GRANDE` because "grande" is all they have left --
    the noise set strips "bolsas", which is the one word that says what the
    thing actually is.

    Only this one family is fenced off, not every family from every other.
    Upstream miscategorises often enough that a juice sitting in
    `alimentos_*` instead of `bebidas_*` is ordinary, and refusing to group
    those would cost a real merge to prevent a merge nobody was going to
    make anyway. Packaging is the case where the categories are reliable
    and the collision is real.
    """
    fa, fb = _familia(a), _familia(b)
    return (fa == "empaques") == (fb == "empaques")


def parecidos(
    db: Session,
    producto: CatalogProduct,
    *,
    q: str = "",
    solo_receta: bool = True,
    solo_reciente: bool = True,
    dias_reciente: int = DIAS_RECIENTE,
    limite: int = 6,
) -> list[Parecido]:
    """Other pending products that are probably the same insumo as this one.

    The live catalog is full of them, and they are the single thing that
    makes a 1 435-row queue tolerable: `QUESO MOZZARELLA RALLADO LEPRINO
    1/15 LB` and `... 1/2,27 KG` are one insumo bought in two pack sizes,
    and `AZUCAR BLANCO DONA MARIA` and `AZUCAR BLANCO DOÑA MARIA` differ by
    a tilde. Deciding those separately, dozens of cards apart, is how a
    duplicate insumo gets created -- and a duplicate insumo splits one
    ingredient's price history in half without anything looking wrong.

    **Claude proposes the grouping; `insumo_match.similitud` is the
    fallback.** The two are not equally good at this and the live catalog
    shows it in both directions: token overlap scored `OSOBUCO` against
    `OSOBUCO PICADO, CAJAS 3KG APROX (P/KG)` at 0.32 and missed a real
    pair, while scoring `PEPINOS GRANDE` against `ZUQUINI (GRANDE)` at
    0.43 on a shared adjective. Reading which words carry the ingredient is
    the thing a model is actually better at.

    **The over-merge rule does not move.** `insumo_match.conflictivos`
    vetoes whatever the model proposes, so "PECHUGA CON HUESO is not
    Pechuga deshuesada" is still enforced in exactly one place regardless
    of who suggested the merge. `_misma_familia` vetoes on the same terms.

    With no API key -- or a timeout, or an answer that does not parse --
    this degrades to the similarity path silently and completely. The queue
    has always worked either way and says which; that does not change
    because the better half got better.
    """
    otros = db.execute(
        select(CatalogProduct, func.coalesce(func.count(CatalogPricePoint.id), 0))
        .join(
            CatalogPricePoint,
            CatalogPricePoint.catalog_product_id == CatalogProduct.id,
            isouter=True,
        )
        .where(*_filtros(db, q, solo_receta, solo_reciente,
                         dias_reciente=dias_reciente))
        .where(CatalogProduct.id != producto.id)
        .group_by(CatalogProduct.id)
    ).all()

    # Both proposers see the same candidates, and both are filtered by the
    # same two vetoes first. Ordering by purchase count is what makes the
    # `_MAX_CANDIDATOS` cut in `llm_insumo.agrupar` fall on the rows that
    # matter least if it ever bites.
    elegibles = [
        (otro, n)
        for otro, n in otros
        if _misma_familia(producto, otro)
        and not insumo_match.conflictivos(producto.description, otro.description)
    ]
    elegibles.sort(key=lambda par: -par[1])

    def _score(otro: CatalogProduct) -> float:
        return insumo_match.similitud(producto.description, otro.description)

    # The precomputed answer first. A `CatalogAgrupacion` row means this
    # anchor has been through the grouping pass, so an empty result is a
    # real "nothing looks like this" and NOT a reason to spend a call. The
    # pairs were computed over the unfiltered superset, so intersecting
    # them with `elegibles` is what applies the operator's current filters
    # -- which is why changing a checkbox costs nothing and never needs a
    # recompute.
    agrupado = db.get(CatalogAgrupacion, producto.id) is not None
    if agrupado:
        cacheados = dict(
            db.execute(
                select(CatalogParecido.miembro_id, CatalogParecido.certeza).where(
                    CatalogParecido.anchor_id == producto.id
                )
            ).all()
        )
        salida = [
            Parecido(
                producto=otro,
                compras=n,
                score=_score(otro),
                sugerido=cacheados[otro.id] == "seguro",
                origen="llm",
            )
            for otro, n in elegibles
            if otro.id in cacheados
        ]
        salida.sort(key=lambda p: (-p.sugerido, -p.score, -p.compras))
        return salida[:limite]

    # Not precomputed yet -- a product that arrived since the last sync, or
    # a batch that failed and will be retried. Answer live rather than
    # pretend it is unique.
    veredicto = llm_insumo.agrupar(
        producto.description, [(otro.id, otro.description) for otro, _ in elegibles]
    )

    if veredicto is not None:
        salida = [
            Parecido(
                producto=otro,
                compras=n,
                score=_score(otro),
                # Only "seguro" arrives ticked. A model that is merely
                # confident enough to mention a row is not confident enough
                # to pre-approve it for a bulk confirm.
                sugerido=veredicto[otro.id] == "seguro",
                origen="llm",
            )
            for otro, n in elegibles
            if otro.id in veredicto
        ]
    else:
        salida = [
            Parecido(
                producto=otro,
                compras=n,
                score=score,
                sugerido=score >= UMBRAL_SUGERIDO,
                origen="similitud",
            )
            for otro, n in elegibles
            if (score := _score(otro)) >= 0.35
        ]

    salida.sort(key=lambda p: (-p.sugerido, -p.score, -p.compras))
    return salida[:limite]



def confirmar_muchos(
    db: Session,
    *,
    catalog_product_ids: list[int],
    insumo_id: int | None = None,
    nombre_nuevo: str = "",
    unidad_base: str = "g",
    merma_pct: str = "",
    metodo_costeo: str = "",
    ventana_dias: str = "",
    factores: dict[int, Decimal | None] | None = None,
    factor_origen: str = "manual",
) -> int:
    """Attach several catalog products to ONE insumo in one action.

    The insumo is created once (if this is a "nuevo" confirmation) and every
    product is then confirmed against it, **each with its own factor** --
    which is the whole point: `1/15 LB` and `1/2,27 KG` are the same
    ingredient in two pack sizes, and giving them a shared factor would make
    one of them wrong by the ratio between the packs.

    Everything still goes through `confirmar`, so every row written here is
    `status='confirmed'`, `mapped_by='human'` (non-negotiable #4). This
    function is only ever reached from a person pressing a button.
    """
    factores = factores or {}
    hechos = 0
    for pid in catalog_product_ids:
        fuente = confirmar(
            db,
            catalog_product_id=pid,
            insumo_id=insumo_id,
            nombre_nuevo=nombre_nuevo,
            unidad_base=unidad_base,
            merma_pct=merma_pct,
            metodo_costeo=metodo_costeo,
            ventana_dias=ventana_dias,
            factor=factores.get(pid),
            factor_origen=factor_origen,
        )
        # After the first one the insumo exists; the rest join it rather
        # than each creating their own namesake. Merma and método go with
        # the insumo, not the fuente, so they are only read on that first
        # pass -- clearing the name is what makes the rest join it.
        insumo_id = fuente.insumo_id
        nombre_nuevo = ""
        hechos += 1
    return hechos


# ── la agrupación precomputada (DESIGN.md §3.7) ───────────────────────────
# Asking Claude "what looks like this?" on every card render is one billed
# call per page turn, paid again every time the operator pages back. The
# answer only changes when `catalog_sync` brings in new products, so it is
# computed once per product and read from the table afterwards.


def _anchors_pendientes(db: Session) -> list[CatalogProduct]:
    """The queue's products, in the queue's own order.

    **Exactly what `/insumos/mapeo` shows by default**: the allowlist
    families, bought inside `DIAS_RECIENTE`. Not the wider "every pending
    product in those families" set -- that is 1 075 rows against 405, and
    the extra 670 are products nobody is going to map, priced from purchases
    more than half a year old. Unticking «comprado en 180 días» reaches
    them, and those cards answer live instead, which is the graceful half of
    the trade.

    Busiest first, matching `pendientes`, because a budgeted pass should
    fill the cards the operator reaches first.
    """
    compras = (
        select(
            CatalogPricePoint.catalog_product_id.label("pid"),
            func.count().label("n"),
        )
        .group_by(CatalogPricePoint.catalog_product_id)
        .subquery()
    )
    _alc = alcance(db)
    filas = db.execute(
        select(CatalogProduct)
        .join(compras, compras.c.pid == CatalogProduct.id, isouter=True)
        # The SAVED scope, not a hardcoded one. The badge and the grouping
        # pass both have to see what the queue sees: a badge counting rows
        # the screen filters out is a badge that never clears, and a
        # grouping pass over a wider set spends calls on products nobody
        # is going to be shown.
        .where(*_filtros(db, "", _alc.solo_receta, _alc.solo_reciente,
                         dias_reciente=_alc.dias_reciente))
        .order_by(func.coalesce(compras.c.n, 0).desc(), CatalogProduct.description)
    ).scalars()
    return list(filas)


def precomputar_parecidos(
    db: Session,
    *,
    limite: int | None = None,
    solo_faltantes: bool = True,
    estado: "EstadoAgrupacion | None" = None,
) -> dict:
    """Fill `catalog_parecido` for the queue. Returns a small report.

    **One call per anchor, the same `llm_insumo.agrupar` the live path
    uses.** That is the point rather than an accident: a cached row and a
    freshly computed one come out of the same function against the same
    candidate list, so precomputing changes what the queue *costs* and not
    what it *offers*. Batching was tried and rejected -- it needs a sort to
    put look-alikes in the same batch, which silently caps recall at
    whatever that ordering happens to co-locate (96% of plausible pairs on
    this catalog), and it forces transitivity inside a batch.

    **Rows are written in both directions.** When A's pass finds B, both
    `(A,B)` and `(B,A)` are stored, so a product that arrives later
    backfills itself into the anchors that already ran without any of them
    being recomputed. That is what keeps a routine sync at one call per new
    product instead of a full re-pass.

    `limite` caps how many anchors one call of this does, and
    `solo_faltantes` skips anchors already carrying a `CatalogAgrupacion`
    row. Together they make the pass **resumable**: it commits per anchor,
    so an interrupted run keeps everything it earned and the next run picks
    up exactly where it stopped. That matters because the first pass is 405
    calls, and anything that long will be interrupted eventually.

    **Never raises for anything the model does.** An anchor whose call fails
    is left unmarked, so it is retried later and its card answers live in
    the meantime -- the same stance `catalog_sync` takes about upstream.
    """
    productos = _anchors_pendientes(db)
    if len(productos) < 2:
        return {"anchors": 0, "pares": 0, "restantes": 0, "fallidos": 0}

    ya = (
        set(db.scalars(select(CatalogAgrupacion.catalog_product_id)))
        if solo_faltantes
        else set()
    )
    objetivo = [p for p in productos if p.id not in ya]
    restantes = len(objetivo)
    if limite is not None:
        objetivo = objetivo[:limite]

    # The modal reads this while the thread writes it, so every touch is
    # under the lock -- including the total, which is what turns the
    # spinner from indeterminate dots into a percentage ring.
    if estado is not None:
        with _lock:
            estado.total = len(objetivo)

    pares = 0
    hechos = 0
    fallidos = 0

    for ancla in objetivo:
        # The same two vetoes the live path applies, applied before the
        # call so the model is never asked about a pair the app would
        # refuse anyway.
        elegibles = [
            otro
            for otro in productos
            if otro.id != ancla.id
            and _misma_familia(ancla, otro)
            and not insumo_match.conflictivos(ancla.description, otro.description)
        ]

        veredicto = llm_insumo.agrupar(
            ancla.description, [(o.id, o.description) for o in elegibles]
        )
        if veredicto is None:
            fallidos += 1
            if estado is not None:
                with _lock:
                    estado.fallidos = fallidos
            continue  # unmarked on purpose: retried, and live until then

        for miembro_id, certeza in veredicto.items():
            db.merge(
                CatalogParecido(
                    anchor_id=ancla.id, miembro_id=miembro_id, certeza=certeza
                )
            )
            db.merge(
                CatalogParecido(
                    anchor_id=miembro_id, miembro_id=ancla.id, certeza=certeza
                )
            )
            pares += 2

        db.merge(CatalogAgrupacion(catalog_product_id=ancla.id))
        hechos += 1
        # Per anchor, so an interrupted pass keeps what it earned.
        db.commit()
        if estado is not None:
            with _lock:
                estado.procesados = hechos
                estado.pares = pares

    return {
        "anchors": hechos,
        "pares": pares,
        "restantes": max(0, restantes - hechos),
        "fallidos": fallidos,
    }




# ── la agrupación en segundo plano ────────────────────────────────────────
# Ported from `restaurant-manager`'s `product_categories.start_recategorize`:
# module-level state behind a lock, a daemon thread with its own Session,
# and an htmx fragment that polls. Both apps are single-operator and
# single-instance, so one global "is something running" flag is enough --
# and the alternative here is a ten-minute HTTP request, which is how the
# first attempt at this died.


@dataclass
class EstadoAgrupacion:
    """What the modal renders. A copy is handed out, never the original."""

    running: bool = False
    total: int = 0
    procesados: int = 0
    pares: int = 0
    fallidos: int = 0
    error: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None


_estado = EstadoAgrupacion()
_lock = threading.Lock()


def estado_agrupacion() -> EstadoAgrupacion:
    with _lock:
        return replace(_estado)


def iniciar_agrupacion() -> bool:
    """Start the grouping pass on a background thread. False if one is
    already running.

    The guard is not ceremony: two passes over the same anchors would double
    the bill and race each other's writes to `catalog_parecido`.
    """
    with _lock:
        if _estado.running:
            return False
        _estado.running = True
        _estado.total = 0
        _estado.procesados = 0
        _estado.pares = 0
        _estado.fallidos = 0
        _estado.error = None
        _estado.started_at = dt.datetime.now(dt.UTC)
        _estado.finished_at = None

    def _correr() -> None:
        from ..db import SessionLocal  # local: avoids a db<->mapeo cycle at import

        db = SessionLocal()
        try:
            precomputar_parecidos(db, estado=_estado)
        except Exception as exc:  # noqa: BLE001 -- must still clear running, or the modal wedges
            log.exception("Agrupación en segundo plano falló")
            with _lock:
                _estado.error = f"Error inesperado: {exc}"
        finally:
            db.close()
            with _lock:
                _estado.running = False
                _estado.finished_at = dt.datetime.now(dt.UTC)

    threading.Thread(target=_correr, daemon=True).start()
    return True

def faltan_por_agrupar(db: Session) -> int:
    """How many queue products have never been through the grouping pass --
    what `/ajustes` shows so the backfill button can say what is left."""
    productos = _anchors_pendientes(db)
    ya = set(db.scalars(select(CatalogAgrupacion.catalog_product_id)))
    return sum(1 for p in productos if p.id not in ya)


def olvidar_parecidos(db: Session, product_ids: list[int]) -> None:
    """Drop the cached grouping for these products, in both directions.

    Called when a product leaves the pending set. The pairs are pure cache,
    so this is housekeeping rather than a decision -- but leaving them would
    have the queue offer a look-alike that is already mapped.
    """
    if not product_ids:
        return
    db.execute(
        delete(CatalogParecido).where(
            or_(
                CatalogParecido.anchor_id.in_(product_ids),
                CatalogParecido.miembro_id.in_(product_ids),
            )
        )
    )
    db.execute(
        delete(CatalogAgrupacion).where(
            CatalogAgrupacion.catalog_product_id.in_(product_ids)
        )
    )
    db.commit()


@dataclass
class CostoPreview:
    """What this product's own purchases would cost under the settings the
    operator currently has on the card.

    **This product's purchases only**, which is exactly right for a new
    insumo (it is the only source it will have) and deliberately partial
    when mapping onto an existing one, where the real cost unions every
    confirmed fuente. The template says which case it is -- a number that
    quietly means something narrower than the label claims is the failure
    this app is built to avoid.
    """

    valor: Decimal | None
    metodo: str
    ventana_dias: int
    merma_pct: Decimal
    unidad_base: str
    observaciones: int      # in the series, before windowing
    usadas: int             # what the method actually reduced
    descartadas: int        # no CRC price on file upstream
    precio_viejo: bool
    antiguedad_dias: int | None
    factor: Decimal | None


def costo_preview(
    db: Session,
    producto: CatalogProduct,
    *,
    factor: Decimal | None,
    unidad_base: str = "g",
    merma_pct: Decimal = Decimal(0),
    metodo: str = costing.DEFAULT_METHOD,
    ventana_dias: int = costing.DEFAULT_WINDOW_DAYS,
    hoy: dt.date | None = None,
) -> CostoPreview:
    """The cost this fuente would produce, computed the way costing does.

    Runs the real `costing.costo_unitario` over the real price points rather
    than doing its own arithmetic: a preview that agrees with the engine
    only by coincidence is worse than no preview, because it is believed.

    **No factor means no cost**, never a cost computed as if the factor were
    1 -- that is non-negotiable #7 and the single most expensive mistake
    available on this screen, since costing per package instead of per gram
    lands on a plausible-looking number.
    """
    puntos = list(
        db.scalars(
            select(CatalogPricePoint)
            .where(CatalogPricePoint.catalog_product_id == producto.id)
            .order_by(CatalogPricePoint.fecha)
        )
    )
    descartadas = sum(1 for p in puntos if p.precio_efectivo_normalizado_crc is None)

    if not factor or factor <= 0:
        return CostoPreview(
            valor=None, metodo=metodo, ventana_dias=ventana_dias,
            merma_pct=merma_pct, unidad_base=unidad_base,
            observaciones=len(puntos) - descartadas, usadas=0,
            descartadas=descartadas, precio_viejo=False, antiguedad_dias=None,
            factor=None,
        )

    serie = [
        costing.Observation(
            fecha=p.fecha,
            precio_por_unidad_base=p.precio_efectivo_normalizado_crc / factor,
            cantidad_unidad_base=(p.cantidad or Decimal(0)) * factor,
            catalog_price_point_id=p.id,
        )
        for p in puntos
        if p.precio_efectivo_normalizado_crc is not None
    ]

    resultado = costing.costo_unitario(
        serie, merma_pct=merma_pct, metodo=metodo,
        ventana_dias=ventana_dias, hoy=hoy,
    )
    return CostoPreview(
        valor=resultado.valor,
        metodo=resultado.metodo,
        ventana_dias=resultado.ventana_dias,
        merma_pct=merma_pct,
        unidad_base=unidad_base,
        observaciones=len(serie),
        usadas=resultado.observaciones_usadas,
        descartadas=descartadas,
        precio_viejo=resultado.precio_viejo,
        antiguedad_dias=resultado.antiguedad_dias,
        factor=factor,
    )
