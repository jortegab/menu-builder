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

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Insumo, Receta, RecetaLinea
from ..services import (
    cost_chart,
    insumo_costs,
    primer_uso,
    receta_costs,
    receta_historico,
)
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


def _agrupar(rows: list[Receta]) -> list[tuple[str, list[Receta]]]:
    """Platos by `categoria_menu`, in first-seen order, uncategorised last.

    A menu has categories and that is the object the board draws (D38).
    First-seen rather than alphabetical because `svc.listar` already orders
    the rows, and re-sorting the groups would put «Bebidas» above «Fondos»
    on a menu whose own order is neither.
    """
    grupos: dict[str, list[Receta]] = {}
    for r in rows:
        grupos.setdefault((r.categoria_menu or "").strip(), []).append(r)
    sin = grupos.pop("", None)
    salida = list(grupos.items())
    if sin:
        salida.append(("Sin categoría", sin))
    return salida


def _lista(
    request: Request, db: Session, tipo: str, q: str, estado: str, vista: str = ""
) -> HTMLResponse:
    activo = {"activos": True, "archivados": False, "todos": None}.get(estado, True)
    rows = svc.listar(db, tipo=tipo, q=q, activo=activo)
    # The board is the default for the menu and does not exist for
    # preparaciones: a preparación is an ingredient, not a menu item, and
    # "usada en N" is a column, not a card (§3.6).
    vista = vista if vista in ("tablero", "tabla") else "tablero"
    if tipo != "plato":
        vista = "tabla"
    return templates.TemplateResponse(
        request,
        "recetas/list.html",
        {
            "recetas": rows,
            "grupos": _agrupar(rows) if vista == "tablero" else [],
            "costeos": receta_costs.costear_muchas(db, rows),
            "usos": svc.usos_count(db, [r.id for r in rows]),
            "hist": receta_historico.resumen_muchas(db, rows) if vista == "tablero" else {},
            # §3.10, and only ever on the menu: it is the screen `/` lands
            # on, and a checklist repeated on six screens is nagging.
            "primeros_pasos": primer_uso.mostrar(db) if tipo == "plato" else None,
            "tipo": tipo,
            "vista": vista,
            "q": q,
            "estado": estado,
        },
    )


@router.get("/recetas", response_class=HTMLResponse)
def list_platos(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    estado: str = "activos",
    vista: str = "",
):
    return _lista(request, db, "plato", q, estado, vista)


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


def _contexto_builder(
    db: Session, receta: Receta, total_previo: Decimal | None = None, **extra
) -> dict:
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

    # The composition bar (§3.3), which since D39 is the ONLY place cost
    # share appears -- there is no per-row fill and no `%` column.
    #
    # `colores` keys on the LINE id, never on the ingredient's name: a
    # receta may legitimately carry the same insumo twice (the live "Cerdo
    # en salsa" has two Aceite de oliva lines, one for the sear and one to
    # finish), and keying on the name would give both the same swatch and
    # cross-link the wrong row on hover.
    con_costo = [c for c in (costeo.lineas or []) if c.costo is not None]
    segmentos = cost_chart.composicion([(c.nombre, c.costo) for c in con_costo])
    colores = {c.linea.linea_id: s.color for c, s in zip(con_costo, segmentos)}

    return {
        "receta": receta,
        "costeo": costeo,
        "filas": lineas,
        "bases": bases,
        "unidades": unidades,
        "segmentos": segmentos,
        "colores": colores,
        "delta": _delta(total_previo, costeo.costo_total),
        **extra,
    }


def _delta(previo: Decimal | None, actual: Decimal | None) -> Decimal | None:
    """How much the total just moved, or None if that is not a question with
    an answer.

    **The server does this subtraction, not the browser.** §3.4's original
    sketch had the server hand JS two numbers to animate between; the tick
    animation was then dropped (an animation the operator sees forty times a
    day reads as lag, not feedback), and with it went the only reason for
    JavaScript to hold two totals at once. What is left is one number the
    server already knows how to compute, so it computes it -- and D16 is not
    even approached, let alone tested.

    Three things are deliberately not a delta:

    - **Either end missing.** A receta that had no total because an
      ingredient had no price, and now has one, did not get ₡1 046 more
      expensive; it became costable. That is what the incompleto banner
      leaving says, and a chip claiming a rise would be a fabricated
      number (D10's whole argument).
    - **No movement.** Changing a unit from g to g posts an edit and moves
      nothing. «+₡0» is noise wearing the clothes of feedback.
    - **A page load.** Only `_responder` passes a previous total, so a
      fresh GET never has one. §5: nothing animates on page load.
    """
    if previo is None or actual is None:
        return None
    diferencia = actual - previo
    return diferencia if diferencia != 0 else None


