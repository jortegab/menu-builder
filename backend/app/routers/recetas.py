"""Recetas: the menu (`/recetas`), the preparaciones (`/preparaciones`) and
the builder that edits either of them.

**One builder for both tipos**, because they are one table (D8). A plato
and a preparación have identical structure and identical costing; the only
difference is that one declares portions and the other a yield, and that is
two lines in a template, not a second screen. `/preparaciones` is a filtered
list, and every row on it links to `/recetas/{id}` like every plato does.

**Route order is load-bearing.** `/recetas/nueva` is declared before
`/recetas/{receta_id}` because FastAPI matches in declaration order and
validates the route it matched -- the other way round, "nueva" reaches the
int converter and 422s. Same trap `main.py` documents for
`/insumos/mapeo`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Insumo, Receta, RecetaLinea
from ..services import receta_costs
from ..services import recetas as svc
from ..services.insumos import ValidationError
from ..services.units import compatible_units
from ..templating import templates

router = APIRouter()


def _valores(receta: Receta | None, tipo: str) -> dict:
    if receta is None:
        return {"tipo": tipo, "rendimiento_unidad": "g", "porciones": "1"}
    return {
        "nombre": receta.nombre,
        "tipo": receta.tipo,
        "porciones": str(receta.porciones or ""),
        "rendimiento_cantidad": str(receta.rendimiento_cantidad or ""),
        "rendimiento_unidad": receta.rendimiento_unidad or "g",
        "categoria_menu": receta.categoria_menu or "",
        "descripcion": receta.descripcion or "",
        "notas_produccion": receta.notas_produccion or "",
    }


def _lista(request: Request, db: Session, tipo: str, q: str, estado: str) -> HTMLResponse:
    activo = {"activos": True, "archivados": False, "todos": None}.get(estado, True)
    rows = svc.listar(db, tipo=tipo, q=q, activo=activo)
    return templates.TemplateResponse(
        request,
        "recetas/list.html",
        {
            "recetas": rows,
            "costeos": receta_costs.costear_muchas(db, rows),
            "usos": svc.usos_count(db, [r.id for r in rows]),
            "tipo": tipo,
            "q": q,
            "estado": estado,
        },
    )


@router.get("/recetas", response_class=HTMLResponse)
def list_platos(
    request: Request, db: Session = Depends(get_db), q: str = "", estado: str = "activos"
):
    return _lista(request, db, "plato", q, estado)


@router.get("/preparaciones", response_class=HTMLResponse)
def list_preparaciones(
    request: Request, db: Session = Depends(get_db), q: str = "", estado: str = "activos"
):
    return _lista(request, db, "preparacion", q, estado)


# Declared before `/recetas/{receta_id}` -- see the module docstring.
@router.get("/recetas/nueva", response_class=HTMLResponse)
def nueva(request: Request, tipo: str = "plato"):
    tipo = tipo if tipo in svc.TIPOS else "plato"
    return templates.TemplateResponse(
        request,
        "recetas/form.html",
        {"receta": None, "valores": _valores(None, tipo), "errors": {}},
    )


@router.get("/preparaciones/nueva", response_class=HTMLResponse)
def nueva_preparacion(request: Request):
    return RedirectResponse("/recetas/nueva?tipo=preparacion", status_code=303)


@router.post("/recetas")
def crear(
    request: Request,
    db: Session = Depends(get_db),
    nombre: str = Form(""),
    tipo: str = Form("plato"),
    porciones: str = Form(""),
    rendimiento_cantidad: str = Form(""),
    rendimiento_unidad: str = Form(""),
    categoria_menu: str = Form(""),
    descripcion: str = Form(""),
    notas_produccion: str = Form(""),
):
    form = dict(
        nombre=nombre, tipo=tipo, porciones=porciones,
        rendimiento_cantidad=rendimiento_cantidad, rendimiento_unidad=rendimiento_unidad,
        categoria_menu=categoria_menu, descripcion=descripcion,
        notas_produccion=notas_produccion,
    )
    try:
        receta = svc.create(db, **form)
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "recetas/form.html",
            {"receta": None, "valores": form, "errors": exc.errors},
            status_code=422,
        )
    return RedirectResponse(f"/recetas/{receta.id}", status_code=303)


def _contexto_builder(db: Session, receta: Receta, **extra) -> dict:
    """Everything both the full page and the htmx fragment need.

    The fragment is rendered from the *same* context builder as the page, so
    a total that appears after a drag can never be computed differently from
    the one that was there on load -- which is the whole point of D16's
    "no JSON endpoint": one costing engine, one render path.
    """
    costeo = receta_costs.costear(db, receta)
    lineas = receta_costs.lineas_por_id(db, receta)
    # `bases[id]` is the unit the component is *costed* in; `unidades[id]`
    # is every unit the line may be *written* in. Two different things
    # (D30), and the selector must only ever offer the second -- an
    # incompatible pair has to be unreachable through the UI, not merely
    # rejected once the operator has already chosen it.
    bases: dict[int, str] = {}
    unidades: dict[int, list[str]] = {}
    for fila in lineas.values():
        base = svc.unidad_del_componente(
            db,
            fila.tipo_componente,
            fila.insumo_id if fila.tipo_componente == "insumo" else fila.componente_receta_id,
        )
        bases[fila.id] = base or fila.unidad
        unidades[fila.id] = compatible_units(base) if base else [fila.unidad]
    return {
        "receta": receta,
        "costeo": costeo,
        "filas": lineas,
        "bases": bases,
        "unidades": unidades,
        **extra,
    }


def _paleta(db: Session, q: str = "") -> dict:
    """The insumos and preparaciones the operator can drop into a recipe.

    Archived rows never appear: an archived insumo is refused by
    `agregar_linea` anyway, and offering something the drop will reject is
    worse than not offering it.
    """

    like = f"%{q.strip().lower()}%" if q.strip() else None

    stmt_i = select(Insumo).where(Insumo.activo.is_(True))
    if like:
        stmt_i = stmt_i.where(func.lower(Insumo.nombre).like(like))
    stmt_p = select(Receta).where(Receta.activo.is_(True), Receta.tipo == "preparacion")
    if like:
        stmt_p = stmt_p.where(func.lower(Receta.nombre).like(like))

    return {
        "paleta_insumos": list(db.scalars(stmt_i.order_by(Insumo.nombre).limit(60))),
        "paleta_preparaciones": list(db.scalars(stmt_p.order_by(Receta.nombre).limit(60))),
        "paleta_q": q,
    }


@router.get("/recetas/{receta_id}", response_class=HTMLResponse)
def detalle(receta_id: int, request: Request, db: Session = Depends(get_db)):
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    return templates.TemplateResponse(
        request,
        "recetas/detail.html",
        _contexto_builder(
            db, receta,
            usos=svc.usos(db, receta),
            **_paleta(db),
        ),
    )


@router.get("/recetas/{receta_id}/paleta", response_class=HTMLResponse)
def paleta(receta_id: int, request: Request, db: Session = Depends(get_db), q: str = ""):
    """The palette on its own, for the search box's htmx swap."""
    receta = db.get(Receta, receta_id)
    if receta is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(
        request, "recetas/_paleta.html", {"receta": receta, **_paleta(db, q)}
    )


