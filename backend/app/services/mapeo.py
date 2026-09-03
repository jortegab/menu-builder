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
queue that re-offers what you already said no to stops being drained.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    CatalogPricePoint,
    CatalogProduct,
    Insumo,
    InsumoFuente,
    InsumoMapeoRechazo,
)
from . import insumo_match, llm_insumo
from .insumos import unique_slug

# Categories that are never a recipe ingredient. Excluded from the default
# queue view -- 415 products on the live catalog, and every one of them is
# a row the operator would have to skip past to reach the food.
CATEGORIAS_NO_INSUMO = frozenset({
    "empaques", "menaje", "limpieza", "higiene", "oficina", "utilidades",
})

DIAS_RECIENTE = 180


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


def pendientes(
    db: Session,
    *,
    q: str = "",
    solo_comida: bool = True,
    solo_reciente: bool = True,
    limite: int = 50,
) -> list[tuple[CatalogProduct, int]]:
    """Unmapped products as (producto, número de compras), busiest first.

    Ordered by purchase count rather than alphabetically because that is the
    order in which mapping pays off: the ingredient bought 98 times matters
    more than the one bought once, and a queue sorted by name buries it.
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
        .where(CatalogProduct.ignored.is_(False))
    )

    ya = _mapeados(db)
    if ya:
        stmt = stmt.where(CatalogProduct.id.not_in(ya))
    if solo_comida:
        stmt = stmt.where(
            func.coalesce(CatalogProduct.categoria_slug, "").not_in(CATEGORIAS_NO_INSUMO)
        )
    if solo_reciente:
        corte = dt.date.today() - dt.timedelta(days=DIAS_RECIENTE)
        stmt = stmt.where(CatalogProduct.last_purchase_at >= corte)
    if q.strip():
        stmt = stmt.where(func.lower(CatalogProduct.description).like(f"%{q.strip().lower()}%"))

    stmt = stmt.order_by(func.coalesce(compras.c.n, 0).desc(), CatalogProduct.description)
    return [(p, n) for p, n in db.execute(stmt.limit(limite)).all()]


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
    factor: Decimal | None = None,
    factor_origen: str = "manual",
) -> InsumoFuente:
    """Attach one catalog product to an insumo, creating the insumo if this
    is a "nuevo" confirmation.

    Always writes `status='confirmed'` and `mapped_by='human'`: this
    function is only ever reached from a person clicking confirm. The
    suggestion path never calls it.
    """
    if insumo_id is None:
        nombre = (nombre_nuevo or "").strip()
        if not nombre:
            raise ValueError("Falta el nombre del insumo nuevo.")
        insumo = Insumo(
            nombre=nombre, slug=unique_slug(db, nombre), unidad_base=unidad_base
        )
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
    """
    corte = dt.date.today() - dt.timedelta(days=DIAS_RECIENTE)
    stmt = (
        select(func.count())
        .select_from(CatalogProduct)
        .where(
            CatalogProduct.ignored.is_(False),
            CatalogProduct.last_purchase_at >= corte,
            func.coalesce(CatalogProduct.categoria_slug, "").not_in(CATEGORIAS_NO_INSUMO),
        )
    )
    ya = _mapeados(db)
    if ya:
        stmt = stmt.where(CatalogProduct.id.not_in(ya))
    return db.scalar(stmt) or 0


def resumen(db: Session) -> dict:
    """Counts for the queue's header and the nav badge."""
    total = db.scalar(
        select(func.count()).select_from(CatalogProduct).where(CatalogProduct.ignored.is_(False))
    ) or 0
    mapeados = len(_mapeados(db))
    return {
        "total": total,
        "mapeados": mapeados,
        "pendientes": total - mapeados,
        "en_cola": len(pendientes(db, limite=10_000)),
        "llm": llm_insumo.disponible(),
    }
