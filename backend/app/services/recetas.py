"""Receta master data and the lines that make it a graph.

Same shape as `insumos.py` -- this module owns *writing* recetas and
validating what goes in; `receta_costs.py` owns reading them back out as a
cost, the same way `insumo_costs.py` does for an insumo.

**Everything structural is refused here, at write time, with a message that
names the offending thing.** Four rules, and each one exists because the
alternative is a plausible-looking cost that is wrong or a page that hangs:

- **A cycle is rejected with its path spelled out** (non-negotiable #5).
  "Salsa criolla -> Fondo oscuro -> Salsa criolla" is fixable; "referencia
  circular" is not. `graph.would_create_cycle` decides, this module names
  the nodes, and `graph.cost_recipe` defends itself again on read.
- **A nest deeper than `graph.MAX_DEPTH` is rejected too.** The read side
  raises `RecipeTooDeep` and would leave the operator with a plate that
  cannot be opened; refusing the line keeps the failure at the moment they
  can still fix it. Both ends of the new edge count -- a two-level salsa
  under a plato already two levels down is what overflows, and neither half
  looks deep on its own.
- **Only a preparación can be a component.** A plato has portions, not a
  yield, so there is no "per gram of plato" for a line to multiply by --
  costing it would silently produce no cost rather than an error.
- **A line's unit must be compatible with what its component is costed
  in** (D30). The line chooses its own unit freely *within the dimension*:
  kg/g/lb for a pechuga costed per gram, L/ml/oz for an aceite costed per
  ml. Crossing mass and volume needs a density nobody has recorded, so it
  is refused here and the message names both units.

Validation lives here rather than in the CHECK constraints for the reason
`insumos.py` gives at length: a constraint violation arrives as an opaque
`IntegrityError` at commit time and the operator's half-filled form is
gone. The constraints stay as the last line of defence.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import Insumo, Receta, RecetaLinea
from . import graph
from .insumos import ValidationError, parse_decimal, slugify
from .units import IncompatibleUnits, UnknownUnit, dimension_of, normalize_unit

TIPOS = ("plato", "preparacion")

# What `rendimiento_unidad` may be. A preparación is consumed by other
# recipes *by the gram* (or the ml, or the unit), so its yield has to be
# expressed in something a line can convert into -- the same canonical
# units an insumo is costed in, for the same reason (§7.1.1).
UNIDADES_RENDIMIENTO = ("g", "ml", "unidad")

MAX_PORCIONES = 999


# --------------------------------------------------------------------------
# Master data
# --------------------------------------------------------------------------


def unique_slug(db: Session, nombre: str, *, exclude_id: int | None = None) -> str:
    """`slugify` plus a numeric suffix when taken. Two recetas legitimately
    share a name -- "Salsa criolla" as a preparación and as a plato de
    guarnición -- and failing the save over it would be wrong: the name is
    not the identity, the row is.
    """
    base = slugify(nombre)
    candidato, n = base, 1
    while True:
        stmt = select(Receta.id).where(Receta.slug == candidato)
        if exclude_id is not None:
            stmt = stmt.where(Receta.id != exclude_id)
        if db.scalar(stmt) is None:
            return candidato
        n += 1
        candidato = f"{base}-{n}"


def validate(
    *,
    nombre: str,
    tipo: str,
    porciones: str = "",
    rendimiento_cantidad: str = "",
    rendimiento_unidad: str = "",
    categoria_menu: str = "",
    descripcion: str = "",
    notas_produccion: str = "",
) -> dict:
    """Normalized field values, or `ValidationError` with per-field messages.

    The plato/preparación split is the whole reason the two live in one
    table (D8), and it is enforced by *clearing* the other side rather than
    trusting the form not to send it: a plato that kept a stale
    `rendimiento_cantidad` from before its tipo was switched would violate
    the table's own CHECK at commit time.
    """
    errors: dict[str, str] = {}

    nombre = (nombre or "").strip()
    if not nombre:
        errors["nombre"] = "Poné un nombre."
    elif len(nombre) > 200:
        errors["nombre"] = "El nombre no puede pasar de 200 caracteres."

    if tipo not in TIPOS:
        errors["tipo"] = "Elegí si es un plato o una preparación."

    porciones_val: int | None = None
    cantidad_val: Decimal | None = None
    unidad_val: str | None = None

    if tipo == "plato":
        raw = (porciones or "").strip()
        if not raw:
            errors["porciones"] = "Un plato tiene que decir en cuántas porciones sale."
        else:
            try:
                porciones_val = int(raw)
            except ValueError:
                errors["porciones"] = "Las porciones son un número entero."
            else:
                if porciones_val < 1:
                    errors["porciones"] = "Tiene que ser al menos 1 porción."
                elif porciones_val > MAX_PORCIONES:
                    errors["porciones"] = f"Máximo {MAX_PORCIONES} porciones."
    elif tipo == "preparacion":
        try:
            cantidad_val = parse_decimal(rendimiento_cantidad)
        except ValueError:
            errors["rendimiento_cantidad"] = (
                "El rendimiento tiene que ser un número, por ejemplo 2500 o 2,5."
            )
        else:
            if cantidad_val is None:
                errors["rendimiento_cantidad"] = (
                    "Decí cuánto rinde. Es lo que hace que otras recetas la "
                    "puedan pedir por gramo."
                )
            elif cantidad_val <= 0:
                errors["rendimiento_cantidad"] = "El rendimiento tiene que ser mayor a 0."
        if rendimiento_unidad not in UNIDADES_RENDIMIENTO:
            errors["rendimiento_unidad"] = "Elegí la unidad del rendimiento."
        else:
            unidad_val = rendimiento_unidad

    if errors:
        raise ValidationError(errors)

    return {
        "nombre": nombre,
        "tipo": tipo,
        "porciones": porciones_val,
        "rendimiento_cantidad": cantidad_val,
        "rendimiento_unidad": unidad_val,
        "categoria_menu": (categoria_menu or "").strip() or None,
        "descripcion": (descripcion or "").strip() or None,
        "notas_produccion": (notas_produccion or "").strip() or None,
    }


def create(db: Session, **form) -> Receta:
    fields = validate(**form)
    receta = Receta(slug=unique_slug(db, fields["nombre"]), **fields)
    db.add(receta)
    db.commit()
    return receta


def update(db: Session, receta: Receta, **form) -> Receta:
    fields = validate(**form)

    # Turning a preparación into a plato takes away the yield other recipes
    # multiply by, so every line that consumes it would resolve to no cost
    # and those plates would go `incompleto` without anyone touching them.
    # Refused while it is in use, and the message names who uses it.
    if receta.tipo == "preparacion" and fields["tipo"] == "plato":
        consumidores = usos(db, receta)
        if consumidores:
            raise ValidationError(
                {
                    "tipo": (
                        "No se puede convertir en plato: "
                        + ", ".join(r.nombre for r in consumidores[:3])
                        + (" y otras" if len(consumidores) > 3 else "")
                        + " la usan como ingrediente. Quitala de ahí primero."
                    )
                }
            )

    # Changing the yield unit of a preparación in use would leave its
    # consumers converting into a different dimension -- 45 g of something
    # now measured in ml. Refused for the same reason, and the message says
    # which recipes to fix.
    if (
        receta.tipo == "preparacion"
        and fields["tipo"] == "preparacion"
        and receta.rendimiento_unidad
        and fields["rendimiento_unidad"]
    ):
        conflictivas = _consumidores_incompatibles(
            db, receta.id, fields["rendimiento_unidad"]
        )
        if conflictivas:
            raise ValidationError(
                {
                    "rendimiento_unidad": (
                        f"No se puede pasar de {receta.rendimiento_unidad} a "
                        f"{fields['rendimiento_unidad']}: son magnitudes distintas y "
                        + ", ".join(conflictivas[:3])
                        + " ya la piden en la otra. Corregí esas líneas primero."
                    )
                }
            )

    if fields["nombre"] != receta.nombre:
        receta.slug = unique_slug(db, fields["nombre"], exclude_id=receta.id)
    for key, value in fields.items():
        setattr(receta, key, value)
    db.commit()
    return receta


def _consumidores_incompatibles(
    db: Session, receta_id: int, unidad_nueva: str
) -> list[str]:
    """The recetas whose lines would stop converting if this preparación's
    yield unit became `unidad_nueva` -- named, so the operator knows exactly
    which lines to fix rather than being told "no".
    """
    nueva = dimension_of(unidad_nueva)
    rows = db.execute(
        select(Receta.nombre, RecetaLinea.unidad)
        .join(RecetaLinea, RecetaLinea.receta_id == Receta.id)
        .where(RecetaLinea.componente_receta_id == receta_id)
    ).all()
    return sorted({nombre for nombre, unidad in rows if dimension_of(unidad) != nueva})


def set_activo(db: Session, receta: Receta, activo: bool) -> Receta:
    """Archive or restore. Never delete: a preparación can be referenced by
    another receta's line, and deleting one would either orphan that recipe
    or silently change what a plate costs.
    """
    receta.activo = activo
    db.commit()
    return receta


def listar(
    db: Session, *, tipo: str | None = None, q: str = "", activo: bool | None = True
) -> list[Receta]:
    stmt = select(Receta).options(selectinload(Receta.lineas))
    if tipo is not None:
        stmt = stmt.where(Receta.tipo == tipo)
    if activo is not None:
        stmt = stmt.where(Receta.activo == activo)
    if q.strip():
        stmt = stmt.where(func.lower(Receta.nombre).like(f"%{q.strip().lower()}%"))
    return list(db.scalars(stmt.order_by(Receta.nombre)))


def usos(db: Session, receta: Receta) -> list[Receta]:
    """The recetas that consume this one as an ingredient -- "usada en N
    recetas", which is the first question anyone asks about a salsa before
    changing it.
    """
    return list(
        db.scalars(
            select(Receta)
            .join(RecetaLinea, RecetaLinea.receta_id == Receta.id)
            .where(RecetaLinea.componente_receta_id == receta.id)
            .distinct()
            .order_by(Receta.nombre)
        )
    )


def usos_count(db: Session, receta_ids: list[int]) -> dict[int, int]:
    """{receta_id: how many recetas use it} for a whole list in one query."""
    if not receta_ids:
        return {}
    rows = db.execute(
        select(RecetaLinea.componente_receta_id, func.count(func.distinct(RecetaLinea.receta_id)))
        .where(RecetaLinea.componente_receta_id.in_(receta_ids))
        .group_by(RecetaLinea.componente_receta_id)
    ).all()
    return {rid: n for rid, n in rows}


# --------------------------------------------------------------------------
# Lines -- where the graph guards live
# --------------------------------------------------------------------------


def adyacencia(db: Session) -> dict[int, set[int]]:
    """{receta_id: the recetas it directly contains}, for the whole app.

    Read fresh on every write rather than cached: it is one small query
    (there are tens of recetas, not thousands), and a stale adjacency is
    exactly how a cycle gets past the guard that exists to stop it.
    """
    ady: dict[int, set[int]] = {rid: set() for rid in db.scalars(select(Receta.id))}
    rows = db.execute(
        select(RecetaLinea.receta_id, RecetaLinea.componente_receta_id).where(
            RecetaLinea.componente_receta_id.is_not(None)
        )
    ).all()
    for padre, hijo in rows:
        ady.setdefault(padre, set()).add(hijo)
    return ady


def unidad_del_componente(db: Session, tipo_componente: str, componente_id: int) -> str | None:
    """The unit a component is *costed* in -- `Insumo.unidad_base` for an
    insumo, `rendimiento_unidad` for a preparación. What a line's own unit
    has to be compatible with, and what `units.compatible_units` is asked
    about to build the selector.
    """
    if tipo_componente == "insumo":
        insumo = db.get(Insumo, componente_id)
        return insumo.unidad_base if insumo else None
    receta = db.get(Receta, componente_id)
    return receta.rendimiento_unidad if receta else None


def _nombres(db: Session, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {
        rid: nombre
        for rid, nombre in db.execute(
            select(Receta.id, Receta.nombre).where(Receta.id.in_(ids))
        ).all()
    }


def _validar_componente(
    db: Session, receta: Receta, tipo_componente: str, componente_id: int
) -> tuple[str, str]:
    """(nombre, unidad de costeo) of a legal component, or `ValidationError`.

    This is the whole structural guard for one line, in the order the
    operator would want to hear about the problems.
    """
    if tipo_componente not in ("insumo", "receta"):
        raise ValidationError({"componente": "Elegí un insumo o una preparación."})

    if tipo_componente == "insumo":
        insumo = db.get(Insumo, componente_id)
        if insumo is None:
            raise ValidationError({"componente": "Ese insumo no existe."})
        if not insumo.activo:
            raise ValidationError(
                {
                    "componente": (
                        f"«{insumo.nombre}» está archivado. Reactivalo si lo "
                        "vas a volver a usar."
                    )
                }
            )
        return insumo.nombre, insumo.unidad_base

    componente = db.get(Receta, componente_id)
    if componente is None:
        raise ValidationError({"componente": "Esa receta no existe."})
    if componente.id == receta.id:
        raise ValidationError(
            {"componente": f"«{receta.nombre}» no puede contenerse a sí misma."}
        )
    if componente.tipo != "preparacion":
        # A plato declares portions, not a yield, so there is no cost per
        # gram for a line to multiply by. Refused here rather than left to
        # produce a silently uncosted line.
        raise ValidationError(
            {
                "componente": (
                    f"«{componente.nombre}» es un plato, no una preparación. "
                    "Solo una preparación — que declara cuánto rinde — se "
                    "puede usar como ingrediente."
                )
            }
        )
    if not componente.rendimiento_unidad or not componente.rendimiento_cantidad:
        raise ValidationError(
            {
                "componente": (
                    f"«{componente.nombre}» todavía no dice cuánto rinde, así que "
                    "no se puede saber cuánto vale un gramo de ella."
                )
            }
        )

    ady = adyacencia(db)
    ciclo = graph.would_create_cycle(ady, receta.id, componente.id)
    if ciclo:
        nombres = _nombres(db, ciclo)
        raise ValidationError(
            {
                "componente": (
                    "Eso haría un círculo: "
                    + " → ".join(nombres.get(n, f"receta {n}") for n in ciclo)
                    + ". Una receta no puede terminar conteniéndose a sí misma."
                )
            }
        )

    niveles = graph.depth_through_edge(ady, receta.id, componente.id)
    if niveles > graph.MAX_DEPTH:
        raise ValidationError(
            {
                "componente": (
                    f"Eso anidaría {niveles} niveles de recetas y el máximo es "
                    f"{graph.MAX_DEPTH}. Revisá si alguna preparación de esta "
                    "cadena se puede aplanar."
                )
            }
        )

    return componente.nombre, componente.rendimiento_unidad


def _validar_cantidad_unidad(
    cantidad: str, unidad: str, unidad_componente: str, nombre_componente: str
) -> tuple[Decimal, str]:
    errors: dict[str, str] = {}

    valor: Decimal | None = None
    try:
        valor = parse_decimal(cantidad)
    except ValueError:
        errors["cantidad"] = "La cantidad tiene que ser un número, por ejemplo 180 o 0,5."
    else:
        if valor is None:
            errors["cantidad"] = "Poné una cantidad."
        elif valor <= 0:
            errors["cantidad"] = "La cantidad tiene que ser mayor a 0."

    unidad_norm = ""
    try:
        unidad_norm = normalize_unit(unidad)
    except UnknownUnit:
        errors["unidad"] = "Elegí una unidad de la lista."
    else:
        try:
            # Only the dimensions have to agree; the actual conversion
            # happens at costing time. This is D30: the line picks its own
            # unit, and the only thing it may not do is cross magnitudes.
            if dimension_of(unidad_norm) != dimension_of(unidad_componente):
                raise IncompatibleUnits(unidad_norm, unidad_componente)
        except IncompatibleUnits as exc:
            errors["unidad"] = (
                f"No se puede convertir de {exc.desde} a {exc.hacia}: son magnitudes "
                f"distintas. «{nombre_componente}» se costea en {unidad_componente}."
            )

    if errors:
        raise ValidationError(errors)
    return valor, unidad_norm  # type: ignore[return-value]


def agregar_linea(
    db: Session,
    receta: Receta,
    *,
    tipo_componente: str,
    componente_id: int,
    cantidad: str,
    unidad: str = "",
    nota: str = "",
) -> RecetaLinea:
    """Append one line, after every structural guard has passed.

    `unidad` defaults to the component's own costing unit, which is what a
    drag-and-drop from the palette sends: the operator picked a thing, not
    a unit, and grams-for-a-pechuga is the answer that needs no thought.
    """
    nombre, unidad_componente = _validar_componente(
        db, receta, tipo_componente, componente_id
    )
    if unidad_componente is None:  # pragma: no cover - guarded above
        raise ValidationError({"componente": "Ese componente no tiene unidad de costeo."})
    valor, unidad_norm = _validar_cantidad_unidad(
        cantidad, unidad or unidad_componente, unidad_componente, nombre
    )

    siguiente = (
        db.scalar(
            select(func.coalesce(func.max(RecetaLinea.orden), -1)).where(
                RecetaLinea.receta_id == receta.id
            )
        )
        + 1
    )
    linea = RecetaLinea(
        receta_id=receta.id,
        orden=siguiente,
        tipo_componente=tipo_componente,
        insumo_id=componente_id if tipo_componente == "insumo" else None,
        componente_receta_id=componente_id if tipo_componente == "receta" else None,
        cantidad=valor,
        unidad=unidad_norm,
        nota=(nota or "").strip() or None,
    )
    db.add(linea)
    db.commit()
    return linea


def actualizar_linea(
    db: Session, linea: RecetaLinea, *, cantidad: str, unidad: str = "", nota: str | None = None
) -> RecetaLinea:
    """Change a line's quantity or unit. The component itself is never
    edited in place -- swapping what a line points at is a delete and an
    add, so the cycle and depth guards always run on a fresh edge.
    """
    componente_id = (
        linea.insumo_id if linea.tipo_componente == "insumo" else linea.componente_receta_id
    )
    unidad_componente = unidad_del_componente(db, linea.tipo_componente, componente_id)
    if unidad_componente is None:
        raise ValidationError({"componente": "El componente de esta línea ya no existe."})
    nombre = nombre_componente(db, linea) or "el componente"

    valor, unidad_norm = _validar_cantidad_unidad(
        cantidad, unidad or linea.unidad, unidad_componente, nombre
    )
    linea.cantidad = valor
    linea.unidad = unidad_norm
    if nota is not None:
        linea.nota = nota.strip() or None
    db.commit()
    return linea


def nombre_componente(db: Session, linea: RecetaLinea) -> str | None:
    if linea.tipo_componente == "insumo":
        insumo = db.get(Insumo, linea.insumo_id)
        return insumo.nombre if insumo else None
    componente = db.get(Receta, linea.componente_receta_id)
    return componente.nombre if componente else None


def eliminar_linea(db: Session, linea: RecetaLinea) -> None:
    receta_id = linea.receta_id  # read before the row goes away
    db.delete(linea)
    db.commit()
    _compactar_orden(db, receta_id)


def _compactar_orden(db: Session, receta_id: int) -> None:
    """Renumber 0..n-1 after a delete or a move, so `orden` never grows
    holes that a later reorder would have to reason about.
    """
    lineas = list(
        db.scalars(
            select(RecetaLinea)
            .where(RecetaLinea.receta_id == receta_id)
            .order_by(RecetaLinea.orden, RecetaLinea.id)
        )
    )
    for i, linea in enumerate(lineas):
        linea.orden = i
    db.commit()


def reordenar(db: Session, receta: Receta, orden_ids: list[int]) -> None:
    """Apply a new line order, given the line ids in the order they should
    appear.

    Ids that do not belong to this receta are ignored and lines the caller
    did not mention keep their relative position at the end -- a drop that
    raced a delete in another tab must not silently drop rows or renumber
    someone else's recipe.
    """
    lineas = {
        ln.id: ln
        for ln in db.scalars(select(RecetaLinea).where(RecetaLinea.receta_id == receta.id))
    }
    posicion = 0
    for lid in orden_ids:
        linea = lineas.pop(lid, None)
        if linea is not None:
            linea.orden = posicion
            posicion += 1
    for linea in sorted(lineas.values(), key=lambda ln: (ln.orden, ln.id)):
        linea.orden = posicion
        posicion += 1
    db.commit()


def mover_linea(db: Session, linea: RecetaLinea, delta: int) -> None:
    """Move one line up (-1) or down (+1).

    The keyboard path for what drag-and-drop does with a mouse. Drag is the
    fast way to reorder, never the only way -- a keyboard user and a
    trackpad that fights back both need these buttons.
    """
    lineas = list(
        db.scalars(
            select(RecetaLinea)
            .where(RecetaLinea.receta_id == linea.receta_id)
            .order_by(RecetaLinea.orden, RecetaLinea.id)
        )
    )
    ids = [ln.id for ln in lineas]
    if linea.id not in ids:  # pragma: no cover - the row was just loaded
        return
    i = ids.index(linea.id)
    j = i + delta
    if not 0 <= j < len(ids):
        return
    ids[i], ids[j] = ids[j], ids[i]
    reordenar(db, linea.receta, ids)
