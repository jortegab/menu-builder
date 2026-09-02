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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import CatalogPricePoint, CatalogProduct, CatalogSyncRun
from ..services import catalog_sync
from ..services.catalog_client import CatalogClient
from ..templating import templates

router = APIRouter()


def _panel(db: Session) -> dict:
    from sqlalchemy import func

    return {
        "productos": db.scalar(select(func.count()).select_from(CatalogProduct)) or 0,
        "precios": db.scalar(select(func.count()).select_from(CatalogPricePoint)) or 0,
        "as_of": catalog_sync.mirror_as_of(db),
        "watermark": catalog_sync.last_watermark(db),
        "runs": db.scalars(
            select(CatalogSyncRun).order_by(CatalogSyncRun.id.desc()).limit(5)
        ).all(),
        "upstream": settings.upstream_api_url,
    }


@router.get("/ajustes", response_class=HTMLResponse)
def ajustes(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "ajustes/index.html", _panel(db))


@router.post("/ajustes/sincronizar")
def sincronizar(db: Session = Depends(get_db), full: str = ""):
    with CatalogClient(
        settings.upstream_api_url, timeout_seconds=settings.upstream_timeout_seconds
    ) as client:
        catalog_sync.sync_catalog(db, client, full=bool(full))
    return RedirectResponse("/ajustes", status_code=303)
