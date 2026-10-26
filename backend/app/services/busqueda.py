"""Búsqueda global: find a receta, a preparación or an insumo by name.

**What this app genuinely lacked was search at all** (DESIGN.md §3.8).
There was no way to find an insumo except by paging `/insumos`, and that
gets worse with every mapping confirmed -- the screen the operator uses
most is the one that degrades fastest as the app succeeds.

Deliberately *not* a command palette. An earlier draft pitched the full
Raycast idiom -- actions, "agregar «pechuga»" from inside the builder --
on the grounds that it is the single feature that most changes an app's
feel. For one operator, six screens and forty plates that is instrument
cosplay: the nav already reaches any screen in two clicks, so a palette of
*commands* would answer a question nobody has. This is a search box that
happens to be summoned by a keystroke, which is a much smaller thing and
the one that was actually missing.

It searches names only, and that is enough: the operator is looking for
something they already named.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import Insumo, Receta

# One screenful. A search that returns eighty rows is a list, and the app
# already has lists.
LIMITE = 8


@dataclass(frozen=True)
class Hit:
    """One result. `tipo` is the Spanish label the sheet prints, and `url`
    is where Enter lands -- resolved here rather than in the template so a
    route change is one edit."""

    nombre: str
    tipo: str
    url: str
    detalle: str = ""


def buscar(db: Session, q: str, *, limite: int = LIMITE) -> list[Hit]:
    """Recetas, preparaciones and insumos whose name contains `q`.

    Ordered platos → preparaciones → insumos, and *not* by relevance
    score. The operator searching «pechuga» in this app is far more often
    heading for the plate than for the ingredient, and a scored blend would
    reorder itself between keystrokes -- which is the one thing a list must
    not do when Enter is about to land on it.

    Archived rows are included and marked. "Where did that plate go?" is
    exactly the question search gets asked, and answering "no existe" when
    it is merely archived would send the operator looking in the wrong
    place.
    """
    aguja = q.strip().lower()
    if not aguja:
        return []
    patron = f"%{aguja}%"

    recetas = db.scalars(
        select(Receta)
        .where(func.lower(Receta.nombre).like(patron))
        .order_by(Receta.tipo, Receta.nombre)
        .limit(limite * 2)
    ).all()
    insumos = db.scalars(
        select(Insumo)
        .where(or_(func.lower(Insumo.nombre).like(patron)))
        .order_by(Insumo.nombre)
        .limit(limite)
    ).all()

    hits: list[Hit] = []
    for r in (x for x in recetas if x.tipo == "plato"):
        hits.append(
            Hit(
                nombre=r.nombre,
                tipo="Plato",
                url=f"/recetas/{r.id}",
                detalle=r.categoria_menu or ("archivado" if not r.activo else ""),
            )
        )
    for r in (x for x in recetas if x.tipo == "preparacion"):
        hits.append(
            Hit(
                nombre=r.nombre,
                tipo="Preparación",
                url=f"/recetas/{r.id}",
                detalle="archivada" if not r.activo else "",
            )
        )
    for i in insumos:
        hits.append(
            Hit(
                nombre=i.nombre,
                tipo="Insumo",
                url=f"/insumos/{i.id}",
                detalle=i.categoria_slug or ("archivado" if not i.activo else ""),
            )
        )
    return hits[:limite]
