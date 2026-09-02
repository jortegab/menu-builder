"""Unit conversion for recipe quantities. Pure -- no DB, no network, no
settings import, kept dependency-free for the same reason upstream keeps
`currency.py` out of `exchange_rate.py`: the math stays importable and
unit-testable without a database driver.

**Conversion happens only within one dimension** (PLAN.md non-negotiable
#6). Grams do not become millilitres. Every kitchen "knows" that a litre of
water is a kilo, and every kitchen is wrong about oil, honey, cream and
flour by up to 20% -- an error that is invisible on screen and shows up
only as a margin that never quite matches. Crossing dimensions needs an
explicit per-insumo `densidad_g_por_ml`, which is a real number nobody
should invent on the app's behalf, so until one is recorded the conversion
is refused rather than guessed.

`unidad` (count) converts to nothing at all, not even to itself from
another dimension: 3 huevos is not 3 g.
"""

from __future__ import annotations

from decimal import Decimal

MASA = "masa"
VOLUMEN = "volumen"
CONTEO = "conteo"

# {unit: (dimension, how many canonical units one of this unit is worth)}.
# Canonical is `g` for masa, `ml` for volumen, `unidad` for conteo -- the
# smallest unit a recipe realistically calls for, so a conversion into
# canonical is the one that never loses precision to a fraction.
_UNITS: dict[str, tuple[str, Decimal]] = {
    "mg": (MASA, Decimal("0.001")),
    "g": (MASA, Decimal(1)),
    "kg": (MASA, Decimal(1000)),
    "ml": (VOLUMEN, Decimal(1)),
    "l": (VOLUMEN, Decimal(1000)),
    "unidad": (CONTEO, Decimal(1)),
}

CANONICAL: dict[str, str] = {MASA: "g", VOLUMEN: "ml", CONTEO: "unidad"}


class UnknownUnit(ValueError):
    """A unit string this app does not model. Raised rather than defaulted:
    silently treating an unrecognized unit as `unidad` would cost a recipe
    line at 1x whatever the price happens to be.
    """


class IncompatibleUnits(ValueError):
    """A conversion across dimensions. Carries both units so the caller can
    put them in the operator-facing message -- an error that says "no se
    puede convertir de ml a g" is actionable; "unidad inválida" is not.
    """

    def __init__(self, desde: str, hacia: str) -> None:
        self.desde = desde
        self.hacia = hacia
        super().__init__(f"No se puede convertir de {desde} a {hacia}: son magnitudes distintas")


def normalize_unit(unit: str) -> str:
    """Lowercased and stripped, with the handful of spellings that actually
    show up on invoices and in operator typing folded in (`Kg`, `KG`, `lt`,
    `Lt`, `un`, `und`, `u`). Deliberately narrow -- this is a spelling
    table, not a parser; anything not listed raises rather than being
    guessed at.
    """
    u = unit.strip().lower()
    aliases = {
        "kgs": "kg",
        "grs": "g",
        "gr": "g",
        "gramo": "g",
        "gramos": "g",
        "kilo": "kg",
        "kilos": "kg",
        "kilogramo": "kg",
        "kilogramos": "kg",
        "lt": "l",
        "lts": "l",
        "litro": "l",
        "litros": "l",
        "mls": "ml",
        "mililitro": "ml",
        "mililitros": "ml",
        "un": "unidad",
        "und": "unidad",
        "uds": "unidad",
        "u": "unidad",
        "unidades": "unidad",
    }
    u = aliases.get(u, u)
    if u not in _UNITS:
        raise UnknownUnit(f"Unidad desconocida: {unit!r}")
    return u


def dimension_of(unit: str) -> str:
    """The dimension (`masa`/`volumen`/`conteo`) a unit belongs to."""
    return _UNITS[normalize_unit(unit)][0]


def compatible_units(unit: str) -> list[str]:
    """Every unit a quantity in `unit` may be expressed in -- what the
    builder screen's unit selector offers next to a quantity input, so an
    incompatible pair is unreachable through the UI rather than merely
    rejected after the fact.
    """
    dim = dimension_of(unit)
    return [u for u, (d, _) in _UNITS.items() if d == dim]


def convert(cantidad: Decimal, desde: str, hacia: str) -> Decimal:
    """`cantidad` expressed in `hacia`. Raises `IncompatibleUnits` across
    dimensions (never a silent 1:1) and `UnknownUnit` on an unmodelled unit.

    Not quantized: a recipe line of 0.5 kg into g is exactly 500, and
    1 g into kg is exactly 0.001, but 1 unidad split three ways is not a
    figure this function should be rounding on the caller's behalf --
    rounding belongs at the point a number is displayed or stored, where
    the caller knows how many decimals mean something.
    """
    d, h = normalize_unit(desde), normalize_unit(hacia)
    dim_d, factor_d = _UNITS[d]
    dim_h, factor_h = _UNITS[h]
    if dim_d != dim_h:
        raise IncompatibleUnits(d, h)
    if d == h:
        return cantidad
    return cantidad * factor_d / factor_h


def to_canonical(cantidad: Decimal, unit: str) -> tuple[Decimal, str]:
    """(cantidad, unidad) in the dimension's canonical unit -- `g`, `ml` or
    `unidad`. The form every stored quantity and every costing input is
    reduced to, so that two figures are only ever compared after they are
    already in the same unit.
    """
    canon = CANONICAL[dimension_of(unit)]
    return convert(cantidad, unit, canon), canon
