"""Insumo master data: create, edit, list, archive.

The insumo is where the three judgments live that a supplier's product row
cannot make (PLAN.md §6.1, D7) -- what unit the kitchen measures it in, how
much is lost preparing it, and which price to believe. This module owns
validating and writing them; `insumo_costs.py` (step 1.4) will own reading
them back out as a cost.

Validation is deliberately **not** left to the database's CHECK
constraints. Those exist as the last line of defence and must stay, but a
constraint violation surfaces as an opaque `IntegrityError` at commit time,
long after the form data is gone -- the operator gets a 500 instead of
"la merma tiene que estar entre 0 y 95". So the rules are checked here,
where a message can name the field and say what to do.

`slugify` and `validate` are pure and take no Session, so the whole rule
set is testable without a database.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CatalogProduct, Insumo, InsumoFuente, RecetaLinea
from .costing import MAX_MERMA_PCT, METHODS
from .units import CANONICAL

# What a recipe can actually measure in. A subset of `units._UNITS`: an
# insumo is *used* in the canonical unit of its dimension, and a recipe line
# converts into it (§7.1). Offering "kg" as a unidad de uso would work
# arithmetically and then read badly on every recipe -- 0.18 kg of pechuga
# rather than 180 g.
UNIDADES_USO = tuple(CANONICAL.values())  # ("g", "ml", "unidad")

MAX_VENTANA_DIAS = 3650  # ten years; past this it is a typo, not a window


class ValidationError(ValueError):
    """Field-keyed messages for a form to render inline.

    `errors` maps field name -> Spanish message. Carrying the field is what
    lets the template put the message next to the input that caused it
    instead of in a banner at the top that says "revisá los datos".
    """

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def slugify(nombre: str) -> str:
    """A URL-safe slug from a Spanish name.

    Accents are folded rather than dropped, so "Pechuga deshuesada" and
    "Pechuga deshuesáda" do not become two different slugs over one typo.
    """
    folded = unicodedata.normalize("NFKD", nombre.strip().lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return slug or "insumo"


def unique_slug(db: Session, nombre: str, *, exclude_id: int | None = None) -> str:
    """`slugify` plus a numeric suffix if that slug is taken.

    Two insumos legitimately share a name often enough to matter -- "Salsa"
    from two suppliers, "Queso" in two presentations -- and failing the save
    with "ese nombre ya existe" would be wrong: the *name* is not the
    identity, the row is.
    """
    base = slugify(nombre)
    candidate, n = base, 1
    while True:
        stmt = select(Insumo.id).where(Insumo.slug == candidate)
        if exclude_id is not None:
            stmt = stmt.where(Insumo.id != exclude_id)
        if db.scalar(stmt) is None:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def _parse_decimal(raw: str) -> Decimal | None:
    """A number typed by a person. Accepts "12", "12.5" and "12,5" -- the
    comma is the decimal separator in Costa Rica and a form that rejects it
    is a form fighting its user.
    """
    text = (raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(text) from None


def validate(
    *,
    nombre: str,
    unidad_uso: str,
    merma_pct: str = "",
    metodo_costeo: str = "",
    ventana_dias: str = "",
    categoria_slug: str = "",
    notas: str = "",
) -> dict:
    """Normalized field values, or `ValidationError` with per-field messages.

    Empty `metodo_costeo`/`ventana_dias` are **not** errors: they mean "use
    the app default", stored as NULL so an insumo that has never been given
    an opinion follows the global setting as it changes, rather than being
    frozen at whatever the default was the day it was created (models.py's
    own note on those columns).
    """
    errors: dict[str, str] = {}

    nombre = (nombre or "").strip()
    if not nombre:
        errors["nombre"] = "Poné un nombre."
    elif len(nombre) > 200:
        errors["nombre"] = "El nombre no puede pasar de 200 caracteres."

    if unidad_uso not in UNIDADES_USO:
        errors["unidad_uso"] = "Elegí una unidad de uso."

    merma = Decimal(0)
    try:
        parsed = _parse_decimal(merma_pct)
        merma = Decimal(0) if parsed is None else parsed
        if merma < 0:
            errors["merma_pct"] = "La merma no puede ser negativa."
        elif merma >= MAX_MERMA_PCT:
            # The divisor's asymptote turns a fat-fingered 995 into an
            # astronomically expensive plate that still looks like a number.
            errors["merma_pct"] = (
                f"La merma tiene que ser menor a {MAX_MERMA_PCT}%. "
                "Si de verdad se pierde casi todo, revisá la unidad de uso."
            )
    except ValueError:
        errors["merma_pct"] = "La merma tiene que ser un número, por ejemplo 12 o 12,5."

    metodo = (metodo_costeo or "").strip() or None
    if metodo is not None and metodo not in METHODS:
        errors["metodo_costeo"] = "Elegí un método de la lista."

    ventana: int | None = None
    raw_ventana = (ventana_dias or "").strip()
    if raw_ventana:
        try:
            ventana = int(raw_ventana)
        except ValueError:
            errors["ventana_dias"] = "La ventana tiene que ser un número entero de días."
        else:
            if ventana < 1:
                errors["ventana_dias"] = "La ventana tiene que ser de al menos 1 día."
            elif ventana > MAX_VENTANA_DIAS:
                errors["ventana_dias"] = (
                    f"La ventana no puede pasar de {MAX_VENTANA_DIAS} días."
                )

    if errors:
        raise ValidationError(errors)

    return {
        "nombre": nombre,
        "unidad_uso": unidad_uso,
        "merma_pct": merma,
        "metodo_costeo": metodo,
        "ventana_dias": ventana,
        "categoria_slug": (categoria_slug or "").strip() or None,
        "notas": (notas or "").strip() or None,
    }


def create(db: Session, **form) -> Insumo:
    fields = validate(**form)
    insumo = Insumo(slug=unique_slug(db, fields["nombre"]), **fields)
    db.add(insumo)
    db.commit()
    return insumo


def update(db: Session, insumo: Insumo, **form) -> Insumo:
    fields = validate(**form)
    # The slug follows the name, but only when the name actually changed --
    # re-slugging on every save would hand out a new URL for an edit that
    # only touched the merma.
    if fields["nombre"] != insumo.nombre:
        insumo.slug = unique_slug(db, fields["nombre"], exclude_id=insumo.id)
    for key, value in fields.items():
        setattr(insumo, key, value)
    db.commit()
    return insumo


def set_activo(db: Session, insumo: Insumo, activo: bool) -> Insumo:
    """Archive or restore. Never delete.

    An insumo can be referenced by a `receta_linea`, and deleting one would
    either orphan a recipe or silently change what a plate costs. Archiving
    keeps history intact and takes it out of the pickers.
    """
    insumo.activo = activo
    db.commit()
    return insumo


def usage_count(db: Session, insumo: Insumo) -> int:
    """How many recipe lines use this insumo -- the "usado en N recetas"
    link, and the reason an archive is a warning rather than a silent flip.
    """
    return db.scalar(
        select(func.count()).select_from(RecetaLinea).where(RecetaLinea.insumo_id == insumo.id)
    ) or 0


def fuente_counts(db: Session, insumo_ids: list[int]) -> dict[int, int]:
    """{insumo_id: confirmed source count} for a whole list in one query.

    Only `confirmed` sources are counted, because only those feed a price
    series (non-negotiable #4) -- a suggestion pending review is not yet a
    reason to believe an insumo has a price.
    """
    if not insumo_ids:
        return {}
    rows = db.execute(
        select(InsumoFuente.insumo_id, func.count())
        .where(
            InsumoFuente.insumo_id.in_(insumo_ids),
            InsumoFuente.status == "confirmed",
        )
        .group_by(InsumoFuente.insumo_id)
    ).all()
    return {insumo_id: n for insumo_id, n in rows}


def listar(db: Session, *, q: str = "", activo: bool | None = True) -> list[Insumo]:
    stmt = select(Insumo)
    if activo is not None:
        stmt = stmt.where(Insumo.activo == activo)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(Insumo.nombre).like(like))
    return list(db.scalars(stmt.order_by(Insumo.nombre)))


def categorias_disponibles(db: Session) -> list[str]:
    """Category slugs actually present in the mirrored catalog.

    Read from the mirror rather than hardcoded so this app never carries a
    second copy of upstream's taxonomy -- which just changed underneath us
    (alimentos_* became insumos_*) and would have gone stale immediately.
    """
    return [
        slug
        for slug in db.scalars(
            select(CatalogProduct.categoria_slug)
            .where(CatalogProduct.categoria_slug.is_not(None))
            .distinct()
            .order_by(CatalogProduct.categoria_slug)
        )
    ]
