"""Deterministic product→insumo matching: read a pack size out of an
invoice description, and score how much that description looks like an
existing insumo. Pure -- no DB, no network, no settings.

This is **the fallback that must always work** (PLAN.md §8). The LLM in
`llm_insumo.py` is an accelerator: no API key, a timeout, a network failure
or an unusable answer all land here, and the mapping queue still gets a
suggestion for every product. Nothing in this module is allowed to need a
key, a connection, or a bill.

It also carries more weight than "fallback" suggests, for a measured
reason: **85% of the live catalog has no usable unit** (`unidad_medida` is
`Unid` or `Otros`), and `unidad_comercial` is NULL on 96.7% of purchase
lines. The pack size is in the description text or nowhere, so
`parse_presentacion` is the primary source of a conversion factor, not a
backstop.

Nothing here decides anything on its own. Every output is a *suggestion*
that a person confirms (non-negotiable #4) -- the scores order a queue,
they never cross a threshold into being applied.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from .units import UnknownUnit, convert, normalize_unit

# Category slugs whose products are sold by weight often enough that "kg"
# is the right guess when the description says nothing -- the operator's
# own rule ("meats, chicken and seafood are in kg"), extended to produce
# for the same reason. Deliberately a small, explicit list: a wrong default
# here is a factor-of-1000 costing error, so it errs toward `sin_resolver`
# rather than toward guessing broadly.
CATEGORIAS_POR_KILO = frozenset({
    "alimentos_carnes",
    "alimentos_pollo",
    "alimentos_cerdo",
    "alimentos_pescados_mariscos",
    "alimentos_verduras",
    "alimentos_frutas",
})

# Words that describe packaging rather than the ingredient. Stripped before
# comparing descriptions, so "PECHUGA DESHUESADA BOLSA 5KG" and "FILETE DE
# PECHUGA DESHUESADA" can be seen as the same thing.
#
# **Plurals are listed explicitly, and that is not redundancy.** The set is
# matched whole-word, so "caja" filtered while "cajas" did not, and the
# survivor then counted as evidence about the ingredient: OSOBUCO vs
# OSOBUCO PICADO, CAJAS 3KG APROX (P/KG) scored 0.320 -- under the 0.35
# floor, so the pair was never even offered as the same insumo. Every noun
# here earns its plural.
_RUIDO = frozenset({
    "bolsa", "bolsas", "caja", "cajas", "cj", "cjs", "paq", "paquete",
    "paquetes", "pack", "packs", "bulto", "bultos", "saco", "sacos",
    "bot", "botella", "botellas", "lata", "latas", "pza", "pzas", "pieza",
    "piezas", "und", "unid", "unidad",
    "premium", "extra", "cong", "congelado", "congelada", "fresco", "fresca",
    "mfs", "aprox", "x", "de", "del", "la", "el", "los", "las", "con", "y",
})

# Every spelling `units.normalize_unit` accepts, longest first so the
# alternation cannot match "g" inside "gal" or "l" inside "lt". Sorted in
# code rather than by hand: the hand-ordered version silently lost every
# volume unit to one careless edit, and a parser that stops seeing "ML" is
# a parser that quietly stops finding pack sizes on half the catalog.
_UNIDADES_TEXTO = "|".join(
    sorted(
        (
            "kgs", "kg", "gramos", "gramo", "grs", "gr", "g",
            "libras", "libra", "lbs", "lb",
            "flozs", "floz", "onzas", "onza", "ozs", "oz",
            "mililitros", "mililitro", "mls", "ml",
            "litros", "litro", "lts", "lt", "l",
            "galones", "galon", "gln", "gal", "gl",
            "unidades", "und", "uds", "un", "u",
        ),
        key=len,
        reverse=True,
    )
)

# "1/5kg", "2/500 GR" -- count/size, the form Mayca and Hevar both use.
_RE_FRACCION = re.compile(
    rf"(?<![\d.,])(\d+)\s*/\s*(\d+(?:[.,]\d+)?)\s*({_UNIDADES_TEXTO})\b", re.I
)
# "CJ 24x350ML", "24 X 350 ML" -- count x size.
_RE_POR = re.compile(
    rf"(?<![\d.,])(\d+)\s*[xX]\s*(\d+(?:[.,]\d+)?)\s*({_UNIDADES_TEXTO})\b", re.I
)
# "BOLSA 2.5 KG", "500GR", "1 LT" -- a bare quantity.
_RE_SIMPLE = re.compile(
    rf"(?<![\d.,/])(\d+(?:[.,]\d+)?)\s*({_UNIDADES_TEXTO})\b", re.I
)


def _num(raw: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


def normalizar(texto: str) -> str:
    """Accent-folded, uppercase, punctuation-free. The comparable form of a
    description or an insumo name.
    """
    folded = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s]+", " ", folded).strip()


def tokens_significativos(texto: str) -> list[str]:
    """Words that say what the ingredient *is* -- packaging words, numbers
    and units removed.
    """
    palabras = normalizar(texto).split()
    out = []
    for w in palabras:
        if w in _RUIDO:
            continue
        # Anything starting with a digit is a quantity, not an ingredient:
        # a bare "454", or a number glued to its unit ("1kg", "454gr",
        # "750ml"). The glued form is the one that matters -- it is neither
        # a digit nor a recognised unit on its own, so without this it
        # survived into proposed names as "Paprika 454gr".
        if w[0].isdigit():
            continue
        # A single letter is never an ingredient. These are the debris of
        # a stripped suffix -- "P/KG" leaves a "p", "C/U" a "c", "S/" an
        # "s" -- and 175 of them survive across the live catalog. On a
        # short description they are not harmless: they inflate the token
        # set a Jaccard overlap divides by, which is what pushed the two
        # osobucos apart.
        if len(w) == 1:
            continue
        try:
            normalize_unit(w)
            continue  # it is a unit, not an ingredient word
        except UnknownUnit:
            pass
        out.append(w)
    return out


def parse_presentacion(descripcion: str) -> tuple[Decimal, str] | None:
    """The pack size a description declares, as (cantidad, unidad), or None.

    **Always the total content**, in every shape. The factor a fuente
    carries is how many `unidad_base` one purchase unit yields, and the
    purchase unit is whatever the invoice priced -- so a case of 24 bottles
    is 24 bottles' worth, not one bottle's.

    Three real shapes:

    - `4/2.5 KG`  -> (10, "kg")    four packages **of** 2.5 kg.
    - `24x350ML`  -> (8400, "ml")  twenty-four **times** 350 ml.
    - `2.5 KG`    -> (2.5, "kg")   a bare quantity.

    Both compound forms multiply, and the live catalog settles it rather
    than convention: `MARGARINA BLOQUES CORONA 6/2.08KG (12,5KG)` states
    its own total in the parenthetical, and 6 x 2.08 = 12.48 ~= 12,5. An
    earlier version of this function discarded the count on the slash form,
    which would have costed that margarine at 2.08 kg -- a 6x error, and
    invisible.

    The rule was untestable against this catalog until it was looked at
    properly: **352 of the ~420 slash forms are `1/...`**, where multiplying
    and discarding agree. The other ~65 (`4/2.5 KG`, `24/600 ML`,
    `6/12 UDS`) are where it mattered, and they were all being read wrong.

    A unit must follow immediately, which is what keeps the ratios and
    fractions that pepper real descriptions from being read as pack sizes:
    `CARNE MOLIDA RES 81/19` (a lean/fat ratio) and `BRIOCHE 4 5/8"` (an
    inch fraction) both have no unit after the second number, so neither
    matches -- and the bare-quantity rule then correctly finds the `4.5KG`
    further along the first one.
    """
    texto = descripcion or ""

    m = _RE_FRACCION.search(texto)
    if m:
        count, size = _num(m.group(1)), _num(m.group(2))
        if count and size and count > 0 and size > 0:
            try:
                return count * size, normalize_unit(m.group(3))
            except UnknownUnit:
                pass

    m = _RE_POR.search(texto)
    if m:
        count, size = _num(m.group(1)), _num(m.group(2))
        if count and size and count > 0 and size > 0:
            try:
                return count * size, normalize_unit(m.group(3))
            except UnknownUnit:
                pass

    m = _RE_SIMPLE.search(texto)
    if m:
        size = _num(m.group(1))
        if size is not None and size > 0:
            try:
                return size, normalize_unit(m.group(2))
            except UnknownUnit:
                pass

    return None


def resolve_factor(
    *,
    descripcion: str,
    unidad_base: str,
    unidad_medida: str | None = None,
    categoria_slug: str | None = None,
) -> tuple[Decimal | None, str]:
    """(factor_conversion, factor_origen) -- how many `unidad_base` one
    purchase unit yields, and which rule produced that number.

    The priority chain is PLAN.md §7.2's, minus `manual` (a person's own
    value is never overwritten by any of this, so it never reaches here):

    1. `llm_descripcion` -- handled by the caller when the LLM answers.
    2. `unidad_medida`   -- the XML's own unit, when it is a real one.
    3. `default_categoria` -- carne/pollo/mariscos/produce default to kg.
    4. `sin_resolver`    -- nothing fired.

    This function covers 2-4 plus the *deterministic* reading of the
    description, which is recorded as `llm_descripcion`'s deterministic
    twin: `descripcion`. A `None` factor is never a zero -- the fuente stays
    unusable and sits in the queue asking for a number (non-negotiable #7).
    """
    presentacion = parse_presentacion(descripcion)
    if presentacion is not None:
        cantidad, unidad = presentacion
        try:
            return convert(cantidad, unidad, unidad_base), "descripcion"
        except (UnknownUnit, ValueError):
            # A pack size in a dimension the insumo does not use (a "500 ML"
            # on something measured in grams) is not a factor -- it is a
            # sign the insumo's unidad base is wrong, which is a person's
            # call, not a conversion to force.
            pass

    if unidad_medida:
        try:
            return convert(Decimal(1), unidad_medida, unidad_base), "unidad_medida"
        except (UnknownUnit, ValueError):
            pass

    if categoria_slug in CATEGORIAS_POR_KILO:
        try:
            return convert(Decimal(1), "kg", unidad_base), "default_categoria"
        except (UnknownUnit, ValueError):
            pass

    return None, "sin_resolver"


# Word pairs that mean "these are not the same ingredient", however much
# text they share. Found by running the matcher over the real catalog:
# `PECHUGA CON HUESO` scored 0.47 against the insumo `Pechuga deshuesada`
# and was offered as a match. Those two differ by roughly 30 points of
# merma, so merging them is not a small error -- it is PLAN.md §14's named
# "LLM over-merges" risk, reached by string similarity instead.
#
# Deliberately **conflict pairs, not a list of significant words.** The
# weaker rule ("penalise any qualifier one side lacks") also throws away
# correct matches: the real catalog contains `PECHUGA DE POLLO DESGUESADA`,
# a typo that shares no token with "deshuesada" and is still the same
# ingredient. Conflicts only fire when both sides say something, and say
# opposite things.
_CONFLICTOS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"hueso"}), frozenset({"deshuesada", "deshuesado", "filete", "filet"})),
    (frozenset({"entero", "entera"}), frozenset({"molido", "molida", "rebanado", "rebanada"})),
    (frozenset({"crudo", "cruda"}), frozenset({"cocido", "cocida", "ahumado", "ahumada"})),
)


def _hay_conflicto(a: set[str], b: set[str]) -> bool:
    """True when the two token sets assert opposite things about the same
    ingredient -- one says "con hueso" and the other "deshuesada".
    """
    for izq, der in _CONFLICTOS:
        if (a & izq and b & der) or (b & izq and a & der):
            return True
    return False


def conflictivos(a: str, b: str) -> bool:
    """Whether two descriptions actively contradict each other.

    The public face of `_hay_conflicto`, exposed so a *different* proposer
    can be vetoed by the same rule. `similitud` applies it internally; when
    the LLM proposes a grouping instead, `mapeo.parecidos` applies it to
    that proposal, so "PECHUGA CON HUESO is not Pechuga deshuesada" stays
    one rule in one place no matter who suggested the merge.
    """
    return _hay_conflicto(set(tokens_significativos(a)), set(tokens_significativos(b)))


def similitud(descripcion: str, nombre_insumo: str) -> float:
    """0..1 -- how much a catalog description looks like an insumo name.

    Two signals, deliberately combined rather than either alone:

    - **Token overlap** catches word order and extra packaging words, which
      is most of the real difference ("FILETE DE PECHUGA DESHUESADA" vs
      "Pechuga deshuesada").
    - **Sequence ratio** catches spelling drift and truncation, which token
      overlap scores as a flat zero ("PECHUGA DESH." vs "Pechuga deshuesada").

    Weighted toward tokens because a shared *word* is much stronger evidence
    than shared letters: "PECHUGA" and "PESCADO" are 57% similar as strings
    and share nothing that matters.
    """
    a, b = tokens_significativos(descripcion), tokens_significativos(nombre_insumo)
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    if _hay_conflicto(sa, sb):
        # Not "less similar" -- not the same thing. Returning 0 rather than
        # a reduced score keeps it below any floor a caller might pick.
        return 0.0
    overlap = len(sa & sb) / len(sa | sb)
    ratio = difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    return round(0.7 * overlap + 0.3 * ratio, 3)


def mejor_match(
    descripcion: str, candidatos: list[tuple[int, str]], *, minimo: float = 0.35
) -> tuple[int, float] | None:
    """The best (insumo_id, score) above `minimo`, or None.

    `minimo` exists to keep the queue honest rather than to decide anything:
    below it the suggestion is worse than no suggestion, and offering one
    trains the operator to click through without reading. It is never a
    threshold for *applying* a mapping -- that always takes a person.
    """
    mejor: tuple[int, float] | None = None
    for insumo_id, nombre in candidatos:
        score = similitud(descripcion, nombre)
        if score >= minimo and (mejor is None or score > mejor[1]):
            mejor = (insumo_id, score)
    return mejor