def _inspector(db: Session, filas: dict, linea_id: int | None) -> dict:
    """The «¿Por qué este costo?» pane for whichever line is selected.

    The explanation itself already exists -- `insumo_costs.explicar` is what
    `/insumos/{id}` renders. All this does is bring it *next to the line*,
    so finding out why an ingredient is expensive stops being a trip out of
    the recipe being built and back (DESIGN.md §3.1).

    Nothing selected, a stale id from an older render, or a line pointing at
    a preparación rather than an insumo all resolve to "no pane", never to
    an error: the id rides in the URL, so a bookmarked or reloaded builder
    must not 500 because a line was deleted in between.
    """
    fila = filas.get(linea_id) if linea_id else None
    if fila is None or fila.tipo_componente != "insumo":
        return {"sel": fila, "explicacion": None}
    insumo = db.get(Insumo, fila.insumo_id)
    if insumo is None:
        return {"sel": None, "explicacion": None}
    return {"sel": fila, "sel_insumo": insumo, "explicacion": insumo_costs.explicar(db, insumo)}


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
def detalle(
    receta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    linea: int | None = None,
):
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    ctx = _contexto_builder(
        db, receta,
        usos=svc.usos(db, receta),
        hist=receta_historico.tarjeta(db, receta),
        **_paleta(db),
    )
    # Which line the inspector is showing rides in the query string rather
    # than in JS state, so selecting one is a plain link: it works with
    # JavaScript off, it is bookmarkable, and the back button does the
    # obvious thing (DESIGN.md §6's revised promise).
    ctx.update(_inspector(db, ctx["filas"], linea))
    return templates.TemplateResponse(request, "recetas/detail.html", ctx)


@router.get("/recetas/{receta_id}/cocina", response_class=HTMLResponse)
def cocina(receta_id: int, request: Request, db: Session = Depends(get_db)):
    """The recipe at arm's length (§3.11, D43): large type, quantities and
    notas, **no costs and no controls**.

    It deliberately does not call `receta_costs.costear`. Not printing a
    figure the context already carries would leave the promise one `{{ }}`
    away from being broken, on a screen that lives on a pass where anyone
    walking past can read it; `svc.arbol_cocina` has no cost field at all,
    so there is nothing to leak. It also means a receta whose graph has a
    problem, or whose ingredients have no prices yet, still says what goes
    in it -- which is all a cook wanted from it.
    """
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    return templates.TemplateResponse(
        request,
        "recetas/cocina.html",
        {"receta": receta, "nodos": svc.arbol_cocina(db, receta)},
    )


# --------------------------------------------------------------------------
# El modo simulador (DESIGN.md §3.9, D42). Probar sin guardar.
#
# **Nothing in here writes**, and that is structural rather than careful:
# `receta_costs.simular` substitutes quantities on in-memory dataclasses
# between `cargar_grafo` and `graph.cost_recipe`, so there is no write path
# to get wrong. `receta_costo_historico` reads committed rows and rides the
# catalog sync, so it is incapable of seeing a simulation -- a simulated
# cost in that table would be a wrong number that looks exactly like a
# right one.
#
# **The scratch state lives in the form, not in a session, and that revises
# Q11.** The question as posed was "session or a `borrador` row", and the
# row was rightly rejected: with no Alembic (D15) it is a hand-written
# ALTER TABLE, and it puts a not-real cost in a table the rest of the app
# reads. But the session half is not needed either. The simulador is one
# screen with the quantities in its own inputs; every recompute posts them
# and gets a rendered answer back, and «Guardar» posts the same values to
# the ordinary validated write path.
#
# That satisfies every constraint D42 lists and drops a whole class of
# state: no cookie, no server-side dict, no eviction, no dependency
# (Starlette's session middleware is not installed and this does not need
# it), and no behaviour that differs across a restart. It also matches
# D42's own stated semantics *better* -- "leaving the page discards" is
# what a form does by construction, whereas a session would helpfully
# restore a simulation the operator had already walked away from.
# --------------------------------------------------------------------------


