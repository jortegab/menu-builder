"""The four steps a new install has to walk, and whether each is done.

DESIGN.md §3.10. The empty-state prose in `insumos/list.html` is genuinely
good -- "un insumo es lo que usa la cocina: «Pechuga deshuesada», no
«filete de pechuga deshuesada de Aviocal»" -- and it was stranded at the
bottom of an empty card, on a screen that is only reached once the
operator already knows to go there. What was missing was the shape of the whole thing:
**sincronizar → mapear → crear un insumo → armar la primera receta.**

**Every step is evaluated from data the app already has**, and that is the
design constraint, not a convenience. A checklist with its own progress
column would be a second source of truth about whether the catalog has
been synced -- one that can disagree with `catalog_sync_run`, and that
somebody would eventually have to reconcile. There is nothing to store
here, nothing to migrate under D15, and nothing that can go stale: the
answer is recomputed from the same rows the rest of the app reads.

It shows on `/recetas` and nowhere else, and it disappears for good once
the fourth step is done. A permanent onboarding card is furniture.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import CatalogSyncRun, Insumo, InsumoFuente, Receta


@dataclass(frozen=True)
class Paso:
    clave: str
    titulo: str
    detalle: str
    hecho: bool
    url: str
    accion: str


def pasos(db: Session) -> list[Paso]:
    """The four steps, in order, each with whether it is already done."""
    sincronizado = (
        db.scalar(
            select(func.count())
            .select_from(CatalogSyncRun)
            .where(CatalogSyncRun.status == "ok")
        )
        or 0
    ) > 0
    # A *confirmed* source, not merely a suggestion: nothing is active
    # until a person confirms it (non-negotiable #4), so a suggestion
    # sitting in the queue has not advanced this step at all.
    mapeado = (
        db.scalar(
            select(func.count())
            .select_from(InsumoFuente)
            .where(InsumoFuente.status == "confirmed")
        )
        or 0
    ) > 0
    hay_insumo = (db.scalar(select(func.count()).select_from(Insumo)) or 0) > 0
    hay_receta = (db.scalar(select(func.count()).select_from(Receta)) or 0) > 0

    return [
        Paso(
            clave="sync",
            titulo="Traer el catálogo",
            detalle=(
                "Los precios salen de restaurant-manager. Esta app no los "
                "recalcula: los lee."
            ),
            hecho=sincronizado,
            url="/ajustes",
            accion="Ir a Ajustes",
        ),
        Paso(
            clave="mapeo",
            titulo="Decir qué producto es qué insumo",
            detalle=(
                "«PECHUGA DESHUESADA MFS 1/5KG» del proveedor es «Pechuga "
                "deshuesada» de la cocina. Nada se activa solo: hasta que "
                "alguien confirma, ese producto no le pone precio a nada."
            ),
            hecho=mapeado,
            url="/insumos/mapeo",
            accion="Ir a Mapeo",
        ),
        Paso(
            clave="insumo",
            titulo="Tener un insumo",
            detalle=(
                "Un insumo es lo que usa la cocina: «Pechuga deshuesada», no "
                "«filete de pechuga deshuesada de Aviocal». El mapeo suele "
                "crear el primero solo."
            ),
            hecho=hay_insumo,
            url="/insumos/nuevo",
            accion="Crear un insumo",
        ),
        Paso(
            clave="receta",
            titulo="Armar la primera receta",
            detalle=(
                "Arrastrá insumos y preparaciones, y el costo se arma solo "
                "sobre la marcha."
            ),
            hecho=hay_receta,
            url="/recetas/nueva",
            accion="Crear un plato",
        ),
    ]


def mostrar(db: Session) -> list[Paso] | None:
    """The steps, or None once there is nothing left to teach.

    Gone for good when the last step is done -- not collapsed, not
    dismissible. A card that survives its own purpose is furniture, and
    this one has taught everything it knows by the time a receta exists.
    """
    filas = pasos(db)
    return None if all(p.hecho for p in filas) else filas