@router.get("/recetas/{receta_id}/editar", response_class=HTMLResponse)
def editar(receta_id: int, request: Request, db: Session = Depends(get_db)):
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    return templates.TemplateResponse(
        request,
        "recetas/form.html",
        {"receta": receta, "valores": _valores(receta, receta.tipo), "errors": {}},
    )


@router.post("/recetas/{receta_id}")
def actualizar(
    receta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    nombre: str = Form(""),
    tipo: str = Form("plato"),
    porciones: str = Form(""),
    rendimiento_cantidad: str = Form(""),
    rendimiento_unidad: str = Form(""),
    categoria_menu: str = Form(""),
    descripcion: str = Form(""),
    notas_produccion: str = Form(""),
):
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    form = dict(
        nombre=nombre, tipo=tipo, porciones=porciones,
        rendimiento_cantidad=rendimiento_cantidad, rendimiento_unidad=rendimiento_unidad,
        categoria_menu=categoria_menu, descripcion=descripcion,
        notas_produccion=notas_produccion,
    )
    try:
        svc.update(db, receta, **form)
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "recetas/form.html",
            {"receta": receta, "valores": form, "errors": exc.errors},
            status_code=422,
        )
    return RedirectResponse(f"/recetas/{receta_id}", status_code=303)


@router.post("/recetas/{receta_id}/archivar")
def archivar(receta_id: int, db: Session = Depends(get_db), activo: str = Form("")):
    receta = db.get(Receta, receta_id)
    if receta is not None:
        svc.set_activo(db, receta, activo == "1")
    return RedirectResponse(f"/recetas/{receta_id}", status_code=303)