def _delta_sim(receta: Receta, sim) -> Decimal | None:
    """How much the simulation moves the headline figure — per portion for
    a plato, per batch for a preparación.

    Same `_delta` rules and for the same reasons: a figure that was «—» at
    either end did not move, it became costable, and a chip claiming a rise
    would be a number nobody computed (D10).
    """
    campo = "costo_por_porcion" if receta.tipo == "plato" else "costo_total"
    return _delta(getattr(sim.actual, campo), getattr(sim.simulada, campo))


def _cantidades_simuladas(form) -> dict[int, Decimal]:
    """`{linea_id: cantidad}` off the simulador's own inputs.

    Anything unparseable is dropped rather than defaulted: a line whose box
    the operator emptied or typo'd keeps its real quantity, which is the
    reading that cannot invent a number. A comma is a decimal separator
    here as everywhere else in this app.
    """
    salida: dict[int, Decimal] = {}
    for clave, valor in form.multi_items():
        if not clave.startswith("cant-"):
            continue
        try:
            linea_id = int(clave[5:])
            cantidad = Decimal(str(valor).strip().replace(",", "."))
        except (ValueError, InvalidOperation):
            continue
        if cantidad > 0:
            salida[linea_id] = cantidad
    return salida


@router.get("/recetas/{receta_id}/simular", response_class=HTMLResponse)
def simular_abrir(receta_id: int, request: Request, db: Session = Depends(get_db)):
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    filas = receta_costs.lineas_por_id(db, receta)
    cantidades = {f.id: f.cantidad for f in filas.values()}
    sim = receta_costs.simular(db, receta, cantidades)
    return templates.TemplateResponse(
        request,
        "recetas/simular.html",
        {
            "receta": receta,
            "filas": filas,
            "cantidades": cantidades,
            "sim": sim,
            "delta_sim": _delta_sim(receta, sim),
            "nombres": {
                f.id: svc.nombre_componente(db, f) or "—" for f in filas.values()
            },
        },
    )


@router.post("/recetas/{receta_id}/simular", response_class=HTMLResponse)
async def simular_recalcular(
    receta_id: int, request: Request, db: Session = Depends(get_db)
):
    """Recompute and render. Answers the fragment for htmx and the whole
    page for a plain form post, same dual shape as every other mutation
    route -- except that this one mutates nothing (D35)."""
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    cantidades = _cantidades_simuladas(await request.form())
    filas = receta_costs.lineas_por_id(db, receta)
    sim = receta_costs.simular(db, receta, cantidades)
    ctx = {
        "receta": receta,
        "filas": filas,
        "cantidades": {f.id: cantidades.get(f.id, f.cantidad) for f in filas.values()},
        "sim": sim,
        "delta_sim": _delta_sim(receta, sim),
        "nombres": {f.id: svc.nombre_componente(db, f) or "—" for f in filas.values()},
    }
    plantilla = (
        "recetas/_simulador.html"
        if request.headers.get("hx-request")
        else "recetas/simular.html"
    )
    return templates.TemplateResponse(request, plantilla, ctx)


