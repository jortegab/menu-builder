"""The DB-facing half of costing: turn an insumo's confirmed sources into a
price series, hand it to `costing.py`, and keep everything needed to explain
the answer.

`costing.py` owns the arithmetic and stays pure. This module owns the
gathering, which is where the judgement calls live:

- **Only `confirmed` *and* `activo` sources contribute** (non-negotiable
  #4). A suggestion pending review is not evidence, and a source the
  operator switched off is a supplier they stopped buying from -- keeping
  its old prices in the series would quietly hold the cost down.
- **A source with no `factor_conversion` contributes nothing and is named.**
  Never treated as 1: that would cost the insumo per *package* instead of
  per gram, which reads as a plausible number and is wrong by the pack size
  (non-negotiable #7).
- **An observation with no CRC price is dropped and counted.** That is a USD
  line whose date had no exchange rate upstream; substituting the USD figure
  would understate it by ~500x.

Every one of those exclusions is *reported*, not silently applied --
`Explicacion` carries them, and the "¿Por qué este costo?" panel renders
them. An insumo that costs less than expected because half its sources have
no factor is a question the screen should answer before it is asked.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CatalogPricePoint, CatalogProduct, Insumo, InsumoFuente
from .costing import DEFAULT_METHOD, DEFAULT_WINDOW_DAYS, CostResult, Observation, costo_unitario


@dataclass
class FuenteVista:
    """One source, as the explanation panel shows it."""

    fuente: InsumoFuente
    producto: CatalogProduct
    observaciones: int
    usable: bool          # confirmed, active, and has a factor
    motivo: str | None    # why not, when not


@dataclass
class ObservacionVista:
    """One priced purchase, traceable back to the invoice line it came from."""

    fecha: dt.date
    proveedor: str
    descripcion: str
    clave: str
    precio_normalizado: Decimal
    factor: Decimal
    precio_por_unidad_uso: Decimal
    en_ventana: bool


@dataclass
class Explicacion:
    """Everything the operator needs to believe (or dispute) the number.

    This is PLAN.md §7.6 made concrete. It is not debug output: every field
    appears on screen, and the panel is what makes a figure nobody computed
    by hand trustworthy.
    """

    costo: CostResult
    metodo: str
    ventana_dias: int
    merma_pct: Decimal
    fuentes: list[FuenteVista] = field(default_factory=list)
    observaciones: list[ObservacionVista] = field(default_factory=list)
    descartadas_sin_tipo_cambio: int = 0
    fuentes_sin_factor: int = 0

    @property
    def sin_precio(self) -> bool:
        return self.costo.valor is None

    @property
    def hay_advertencias(self) -> bool:
        return bool(
            self.descartadas_sin_tipo_cambio
            or self.fuentes_sin_factor
            or self.costo.precio_viejo
        )


def _config(insumo: Insumo, ajustes) -> tuple[str, int]:
    """The method and window actually in force: the insumo's own, else the
    app default. NULL means "follow the default", not "unset" -- an insumo
    that never had an opinion tracks the global setting as it changes.
    """
    metodo = insumo.metodo_costeo or (
        getattr(ajustes, "metodo_costeo_default", None) or DEFAULT_METHOD
    )
    ventana = insumo.ventana_dias or (
        getattr(ajustes, "ventana_dias_default", None) or DEFAULT_WINDOW_DAYS
    )
    return metodo, ventana


def explicar(
    db: Session, insumo: Insumo, *, ajustes=None, hoy: dt.date | None = None
) -> Explicacion:
    """Cost this insumo and show the work."""
    hoy = hoy or dt.date.today()
    metodo, ventana = _config(insumo, ajustes)

    filas = db.execute(
        select(InsumoFuente, CatalogProduct)
        .join(CatalogProduct, CatalogProduct.id == InsumoFuente.catalog_product_id)
        .where(InsumoFuente.insumo_id == insumo.id)
        .order_by(CatalogProduct.description)
    ).all()

    fuentes: list[FuenteVista] = []
    observaciones: list[ObservacionVista] = []
    serie: list[Observation] = []
    descartadas = 0
    sin_factor = 0
    desde = hoy - dt.timedelta(days=ventana)

    for fuente, producto in filas:
        motivo = None
        usable = True
        if fuente.status != "confirmed":
            usable, motivo = False, "sin confirmar"
        elif not fuente.activo:
            usable, motivo = False, "desactivada"
        elif fuente.factor_conversion is None:
            usable, motivo = False, "falta el factor"
            sin_factor += 1

        puntos = []
        if usable:
            puntos = list(
                db.scalars(
                    select(CatalogPricePoint)
                    .where(CatalogPricePoint.catalog_product_id == producto.id)
                    .order_by(CatalogPricePoint.fecha)
                )
            )
            for punto in puntos:
                precio_base = punto.precio_efectivo_normalizado_crc
                if precio_base is None:
                    # A USD line with no exchange rate on file upstream.
                    descartadas += 1
                    continue
                factor = fuente.factor_conversion
                precio_uso = precio_base / factor
                serie.append(
                    Observation(
                        fecha=punto.fecha,
                        precio_por_unidad_uso=precio_uso,
                        cantidad_unidad_uso=(punto.cantidad or Decimal(0)) * factor,
                        fuente_id=fuente.id,
                        catalog_price_point_id=punto.id,
                    )
                )
                observaciones.append(
                    ObservacionVista(
                        fecha=punto.fecha,
                        proveedor=producto.supplier_nombre,
                        descripcion=producto.description,
                        clave=punto.clave,
                        precio_normalizado=precio_base,
                        factor=factor,
                        precio_por_unidad_uso=precio_uso,
                        en_ventana=punto.fecha >= desde,
                    )
                )

        fuentes.append(
            FuenteVista(
                fuente=fuente,
                producto=producto,
                observaciones=len([p for p in puntos if p.precio_efectivo_normalizado_crc is not None]),
                usable=usable,
                motivo=motivo,
            )
        )

    costo = costo_unitario(
        serie,
        merma_pct=insumo.merma_pct or Decimal(0),
        metodo=metodo,
        ventana_dias=ventana,
        hoy=hoy,
    )

    observaciones.sort(key=lambda o: o.fecha, reverse=True)
    return Explicacion(
        costo=costo,
        metodo=metodo,
        ventana_dias=ventana,
        merma_pct=insumo.merma_pct or Decimal(0),
        fuentes=fuentes,
        observaciones=observaciones,
        descartadas_sin_tipo_cambio=descartadas,
        fuentes_sin_factor=sin_factor,
    )


def costo(db: Session, insumo: Insumo, *, ajustes=None, hoy: dt.date | None = None) -> CostResult:
    """Just the number, for callers that do not need the whole explanation
    (the recipe engine, step 1.5).
    """
    return explicar(db, insumo, ajustes=ajustes, hoy=hoy).costo


def costos(
    db: Session, insumos: list[Insumo], *, ajustes=None, hoy: dt.date | None = None
) -> dict[int, CostResult]:
    """{insumo_id: cost} for a list -- what the Insumos list column needs.

    Deliberately a loop over `explicar` rather than one clever query: the
    costing method varies per insumo, and a version of this that computed
    six different aggregates in SQL would be a second implementation of
    `costing.py` living in a different language. If this ever becomes slow,
    the fix is caching the result, not reimplementing the math.
    """
    return {i.id: costo(db, i, ajustes=ajustes, hoy=hoy) for i in insumos}