# --------------------------------------------------------------------------
# Lines. Every one of these answers with the same fragment when htmx asked
# and with a redirect when a plain form did, so the screen works with
# JavaScript switched off -- htmx is the fast path, never the only one.
# --------------------------------------------------------------------------


def _responder(
    request: Request, db: Session, receta: Receta, *, error: str | None = None
):
    if request.headers.get("hx-request"):
        return templates.TemplateResponse(
            request,
            "recetas/_cuerpo.html",
            _contexto_builder(db, receta, error_linea=error),
        )
    destino = f"/recetas/{receta.id}"
    return RedirectResponse(destino, status_code=303)


@router.post("/recetas/{receta_id}/lineas")
def agregar_linea(
    receta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    tipo_componente: str = Form(""),
    componente_id: int = Form(...),
    cantidad: str = Form("1"),
    unidad: str = Form(""),
):
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    error = None
    try:
        svc.agregar_linea(
            db, receta, tipo_componente=tipo_componente, componente_id=componente_id,
            cantidad=cantidad, unidad=unidad,
        )
    except ValidationError as exc:
        error = "; ".join(exc.errors.values())
    db.refresh(receta)
    return _responder(request, db, receta, error=error)


@router.post("/recetas/{receta_id}/lineas/orden")
def reordenar(
    receta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    orden: str = Form(""),
):
    """`orden` is a comma-separated list of line ids, in their new order --
    what the drag module posts after a drop.
    """
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    ids = [int(p) for p in orden.split(",") if p.strip().isdigit()]
    if ids:
        svc.reordenar(db, receta, ids)
    db.refresh(receta)
    return _responder(request, db, receta)


@router.post("/recetas/{receta_id}/lineas/{linea_id}")
def actualizar_linea(
    receta_id: int,
    linea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    cantidad: str = Form(""),
    unidad: str = Form(""),
):
    receta = db.get(Receta, receta_id)
    linea = db.get(RecetaLinea, linea_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    error = None
    if linea is not None and linea.receta_id == receta.id:
        try:
            svc.actualizar_linea(db, linea, cantidad=cantidad, unidad=unidad)
        except ValidationError as exc:
            db.rollback()
            error = "; ".join(exc.errors.values())
    db.refresh(receta)
    return _responder(request, db, receta, error=error)


@router.post("/recetas/{receta_id}/lineas/{linea_id}/eliminar")
def eliminar_linea(
    receta_id: int, linea_id: int, request: Request, db: Session = Depends(get_db)
):
    receta = db.get(Receta, receta_id)
    linea = db.get(RecetaLinea, linea_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    if linea is not None and linea.receta_id == receta.id:
        svc.eliminar_linea(db, linea)
    db.refresh(receta)
    return _responder(request, db, receta)


@router.post("/recetas/{receta_id}/lineas/{linea_id}/mover")
def mover_linea(
    receta_id: int,
    linea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    delta: int = Form(0),
):
    """The keyboard path for reordering. Drag is faster; this always works."""
    receta = db.get(Receta, receta_id)
    linea = db.get(RecetaLinea, linea_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    if linea is not None and linea.receta_id == receta.id and delta in (-1, 1):
        svc.mover_linea(db, linea, delta)
    db.refresh(receta)
    return _responder(request, db, receta)
