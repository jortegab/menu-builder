"""Insumos -- the canonical ingredient layer (PLAN.md §10).

Step 1.2 is master data only: create, edit, archive, and the three
configuration fields. Sources (`/insumos/mapeo`, step 1.3) and the cost
each insumo resolves to (step 1.4) are not wired yet, and the screens say
so rather than showing a zero.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Insumo
from ..services import insumo_costs, insumos as svc
from ..templating import templates

router = APIRouter()


def _defaults() -> dict:
    """What "usar el valor por defecto" resolves to right now, so the form
    can show it as placeholder text instead of leaving the operator to
    guess what an empty field means.
    """
    return {
        "metodo_default": settings.costing_method_default,
        "ventana_default": settings.costing_window_days_default,
    }


@router.get("/insumos", response_class=HTMLResponse)
def list_insumos(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    estado: str = "activos",
):
    activo = {"activos": True, "archivados": False, "todos": None}.get(estado, True)
    rows = svc.listar(db, q=q, activo=activo)
    return templates.TemplateResponse(
        request,
        "insumos/list.html",
        {
            "insumos": rows,
            "fuentes": svc.fuente_counts(db, [i.id for i in rows]),
            "costos": insumo_costs.costos(db, rows),
            "q": q,
            "estado": estado,
            **_defaults(),
        },
    )


@router.get("/insumos/nuevo", response_class=HTMLResponse)
def nuevo(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "insumos/form.html",
        {
            "insumo": None,
            "valores": {"unidad_uso": "g", "merma_pct": "0"},
            "errors": {},
            "categorias": svc.categorias_disponibles(db),
            **_defaults(),
        },
    )


@router.post("/insumos")
def crear(
    request: Request,
    db: Session = Depends(get_db),
    nombre: str = Form(""),
    unidad_uso: str = Form(""),
    merma_pct: str = Form(""),
    metodo_costeo: str = Form(""),
    ventana_dias: str = Form(""),
    categoria_slug: str = Form(""),
    notas: str = Form(""),
):
    form = dict(
        nombre=nombre, unidad_uso=unidad_uso, merma_pct=merma_pct,
        metodo_costeo=metodo_costeo, ventana_dias=ventana_dias,
        categoria_slug=categoria_slug, notas=notas,
    )
    try:
        insumo = svc.create(db, **form)
    except svc.ValidationError as exc:
        # Re-render the form with what they typed still in it. Losing a
        # half-filled form to a validation error is how people stop
        # trusting a screen.
        return templates.TemplateResponse(
            request,
            "insumos/form.html",
            {
                "insumo": None, "valores": form, "errors": exc.errors,
                "categorias": svc.categorias_disponibles(db), **_defaults(),
            },
            status_code=422,
        )
    return RedirectResponse(f"/insumos/{insumo.id}", status_code=303)


@router.get("/insumos/{insumo_id}", response_class=HTMLResponse)
def detalle(insumo_id: int, request: Request, db: Session = Depends(get_db)):
    insumo = db.get(Insumo, insumo_id)
    if insumo is None:
        return RedirectResponse("/insumos", status_code=303)
    return templates.TemplateResponse(
        request,
        "insumos/detail.html",
        {
            "insumo": insumo,
            "fuentes": svc.fuente_counts(db, [insumo.id]).get(insumo.id, 0),
            "usos": svc.usage_count(db, insumo),
            "exp": insumo_costs.explicar(db, insumo),
            **_defaults(),
        },
    )


@router.get("/insumos/{insumo_id}/editar", response_class=HTMLResponse)
def editar(insumo_id: int, request: Request, db: Session = Depends(get_db)):
    insumo = db.get(Insumo, insumo_id)
    if insumo is None:
        return RedirectResponse("/insumos", status_code=303)
    return templates.TemplateResponse(
        request,
        "insumos/form.html",
        {
            "insumo": insumo,
            "valores": {
                "nombre": insumo.nombre,
                "unidad_uso": insumo.unidad_uso,
                "merma_pct": str(insumo.merma_pct or 0),
                "metodo_costeo": insumo.metodo_costeo or "",
                "ventana_dias": str(insumo.ventana_dias or ""),
                "categoria_slug": insumo.categoria_slug or "",
                "notas": insumo.notas or "",
            },
            "errors": {},
            "categorias": svc.categorias_disponibles(db),
            **_defaults(),
        },
    )


@router.post("/insumos/{insumo_id}")
def actualizar(
    insumo_id: int,
    request: Request,
    db: Session = Depends(get_db),
    nombre: str = Form(""),
    unidad_uso: str = Form(""),
    merma_pct: str = Form(""),
    metodo_costeo: str = Form(""),
    ventana_dias: str = Form(""),
    categoria_slug: str = Form(""),
    notas: str = Form(""),
):
    insumo = db.get(Insumo, insumo_id)
    if insumo is None:
        return RedirectResponse("/insumos", status_code=303)
    form = dict(
        nombre=nombre, unidad_uso=unidad_uso, merma_pct=merma_pct,
        metodo_costeo=metodo_costeo, ventana_dias=ventana_dias,
        categoria_slug=categoria_slug, notas=notas,
    )
    try:
        svc.update(db, insumo, **form)
    except svc.ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "insumos/form.html",
            {
                "insumo": insumo, "valores": form, "errors": exc.errors,
                "categorias": svc.categorias_disponibles(db), **_defaults(),
            },
            status_code=422,
        )
    return RedirectResponse(f"/insumos/{insumo_id}", status_code=303)


@router.post("/insumos/{insumo_id}/archivar")
def archivar(insumo_id: int, db: Session = Depends(get_db), activo: str = Form("")):
    insumo = db.get(Insumo, insumo_id)
    if insumo is not None:
        svc.set_activo(db, insumo, activo == "1")
    return RedirectResponse(f"/insumos/{insumo_id}", status_code=303)
