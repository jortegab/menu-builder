"""`/insumos/mapeo` -- the mapping queue (PLAN.md §8, DESIGN.md §3.7).

Suggestions are computed **on render**, not stored. They are cheap
deterministically, they go stale the moment an insumo is created or
renamed, and storing them would mean a second thing to invalidate. The LLM
path is the exception worth watching: it is one billed call per row shown.

**Since step 2.3 the screen is a queue of one**, not a stack of forms. A
page that emits one card per pending product against a 1 420-product
catalog gets *longer* the more work there is, which is exactly backwards.
It also cost 25 billed LLM calls per render; one card costs one.

In front of it is the bulk gate: a large share of the catalog will never be
an ingredient -- `CLORO ECONOMICO MFS 1/1 GL`, `CREMA LAVAPLATOS LIMON NIT
1/1KG` -- and marching those through a one-at-a-time queue is a punishment.
The gate dismisses a whole filtered set at once, and because that is a
large irreversible-feeling action it names its own size, records a `lote`,
and offers an undo.

The rules do not move. `confirmar` is still only ever reached from a
person, still writes `mapped_by=human`, and confidence still orders the
queue and never auto-confirms (non-negotiable #4). This is a presentation
change and nothing else.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..models import CatalogProduct
from ..db import get_db
from ..services import insumo_match
from ..services import mapeo as svc
from ..services import insumos as insumos_svc
from ..services.costing import METHODS
from ..services.insumos import UNIDADES_BASE, ValidationError
from ..services.pagination import pagination_window
from ..templating import templates

router = APIRouter()

# The card on screen, plus enough of a look-ahead to say what is next. One
# billed LLM call per suggestion, and only the first is ever suggested --
# the peek is a description, not a Sugerencia.
_VENTANA = 6


# `_bandera` lived here, and its going is the point of the change.
#
# The queue used to carry `receta` and `reciente` as request flags, and an
# unchecked checkbox posts its hidden mirror as `value=""` -- which FastAPI
# reads as *absent* and replaces with the default. On the bulk gate that
# inverted the operator's own filter between the count they were shown and
# the set they approved. Two locks were needed to hold that door shut.
#
# The scope is app policy now (`/ajustes`), so it never rides in a request
# at all and the door is not there to lock. What replaces those two tests is
# the one below it: the gate must act on the SAVED scope.


def _cat(form) -> list[str]:
    """The ticked categories, read off the raw form.

    Not a declared parameter, for the same reason `tambien` is not: a
    repeated field is a list, and FastAPI substitutes the default for one
    that is absent. On the gate that default would be «no category filter»,
    i.e. a WIDER set than the count the operator approved. That is the same
    shape of bug the removed `_bandera` guarded against, which is why the
    template mirrors these into every POST form rather than trusting a
    default to be harmless.
    """
    return [str(v) for v in form.getlist("cat")]


def _volver(q: str, cat: list[str] | None = None, desde: int = 0, extra: str = "") -> str:
    """Back to the queue with the operator's filters intact. Losing them on
    every confirmation would make the gate pointless.

    Percent-encoded, which it was not: a search for «arroz & frijol» used to
    end the query string at the `&` and silently drop every filter after it.
    Category slugs are `[a-z_]` today and would survive raw, but they come
    from upstream's taxonomy and nothing here gets to promise that.
    """
    url = f"/insumos/mapeo?q={quote(q)}"
    for slug in cat or []:
        url += f"&cat={quote(slug)}"
    if desde:
        url += f"&desde={desde}"
    return url + extra


def _pantalla(
    request: Request,
    db: Session,
    *,
    q: str,
    cat: list[str],
    desde: int,
    lote: str = "",
    descartados: int = 0,
    errors: dict[str, str] | None = None,
    valores: dict[str, str] | None = None,
):
    """Render the queue.

    Split out so a failed confirmation can answer with the *same screen*
    rather than a 500 or a redirect that loses what was typed. Nothing was
    written when validation refuses, so `desde` still points at the same
    product and the card rebuilds itself identically -- with `errors` beside
    the offending field and `valores` holding what the operator had in it.
    """
    # The scope is app policy (`/ajustes`), read once here and passed down.
    # It is NOT a request parameter any more: a filter that decides what a
    # bulk dismissal covers has no business being flippable by a stray click
    # on the screen that offers the dismissal.
    alc = svc.alcance(db)
    coinciden = svc.contar_filtro(
        db, q=q, solo_receta=alc.solo_receta, solo_reciente=alc.solo_reciente,
        categorias=cat, dias_reciente=alc.dias_reciente,
    )
    # One product per page, so the page strip counts products. `desde` is
    # clamped at BOTH ends: these offsets ride in bookmarkable URLs and
    # survive a bulk dismissal that can shorten the queue by hundreds, and
    # an offset past the end would otherwise render a card-less screen that
    # looks like "nothing left to map" when there is plenty.
    desde = max(0, min(desde, max(0, coinciden - 1)))
    filas = svc.pendientes(
        db, q=q, solo_receta=alc.solo_receta, solo_reciente=alc.solo_reciente,
        categorias=cat, dias_reciente=alc.dias_reciente,
        limite=_VENTANA, desde=desde,
    )
    actual = filas[0] if filas else None
    sugerencia = svc.sugerir(db, actual[0], actual[1]) if actual else None

    # Other *pending* rows that are probably the same insumo, so one
    # confirmation can take the whole group. The live catalog is full of
    # them and they are what makes a 1 435-row queue tolerable.
    #
    # **The category picker deliberately does NOT narrow this.** A duplicate
    # pair is as likely to straddle two leaves as to sit in one -- upstream
    # categorises per product, so the same sugar can land in
    # `alimentos_abarrotes` and in `alimentos_otros` -- and hiding the second
    # one is how a duplicate insumo gets created, which splits an
    # ingredient's price history in half with nothing looking wrong on any
    # screen. Confirming a look-alike from outside the current filter is
    # harmless: it is on screen, a person ticked it, and
    # `posicion_tras_decidir` counts it as zero because it was never in the
    # filtered order to begin with.
    grupo = (
        svc.parecidos(
            db, actual[0], q=q,
            solo_receta=alc.solo_receta, solo_reciente=alc.solo_reciente,
            dias_reciente=alc.dias_reciente,
        )
        if actual
        else []
    )
    # Each member keeps its OWN factor: `1/15 LB` and `1/2,27 KG` are one
    # ingredient in two pack sizes, and a shared factor would make one of
    # them wrong by the ratio between the packs.
    unidad = sugerencia.unidad_sugerida if sugerencia else "g"
    factores = {
        p.producto.id: insumo_match.resolve_factor(
            descripcion=p.producto.description,
            unidad_base=unidad,
            unidad_medida=p.producto.unidad_medida,
            categoria_slug=p.producto.categoria_slug,
        )
        for p in grupo
    }

    return templates.TemplateResponse(
        request,
        "insumos/mapeo.html",
        {
            # Exactly one suggestion is computed, which is also exactly one
            # billed call when a key is configured.
            "sugerencia": sugerencia,
            "grupo": grupo,
            "factores": factores,
            "resumen": svc.resumen(db),
            "cobertura": svc.cobertura(db),
            # No longer rendered -- the count line went with the gate -- but
            # still what clamps `desde` and sizes the page strip above.
            "coinciden": coinciden,
            "pagina": desde + 1,
            "total_paginas": max(1, coinciden),
            "ventana": pagination_window(desde + 1, max(1, coinciden)),
            "q": q,
            "cat": cat,
            # The scope, for the pills that report it. The screen states it
            # and links to where it is changed; it does not offer to change
            # it in place.
            "alcance": alc,
            # Counted WITHOUT the category filter applied, so ticking one
            # does not empty the list it was ticked from.
            "facetas": svc.facetas_categoria(
                db, q=q, solo_receta=alc.solo_receta,
                solo_reciente=alc.solo_reciente, dias_reciente=alc.dias_reciente,
            ),
            "desde": desde,
            # Set by a redirect straight after a bulk dismissal, so the
            # flash can offer to undo exactly that batch.
            "lote": lote,
            "descartados": descartados,
            "unidades": UNIDADES_BASE,
            "errors": errors or {},
            "valores": valores or {},
            # The «usar el de Ajustes» option needs to name the default it
            # would fall back to, exactly as the insumo form does.
            "metodo_default": settings.costing_method_default,
            "ventana_default": settings.costing_window_days_default,
            # The card as it stands on first render; every later change
            # re-renders it through /insumos/mapeo/costo.
            "costo": svc.costo_preview(
                db, actual[0],
                factor=sugerencia.factor if sugerencia else None,
                unidad_base=sugerencia.unidad_sugerida if sugerencia else "g",
                metodo=settings.costing_method_default,
                ventana_dias=settings.costing_window_days_default,
            ) if actual else None,
        },
    )


@router.get("/insumos/mapeo", response_class=HTMLResponse)
def cola(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    cat: list[str] = Query(default=[]),
    desde: int = 0,
    lote: str = "",
    descartados: int = 0,
):
    return _pantalla(
        request, db, q=q, cat=cat, desde=desde,
        lote=lote, descartados=descartados,
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
async def confirmar(
    request: Request,
    db: Session = Depends(get_db),
    catalog_product_id: int = Form(...),
    insumo_id: str = Form(""),
    nombre_nuevo: str = Form(""),
    unidad_base: str = Form("g"),
    merma_pct: str = Form(""),
    metodo_costeo: str = Form(""),
    ventana_dias: str = Form(""),
    factor: str = Form(""),
    factor_origen: str = Form("manual"),
    q: str = Form(""),
    desde: int = Form(0),
):
    """Confirm this product, and any of its look-alikes the operator ticked.

    The group is opt-in per row and never pre-applied: `parecidos` proposes,
    a person ticks, and only then does anything get written. That is
    non-negotiable #4 unchanged -- what the grouping removes is the *hunting*,
    not the deciding.
    """
    form = await request.form()
    cat = _cat(form)
    # `tambien` are the ticked look-alikes; each carries its own factor,
    # because two pack sizes of one ingredient need two factors.
    tambien = [int(v) for v in form.getlist("tambien") if str(v).isdigit()]
    factores = {catalog_product_id: _factor(factor)}
    for pid in tambien:
        factores[pid] = _factor(str(form.get(f"factor-{pid}", "")))

    # Worked out BEFORE the write, while these rows are still pending: the
    # cursor stays put so the next product falls into this same slot, minus
    # any ticked look-alike that sat behind it.
    alc = svc.alcance(db)
    siguiente = svc.posicion_tras_decidir(
        db,
        desde=desde,
        decididos={catalog_product_id, *tambien},
        q=q,
        solo_receta=alc.solo_receta,
        solo_reciente=alc.solo_reciente,
        categorias=cat,
        dias_reciente=alc.dias_reciente,
    )

    try:
        svc.confirmar_muchos(
            db,
            catalog_product_ids=[catalog_product_id, *tambien],
            insumo_id=int(insumo_id) if insumo_id.strip() else None,
            nombre_nuevo=nombre_nuevo,
            unidad_base=unidad_base if unidad_base in UNIDADES_BASE else "g",
            merma_pct=merma_pct,
            metodo_costeo=metodo_costeo,
            ventana_dias=ventana_dias,
            factores=factores,
            factor_origen=factor_origen,
        )
    except ValidationError as bad:
        # The same screen back, with the message beside the field that
        # caused it and the typed values still in place. A 500 here would
        # cost the operator the group they had just finished ticking.
        db.rollback()
        return _pantalla(
            request, db, q=q, cat=cat, desde=desde,
            errors=bad.errors,
            valores={
                "nombre_nuevo": nombre_nuevo,
                "unidad_base": unidad_base,
                "merma_pct": merma_pct,
                "metodo_costeo": metodo_costeo,
                "ventana_dias": ventana_dias,
                "factor": factor,
            },
        )
    return RedirectResponse(
        _volver(q, cat, desde=siguiente), status_code=303
    )


@router.post("/insumos/mapeo/rechazar")
async def rechazar(
    request: Request,
    db: Session = Depends(get_db),
    catalog_product_id: int = Form(...),
    insumo_id: int = Form(...),
    q: str = Form(""),
    desde: int = Form(0),
):
    cat = _cat(await request.form())
    svc.rechazar(db, catalog_product_id=catalog_product_id, insumo_id=insumo_id)
    # Stays on the same product, and now on the same page too: rejecting the
    # *suggestion* is not deciding the product, so it never leaves the queue
    # and the next render offers the runner-up in the same slot.
    return RedirectResponse(
        _volver(q, cat, desde=desde), status_code=303
    )


@router.post("/insumos/mapeo/descartar")
async def descartar(
    request: Request,
    db: Session = Depends(get_db),
    q: str = Form(""),
):
    """The bulk gate: everything matching the current filters is not an
    insumo. Nothing here touches the mirror -- see `CatalogDescarte`.

    **NOTHING IN THE UI REACHES THIS SINCE 2026-09-07.** «Ninguno es insumo
    — descartar N» came off the screen with the card around the filter bar:
    the scope setting on `/ajustes` keeps limpieza, menaje and higiene out
    of the queue by family, which is the job the gate was doing by hand. The
    route, the service and their tests stay because rows dismissed under the
    old button are still dismissed, `restaurar` is still the way back, and
    deleting a tested bulk operation is not the same decision as taking its
    button off a screen.

    If it ever gets a button again: the count and the dismissed set must be
    the same set. The old button named its own size («descartar 403»), so a
    filter the count honoured and the POST forgot would dismiss hundreds of
    rows nobody approved -- which is why the search text and every ticked
    category ride in this POST body, and why the scope does not ride
    anywhere at all (it is read from `app_setting` on the server).
    """
    cat = _cat(await request.form())
    alc = svc.alcance(db)
    lote, cuantos = svc.descartar_filtro(
        db, q=q, solo_receta=alc.solo_receta, solo_reciente=alc.solo_reciente,
        categorias=cat, dias_reciente=alc.dias_reciente,
    )
    return RedirectResponse(
        _volver(q, cat, extra=f"&lote={lote}&descartados={cuantos}"),
        status_code=303,
    )


@router.post("/insumos/mapeo/deshacer")
async def deshacer(
    request: Request,
    db: Session = Depends(get_db),
    lote: str = Form(""),
    q: str = Form(""),
):
    cat = _cat(await request.form())
    svc.deshacer_descarte(db, lote)
    return RedirectResponse(_volver(q, cat), status_code=303)


@router.post("/insumos/mapeo/restaurar")
def restaurar(db: Session = Depends(get_db)):
    svc.restaurar_todo(db)
    return RedirectResponse("/insumos/mapeo", status_code=303)


@router.post("/insumos/mapeo/costo", response_class=HTMLResponse)
def costo(
    request: Request,
    db: Session = Depends(get_db),
    catalog_product_id: int = Form(...),
    factor: str = Form(""),
    unidad_base: str = Form("g"),
    merma_pct: str = Form(""),
    metodo_costeo: str = Form(""),
    ventana_dias: str = Form(""),
):
    """The cost card, recomputed for whatever is currently in the form.

    **Live, because a stale one would be worse than none.** The card exists
    so the operator can see what their método and factor produce; leaving it
    showing the median while the select says «Máximo» would put a wrong
    number on screen, which is the one thing this app refuses to do.

    Writes nothing. It reads price points and runs the pure costing path.
    """
    producto = db.get(CatalogProduct, catalog_product_id)
    if producto is None:
        return HTMLResponse("")

    metodo = metodo_costeo if metodo_costeo in METHODS else settings.costing_method_default
    try:
        ventana = int(ventana_dias) if ventana_dias.strip() else settings.costing_window_days_default
    except ValueError:
        ventana = settings.costing_window_days_default

    return templates.TemplateResponse(
        request,
        "insumos/_costo.html",
        {
            "costo": svc.costo_preview(
                db, producto,
                factor=_factor(factor),
                unidad_base=unidad_base if unidad_base in UNIDADES_BASE else "g",
                merma_pct=insumos_svc.parse_decimal(merma_pct) or Decimal(0),
                metodo=metodo,
                ventana_dias=max(1, ventana),
            ),
            "producto": producto,
        },
    )
