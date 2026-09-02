"""Menú (platos). Phase 0 stub: the nav and the shell are real so the app
boots and the conventions are set; the list, the builder and the costing
wiring are Phase 1 steps 1.5-1.6.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import templates

router = APIRouter()


@router.get("/recetas", response_class=HTMLResponse)
def list_recetas(request: Request):
    return templates.TemplateResponse(request, "recetas/list.html", {"recetas": []})