@router.post("/recetas/{receta_id}/simular/guardar")
async def simular_guardar(receta_id: int, request: Request, db: Session = Depends(get_db)):
    """Apply the simulation for real.

    Through `svc.actualizar_linea`, the same validated write every other
    quantity edit goes through -- the simulador does not get a private way
    into the database, and a quantity it would refuse on the builder is
    refused here too.
    """
    receta = db.get(Receta, receta_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    cantidades = _cantidades_simuladas(await request.form())
    for linea in receta_costs.lineas_por_id(db, receta).values():
        nueva = cantidades.get(linea.id)
        if nueva is None or nueva == linea.cantidad:
            continue
        try:
            svc.actualizar_linea(db, linea, cantidad=str(nueva), unidad=linea.unidad)
        except ValidationError:
            db.rollback()
    return RedirectResponse(f"/recetas/{receta_id}", status_code=303)


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


def _con_paso(cantidad: str, paso: str) -> str:
    """Apply the stepper's `−`/`+` to whatever is in the quantity box.

    The stepper buttons are real submit buttons carrying `paso`, so a step
    is an ordinary form POST and works with JavaScript off -- the same rule
    drag already follows (D34). The arithmetic is here rather than in the
    browser for the same reason every other number is: there is one place
    that computes, and it is the server.

    A comma is a valid decimal separator in Costa Rica, so it is accepted
    on the way in and handed back the way the service expects. Anything
    unparseable passes through untouched: `svc.actualizar_linea` already
    has the Spanish message for it, and a second opinion here would either
    duplicate that message or silently swallow the operator's typo.
    """
    if not paso:
        return cantidad
    try:
        actual = Decimal(cantidad.strip().replace(",", "."))
        delta = Decimal(paso)
    except (InvalidOperation, AttributeError):
        return cantidad
    # Step by a tenth below 10 -- 0.5 kg of pechuga should not jump to 1.5.
    # Above that the unit is small enough (g, ml) that whole numbers are
    # what anyone means by "one more".
    escala = Decimal("0.1") if actual < 10 else Decimal("1")
    nueva = actual + delta * escala
    if nueva <= 0:  # a line with no quantity is a line meant to be deleted
        return cantidad
    return format(nueva.normalize(), "f")


def _total_actual(db: Session, receta: Receta) -> Decimal | None:
    """The receta's total *before* an edit, for the delta chip (§3.4).

    Yes, this costs a second `costear` per line edit. Taken deliberately:
    the alternative is echoing the old total back from the browser as a
    form field, which would make the chip's baseline something the client
    supplies rather than something the server computed — and on this
    screen, of all screens, a figure whose provenance is "the page said so"
    is the wrong trade. A receta is a handful of lines on a machine in the
    same building.
    """
    return receta_costs.costear(db, receta).costo_total


def _responder(
    request: Request,
    db: Session,
    receta: Receta,
    *,
    error: str | None = None,
    total_previo: Decimal | None = None,
):
    if request.headers.get("hx-request"):
        return templates.TemplateResponse(
            request,
            "recetas/_cuerpo.html",
            _contexto_builder(db, receta, total_previo=total_previo, error_linea=error),
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
    previo = _total_actual(db, receta)
    error = None
    try:
        svc.agregar_linea(
            db, receta, tipo_componente=tipo_componente, componente_id=componente_id,
            cantidad=cantidad, unidad=unidad,
        )
    except ValidationError as exc:
        error = "; ".join(exc.errors.values())
    db.refresh(receta)
    return _responder(request, db, receta, error=error, total_previo=previo)


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
    previo = _total_actual(db, receta)
    ids = [int(p) for p in orden.split(",") if p.strip().isdigit()]
    if ids:
        svc.reordenar(db, receta, ids)
    db.refresh(receta)
    return _responder(request, db, receta, total_previo=previo)


@router.post("/recetas/{receta_id}/lineas/{linea_id}")
def actualizar_linea(
    receta_id: int,
    linea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    cantidad: str = Form(""),
    unidad: str = Form(""),
    paso: str = Form(""),
):
    receta = db.get(Receta, receta_id)
    linea = db.get(RecetaLinea, linea_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    previo = _total_actual(db, receta)
    error = None
    if linea is not None and linea.receta_id == receta.id:
        cantidad = _con_paso(cantidad, paso)
        try:
            svc.actualizar_linea(db, linea, cantidad=cantidad, unidad=unidad)
        except ValidationError as exc:
            db.rollback()
            error = "; ".join(exc.errors.values())
    db.refresh(receta)
    return _responder(request, db, receta, error=error, total_previo=previo)


@router.post("/recetas/{receta_id}/lineas/{linea_id}/eliminar")
def eliminar_linea(
    receta_id: int, linea_id: int, request: Request, db: Session = Depends(get_db)
):
    receta = db.get(Receta, receta_id)
    linea = db.get(RecetaLinea, linea_id)
    if receta is None:
        return RedirectResponse("/recetas", status_code=303)
    previo = _total_actual(db, receta)
    if linea is not None and linea.receta_id == receta.id:
        svc.eliminar_linea(db, linea)
    db.refresh(receta)
    return _responder(request, db, receta, total_previo=previo)


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
    previo = _total_actual(db, receta)
    if linea is not None and linea.receta_id == receta.id and delta in (-1, 1):
        svc.mover_linea(db, linea, delta)
    db.refresh(receta)
    return _responder(request, db, receta, total_previo=previo)
