"""`/buscar` -- búsqueda global (DESIGN.md §3.8).

One route, two shapes, same as every other route in this app (D35): the
results fragment when htmx asked, a whole page when it did not. The whole
page is what makes the feature exist with JavaScript off -- a `<dialog>`
cannot be opened without script, so without the plain page `Ctrl-K` would
be the *only* way in, and search would join drag as something the tablet
and the no-JS path simply do not have.

Registered before `insumos.py` for the same reason `mapeo.py` is: nothing
here collides today, but the file order in `main.py` is load-bearing and
this router belongs on the safe side of it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import busqueda as svc
from ..templating import templates

router = APIRouter()


@router.get("/buscar", response_class=HTMLResponse)
def buscar(request: Request, db: Session = Depends(get_db), q: str = ""):
    ctx = {"q": q, "hits": svc.buscar(db, q)}
    plantilla = (
        "busqueda/_resultados.html"
        if request.headers.get("hx-request")
        else "busqueda/index.html"
    )
    return templates.TemplateResponse(request, plantilla, ctx)
