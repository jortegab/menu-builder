"""LLM-assisted product→insumo suggestion.

Modelled directly on `restaurant-manager`'s `services/llm_categorize.py`,
including the stance that keeps it affordable and safe: **an accelerator,
never a dependency.** No API key, a network failure, a timeout, or an answer
that does not validate all degrade to `insumo_match`'s deterministic
matching. A product always gets *some* suggestion, and nothing ever blocks
on this call succeeding.

**Cost is bounded by design, not by caching.** Called once per *new*
producto, never per purchase. On the live catalog that is ~1 400 calls once,
against 17 645 purchase lines -- a 12:1 ratio, and the reason "once per
producto" is a budget decision rather than a style one. Steady state is a
handful a week.

Called through `httpx` directly rather than the `anthropic` SDK, the same
way upstream calls Hacienda, the BCCR rate and its own categorizer -- this
adds no dependency for one endpoint.

**The answer is validated against the offered list, never trusted raw.**
A model that returns a slug that was never offered, a unit that is not a
unit, or prose instead of a line is treated as no answer at all. And even
a perfect answer is only a *suggestion*: it lands as `status='suggested'`
and waits for a person (non-negotiable #4). `mapping_confidence` orders
the queue; it is never a threshold that auto-confirms.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

import httpx

from ..config import settings
from .units import UnknownUnit, normalize_unit

log = logging.getLogger(__name__)

_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

_SYSTEM_PROMPT = (
    "Sos un asistente de cocina que agrupa productos de factura en insumos de "
    "un restaurante en Costa Rica. Dos proveedores venden el mismo insumo con "
    "nombres distintos: 'FILETE DE PECHUGA DESHUESADA MFS 1/5KG' y 'PECHUGA "
    "DESHUESADA' son el mismo insumo.\n\n"
    "Respondes UNICAMENTE con una linea con tres campos separados por |:\n"
    "  <slug|nuevo:Nombre>|<cantidad>|<unidad>\n\n"
    "Campo 1: el slug exacto de un insumo de la lista ofrecida, o "
    "'nuevo:Nombre del insumo' si ninguno corresponde.\n"
    "Campo 2 y 3: la presentacion que declara la descripcion (por ejemplo "
    "'1/5KG' son 5 y kg; '24x350ML' son 8400 y ml). Si la descripcion no dice "
    "la presentacion, responde - en ambos campos.\n\n"
    "Sin explicacion, sin puntuacion extra, sin comillas. Una sola linea.\n"
    "Ojo: no agrupes cosas distintas. 'Pechuga con hueso' y 'Pechuga "
    "deshuesada' son insumos DISTINTOS porque tienen mermas muy distintas."
)


def _parse_answer(
    text: str, slugs_ofrecidos: set[str]
) -> tuple[str, Decimal | None, str | None] | None:
    """The model's line as (slug_or_nuevo, cantidad, unidad), or None.

    Every branch that returns None is a case where trusting the output would
    put an invented value into a cost. There is no partial credit here: a
    line that cannot be fully understood is discarded and the deterministic
    matcher answers instead.
    """
    partes = [p.strip() for p in (text or "").strip().splitlines()[0].split("|")]
    if len(partes) != 3:
        return None

    destino, raw_cantidad, raw_unidad = partes
    if destino.lower().startswith("nuevo:"):
        nombre = destino.split(":", 1)[1].strip()
        if not nombre or len(nombre) > 200:
            return None
        destino = f"nuevo:{nombre}"
    elif destino not in slugs_ofrecidos:
        # A slug that was never offered. Upstream's rule, verbatim: never trust
        # free-form model output as an identifier.
        return None

    cantidad: Decimal | None = None
    unidad: str | None = None
    if raw_cantidad not in ("-", "") and raw_unidad not in ("-", ""):
        try:
            cantidad = Decimal(raw_cantidad.replace(",", "."))
            unidad = normalize_unit(raw_unidad)
        except (InvalidOperation, UnknownUnit):
            cantidad, unidad = None, None
        else:
            if cantidad <= 0:
                cantidad, unidad = None, None

    return destino, cantidad, unidad


def sugerir(
    descripcion: str, insumos: list[tuple[str, str]]
) -> tuple[str, Decimal | None, str | None] | None:
    """Suggest an insumo for one catalog description.

    `insumos` is [(slug, nombre)] -- the existing insumos the model may
    choose from. Returns (slug | "nuevo:Nombre", cantidad, unidad), or None
    when the model could not be reached or did not answer usably. **Never
    raises**: the caller's fallback is the whole point.
    """
    if not settings.anthropic_api_key:
        return None

    listado = "\n".join(f"- {slug}: {nombre}" for slug, nombre in insumos) or "(ninguno todavía)"
    mensaje = (
        f'Producto: "{descripcion.strip()}"\n\n'
        f"Insumos existentes:\n{listado}\n\nRespuesta:"
    )

    try:
        with httpx.Client(timeout=settings.anthropic_timeout_seconds) as client:
            resp = client.post(
                _API_URL,
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": 60,
                    "temperature": 0,
                    "system": _SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": mensaje}],
                },
            )
    except httpx.HTTPError as exc:
        log.warning("Sugerencia por LLM no respondió para %r: %s", descripcion, exc)
        return None

    if resp.status_code != 200:
        log.warning("Sugerencia por LLM devolvió %s para %r", resp.status_code, descripcion)
        return None

    try:
        payload = resp.json()
        bloques = payload.get("content") or []
        texto = "".join(b.get("text", "") for b in bloques if isinstance(b, dict)).strip()
    except (ValueError, AttributeError) as exc:
        log.warning("Respuesta del LLM con forma inesperada: %s", exc)
        return None

    return _parse_answer(texto, {slug for slug, _ in insumos})


def disponible() -> bool:
    """Whether the LLM path is configured at all -- what the mapping queue
    shows so the operator knows whether suggestions are coming from Claude
    or from string similarity.
    """
    return bool(settings.anthropic_api_key)


# The grouping prompt is a *different question* from the suggestion one and
# has its own system prompt for that reason: `sugerir` picks one destination
# out of a short list of insumos the operator already created, while this
# reads a long list of raw supplier descriptions and asks which of them name
# the same thing. Same model, same transport, different judgement.
_SYSTEM_PROMPT_GRUPO = (
    "Sos un asistente de cocina que revisa facturas de un restaurante en "
    "Costa Rica. Te doy un producto y una lista numerada de otros productos "
    "del catálogo. Decís cuáles de la lista son EL MISMO INSUMO que el "
    "producto dado.\n\n"
    "El mismo insumo puede venir con otra marca, otro proveedor, otro "
    "tamaño de paquete o escrito distinto: 'QUESO MOZZARELLA LEPRINO 1/15 "
    "LB' y 'QUESO MOZZARELLA LEPRINO 1/2,27 KG' son el mismo insumo, y "
    "'AZUCAR BLANCO DONA MARIA' y 'AZUCAR BLANCO DOÑA MARIA' también.\n\n"
    "NO son el mismo insumo si difieren en algo que cambie el rendimiento o "
    "el precio por gramo: 'PECHUGA CON HUESO' y 'PECHUGA DESHUESADA' son "
    "DISTINTOS. Tampoco agrupes cosas que solo comparten un adjetivo: "
    "'PEPINOS GRANDE' y 'ZUQUINI (GRANDE)' son DISTINTOS.\n\n"
    "Respondés UNICAMENTE con una línea por coincidencia, así:\n"
    "  <numero>|<seguro|posible>\n"
    "Usá 'seguro' solo cuando no te quede duda; 'posible' si es probable "
    "pero alguien debería revisarlo.\n"
    "Si ninguno de la lista es el mismo insumo, respondés exactamente: -\n"
    "Sin explicación, sin texto extra."
)

# A bound on the prompt, not on the answer. The pending list is a few
# hundred rows on the live catalog and every one of them is a candidate, so
# this only bites on a catalog much larger than the real one -- and when it
# does, the most-bought products are the ones worth de-duplicating first.
_MAX_CANDIDATOS = 400


def _parse_grupo(text: str, total: int) -> dict[int, str] | None:
    """The model's lines as {index: "seguro"|"posible"}, or None.

    `None` and `{}` mean different things and the caller depends on it:
    `{}` is the model saying "none of these", a real answer, while `None`
    is no usable answer at all and sends the caller to `similitud`. A bare
    "-" is therefore the required way to say "none" -- without it, garbage
    and genuine emptiness would be the same reply.
    """
    limpio = (text or "").strip()
    if not limpio:
        return None
    if limpio == "-":
        return {}

    salida: dict[int, str] = {}
    for linea in limpio.splitlines():
        partes = [p.strip().lower() for p in linea.split("|")]
        if len(partes) != 2:
            continue
        crudo, certeza = partes
        if certeza not in ("seguro", "posible"):
            continue
        try:
            indice = int(crudo)
        except ValueError:
            continue
        # 1-based in the prompt, and an index outside the list is the same
        # class of error as a slug that was never offered: invented, so
        # dropped rather than resolved to whatever it happens to hit.
        if 1 <= indice <= total:
            salida[indice - 1] = certeza

    return salida or None


def agrupar(descripcion: str, candidatos: list[tuple[int, str]]) -> dict[int, str] | None:
    """Which of `candidatos` name the same insumo as `descripcion`.

    `candidatos` is [(id, description)]. Returns {id: "seguro"|"posible"}
    for the matches, `{}` when the model says none match, or None when it
    could not be reached or did not answer usably -- which is the caller's
    signal to fall back to `insumo_match.similitud`. **Never raises.**

    The ids are never sent to the model and never parsed back out of its
    reply: the prompt is a numbered list and the answer is positions in it,
    so the worst a malformed reply can do is name a row that does not
    exist, which is dropped. An id cannot be invented.
    """
    if not settings.anthropic_api_key or not candidatos:
        return None

    recortado = candidatos[:_MAX_CANDIDATOS]
    listado = "\n".join(f"{i}. {desc}" for i, (_, desc) in enumerate(recortado, start=1))
    mensaje = (
        f'Producto: "{descripcion.strip()}"\n\n'
        f"Lista:\n{listado}\n\nRespuesta:"
    )

    try:
        with httpx.Client(timeout=settings.anthropic_timeout_seconds) as client:
            resp = client.post(
                _API_URL,
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": 400,
                    "temperature": 0,
                    "system": _SYSTEM_PROMPT_GRUPO,
                    "messages": [{"role": "user", "content": mensaje}],
                },
            )
    except httpx.HTTPError as exc:
        log.warning("Agrupación por LLM no respondió para %r: %s", descripcion, exc)
        return None

    if resp.status_code != 200:
        log.warning("Agrupación por LLM devolvió %s para %r", resp.status_code, descripcion)
        return None

    try:
        payload = resp.json()
        bloques = payload.get("content") or []
        texto = "".join(b.get("text", "") for b in bloques if isinstance(b, dict)).strip()
    except (ValueError, AttributeError) as exc:
        log.warning("Respuesta del LLM con forma inesperada: %s", exc)
        return None

    posiciones = _parse_grupo(texto, len(recortado))
    if posiciones is None:
        return None
    return {recortado[i][0]: certeza for i, certeza in posiciones.items()}

