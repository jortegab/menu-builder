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

**The answer is validated against the list we offered, never trusted raw.**
A model that returns a slug we did not offer, a unit that is not a unit, or
prose instead of a line is treated as no answer at all. And even a perfect
answer is only a *suggestion*: it lands as `status='suggested'` and waits
for a person (non-negotiable #4). `mapping_confidence` orders the queue; it
is never a threshold that auto-confirms.
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
    line we cannot fully understand is discarded and the deterministic
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
        # A slug we never offered. Upstream's rule, verbatim: never trust
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
