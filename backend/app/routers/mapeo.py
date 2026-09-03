"""`/insumos/mapeo` -- the mapping queue (PLAN.md §8).

Suggestions are computed **on render**, not stored. They are cheap
deterministically, they go stale the moment an insumo is created or
renamed, and storing them would mean a second thing to invalidate. The LLM
path is the exception worth watching: it is one billed call per row shown,
so the page is paginated tightly and the queue is filtered by default.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import mapeo as svc
from ..services.insumos import UNIDADES_BASE
from ..templating import templates

router = APIRouter()

# One screenful. Deliberately small: with an API key configured this is
# also the number of billed calls one page render costs.
_POR_PAGINA = 25


@router.get("/insumos/mapeo", response_class=HTMLResponse)
def cola(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    comida: str = "1",
    reciente: str = "1",
):
    filas = svc.pendientes(
        db, q=q, solo_comida=comida == "1", solo_reciente=reciente == "1",
        limite=_POR_PAGINA,
    )
    return templates.TemplateResponse(
        request,
        "insumos/mapeo.html",
        {
            "sugerencias": [svc.sugerir(db, p, n) for p, n in filas],
            "resumen": svc.resumen(db),
            "q": q,
            "comida": comida,
            "reciente": reciente,
            "unidades": UNIDADES_BASE,
            "por_pagina": _POR_PAGINA,
        },
    )


def _factor(raw: str) -> Decimal | None:
    text = (raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value > 0 else None


@router.post("/insumos/mapeo/confirmar")
def confirmar(
    db: Session = Depends(get_db),
    catalog_product_id: int = Form(...),
    insumo_id: str = Form(""),
    nombre_nuevo: str = Form(""),
    unidad_base: str = Form("g"),
    factor: str = Form(""),
    factor_origen: str = Form("manual"),
):
    svc.confirmar(
        db,
        catalog_product_id=catalog_product_id,
        insumo_id=int(insumo_id) if insumo_id.strip() else None,
        nombre_nuevo=nombre_nuevo,
        unidad_base=unidad_base if unidad_base in UNIDADES_BASE else "g",
        factor=_factor(factor),
        factor_origen=factor_origen,
    )
    return RedirectResponse("/insumos/mapeo", status_code=303)


@router.post("/insumos/mapeo/rechazar")
def rechazar(
    db: Session = Depends(get_db),
    catalog_product_id: int = Form(...),
    insumo_id: int = Form(...),
):
    svc.rechazar(db, catalog_product_id=catalog_product_id, insumo_id=insumo_id)
    return RedirectResponse("/insumos/mapeo", status_code=303)
