"""Ajustes -- app configuration and the catalog sync's own control panel.

The sync runs **synchronously**, in the request. That is a deliberate
simplification for a single-operator app on the restaurant's own machine,
not an oversight: an incremental pull is ~2s against the real catalog, and
a background job would need a worker, a queue and a way to report failure
back to a page nobody is looking at. The full rebuild is slower (~45s on
1,420 products) and is a separate button that says so.

Sync failure is not an error page. `catalog_sync` records the failed run
and returns; this renders it as a red line on the panel and the mirror
keeps serving whatever it already had.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import (
    CatalogAgrupacion,
    CatalogPricePoint,
    CatalogProduct,
    CatalogSyncRun,
    RecetaCostoHistorico,
)
from ..services import catalog_sync, mapeo, receta_historico
from ..services.catalog_client import CatalogClient
from ..templating import templates

router = APIRouter()

# How many anchors one sync may group. Ten calls is ~15 seconds on top of a
# two-second sync -- noticeable, bounded, and enough to absorb a normal
# day's new products without ever leaving work for the button.
PRESUPUESTO_SYNC = 10


def _panel(db: Session) -> dict:
    return {
        "productos": db.scalar(select(func.count()).select_from(CatalogProduct)) or 0,
        "precios": db.scalar(select(func.count()).select_from(CatalogPricePoint)) or 0,
        "as_of": catalog_sync.mirror_as_of(db),
        "watermark": catalog_sync.last_watermark(db),
        "runs": db.scalars(
            select(CatalogSyncRun).order_by(CatalogSyncRun.id.desc()).limit(5)
        ).all(),
        "upstream": settings.upstream_api_url,
        "historico_filas": db.scalar(
            select(func.count()).select_from(RecetaCostoHistorico)
        ) or 0,
        "historico_dias": db.scalar(
            select(func.count(func.distinct(RecetaCostoHistorico.fecha)))
        ) or 0,
        "agrupados": db.scalar(select(func.count()).select_from(CatalogAgrupacion)) or 0,
        "faltan_agrupar": mapeo.faltan_por_agrupar(db),
        # What the mapping queue is scoped to. It used to be two checkboxes
        # on the queue screen; it is app policy, so it lives here and the
        # queue only reports it.
        "alcance": mapeo.alcance(db),
        # The same count the nav badge shows, and deliberately so: this
        # panel is where the badge's size is actually decided.
        "pendientes_alcance": mapeo.contar_pendientes(db),
    }


@router.get("/ajustes", response_class=HTMLResponse)
def ajustes(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "ajustes/index.html", _panel(db))


@router.post("/ajustes/mapeo")
def alcance_mapeo(
    db: Session = Depends(get_db),
    solo_receta: str = Form(""),
    dias_reciente: str = Form(""),
):
    """Save what the mapping queue is scoped to.

    An unchecked checkbox posts nothing at all, so the empty string IS the
    off state here -- and unlike the flags this replaced, that reading is
    safe: this form is a settings form nobody submits by accident, and the
    bulk dismissal it governs is two screens away behind its own count.

    A days field that will not parse falls back to the current value rather
    than to a default. Silently widening the queue because someone typed
    «180 dias» is the one wrong answer available here.
    """
    actual = mapeo.alcance(db)
    try:
        dias = int((dias_reciente or "").strip())
    except ValueError:
        dias = actual.dias_reciente
    mapeo.guardar_alcance(db, solo_receta=solo_receta == "1", dias_reciente=dias)
    return RedirectResponse("/ajustes", status_code=303)


@router.post("/ajustes/sincronizar")
def sincronizar(db: Session = Depends(get_db), full: str = ""):
    with CatalogClient(
        settings.upstream_api_url, timeout_seconds=settings.upstream_timeout_seconds
    ) as client:
        run = catalog_sync.sync_catalog(db, client, full=bool(full))
    # The snapshot rides the sync because a sync is *when a cost can have
    # moved* -- there is nothing else in this app that changes a price. It
    # runs only on a successful pull: recording today's cost against a
    # mirror that failed to refresh would put a point on the chart that
    # says "nothing changed" when the truth is "nothing could be read".
    if run.status == "ok":
        receta_historico.instantanea(db)
        # And the grouping, for the same reason: a sync is the only thing
        # that can bring in a product whose look-alikes are not yet known.
        #
        # **Budgeted, and that is not a detail.** One call per anchor is
        # right for a handful of new products and catastrophic for a whole
        # catalog: 405 anchors is ~10 minutes, inside a request that is
        # supposed to take two seconds. `PRESUPUESTO_SYNC` keeps a routine
        # sync bounded whatever arrives, and the rest is left for the
        # «Agrupar» button, which is resumable. A sync that quietly turns
        # into a ten-minute request is exactly the failure this app should
        # not have.
        mapeo.precomputar_parecidos(db, limite=PRESUPUESTO_SYNC)
    return RedirectResponse("/ajustes", status_code=303)

@router.post("/ajustes/agrupar", response_class=HTMLResponse)
def agrupar(request: Request, db: Session = Depends(get_db)):
    """Start the grouping pass and answer with the modal that watches it.

    **A background thread, not this request.** One call per anchor over 405
    products is roughly ten minutes; served inline it is a request nothing
    survives -- the first attempt at this ran 636 seconds and was killed,
    losing every call it had paid for. Same shape as `restaurant-manager`'s
    «Recategorizar catálogo»: module-level state behind a lock, a daemon
    thread with its own Session, and a fragment that polls.

    Starting it twice is refused rather than queued: two passes over the
    same anchors double the bill and race each other's writes.
    """
    mapeo.iniciar_agrupacion()
    return templates.TemplateResponse(
        request, "ajustes/_agrupar_modal.html", {"estado": mapeo.estado_agrupacion()}
    )


@router.get("/ajustes/agrupar/estado", response_class=HTMLResponse)
def agrupar_estado(request: Request, target: str | None = None):
    """The polled fragment. `target=modal` is what turns on «Cerrar»."""
    return templates.TemplateResponse(
        request,
        "ajustes/_agrupar_estado.html",
        {"estado": mapeo.estado_agrupacion(), "poll_target": target},
    )


@router.post("/ajustes/historico")
def historico(db: Session = Depends(get_db), backfill: str = ""):
    """Store today's cost for every receta, or seed the past.

    Two buttons, deliberately separate: one records what things cost today,
    the other fills days nobody was here for and marks every point it
    writes as an estimate (D32).
    """
    if backfill:
        receta_historico.backfill(db)
    else:
        receta_historico.instantanea(db)
    return RedirectResponse("/ajustes", status_code=303)
