"""The data model of PLAN.md §9, in three layers:

1. **The catalog mirror** (`catalog_*`) -- a projection of
   restaurant-manager's product catalog and price history, pulled through
   its read-only `/api/v1`. Derived, disposable, rebuildable by re-syncing,
   and **never hand-edited** (non-negotiable #3). Nothing in this app may
   hold data that exists only here.
2. **Insumos** -- the canonical, cross-supplier ingredient layer upstream
   designed (its PLAN.md §5.3) and deliberately never built, plus the
   things a supplier's product row cannot know: unidad de uso, merma, and
   which price to believe.
3. **Recetas** -- preparaciones and platos in one table (D8), and their
   lines, which form a DAG.

Domain nouns keep their Spanish names (`insumo`, `receta`) because they are
the operator's own vocabulary; enum *values* are English so they read like
every other status in both apps (D3).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------
# 1. Catalog mirror -- projection of restaurant-manager. Never hand-edited.
# --------------------------------------------------------------------------


class CatalogProduct(Base):
    """One supplier-scoped product, mirrored from upstream's `product`.

    Joined on `internal_code`, not `upstream_product_id`: upstream's own
    docstring calls `internal_code` the stable, unique, human-meaningful
    key ("{supplier_id}-{supplier_code}"), and keying on it means a mapping
    survives anything that could renumber rows upstream.
    """

    __tablename__ = "catalog_product"

    id: Mapped[int] = mapped_column(primary_key=True)
    internal_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    upstream_product_id: Mapped[int] = mapped_column(Integer, index=True)

    supplier_id: Mapped[int] = mapped_column(Integer, index=True)
    supplier_nombre: Mapped[str] = mapped_column(String(200))
    # Upstream merges a parent/subsidiary pair (Mayca Distribuidores/Auto
    # Servicio, Gas Tomza/Envasadora Supergas) into one displayed product.
    # Carried so this app groups the same way rather than offering the
    # operator two mapping-queue rows for what upstream shows as one.
    family_supplier_id: Mapped[int | None] = mapped_column(Integer, index=True)

    description: Mapped[str] = mapped_column(String(300))
    cabys: Mapped[str | None] = mapped_column(String(13))
    unidad_medida: Mapped[str | None] = mapped_column(String(20))  # XML UnidadMedida
    # The *smallest* commercial unit upstream observed for this product --
    # the unit `precio_efectivo_normalizado_crc` is expressed in, and
    # therefore the unit `InsumoFuente.factor_conversion` is defined
    # against. Getting this confused with a given invoice's own unit is
    # PLAN.md §14's "factor inversion" risk.
    unidad_comercial_base: Mapped[str | None] = mapped_column(String(10))

    categoria_slug: Mapped[str | None] = mapped_column(String(60), index=True)
    # Upstream's own product ignore list. Mirrored so an ignored product
    # never reaches the mapping queue here either.
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)

    last_purchase_at: Mapped[dt.date | None] = mapped_column(Date)
    upstream_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[dt.datetime] = _now()

    price_points: Mapped[list[CatalogPricePoint]] = relationship(
        back_populates="product", order_by="CatalogPricePoint.fecha.desc()"
    )


class CatalogPricePoint(Base):
    """One price observation, mirrored from upstream's `product_purchase`.

    Keyed `(clave, numero_linea)` -- upstream's own natural key for an
    invoice line -- so a re-sync is idempotent by construction and a full
    rebuild is DELETE plus re-pull.

    Notas de crédito are already absent: upstream's non-negotiable #5 means
    their lines never became a `ProductPurchase` at all. Do not try to net
    them again here.
    """

    __tablename__ = "catalog_price_point"

    id: Mapped[int] = mapped_column(primary_key=True)
    catalog_product_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_product.id", ondelete="CASCADE"), index=True
    )

    clave: Mapped[str] = mapped_column(String(50), index=True)
    numero_linea: Mapped[int] = mapped_column(Integer)
    fecha: Mapped[dt.date] = mapped_column(Date, index=True)  # FechaEmision

    cantidad: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unidad_comercial: Mapped[str | None] = mapped_column(String(10))
    moneda: Mapped[str | None] = mapped_column(String(10))

    # Net of IVA and of the line's own discount, in `moneda`, in
    # `unidad_comercial` -- upstream's `ProductPurchase.precio_efectivo`.
    precio_efectivo: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    # The same figure in CRC at that date's rate. NULL -- never 0, never the
    # USD figure -- when no rate existed. A NULL here drops the observation
    # from every price series, and the drop is counted and shown.
    precio_efectivo_crc: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    unit_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    # precio_efectivo_crc / unit_ratio -- expressed in the product's
    # `unidad_comercial_base`. **This is the figure costing reads.**
    precio_efectivo_normalizado_crc: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))

    iva_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    descuento_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    product: Mapped[CatalogProduct] = relationship(back_populates="price_points")

    __table_args__ = (
        UniqueConstraint("clave", "numero_linea", name="uq_price_point_clave_linea"),
    )


class CatalogSyncRun(Base):
    """One pull from upstream. Visible in Ajustes: a sync that fails never
    blocks costing (the mirror simply ages), so its failure has to be
    legible somewhere or it is invisible.
    """

    __tablename__ = "catalog_sync_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[dt.datetime] = _now()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|error
    # `modificados_desde` used for this pull; the next one starts here.
    watermark: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    productos_upserted: Mapped[int] = mapped_column(Integer, default=0)
    precios_inserted: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------
# 2. Insumos -- the canonical layer
# --------------------------------------------------------------------------


class Insumo(Base):
    """What the kitchen uses, as opposed to what a supplier sells.

    Carries the three judgments a supplier's product row cannot make:
    `unidad_uso` (what the kitchen measures it in), `merma_pct` (how much
    is lost preparing it), and `metodo_costeo`/`ventana_dias` (which price
    to believe). All three live here rather than on a recipe line because
    all three are properties of the purchased good, not of a dish -- six
    recipes using pechuga must not each carry their own yield (D7).
    """

    __tablename__ = "insumo"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    categoria_slug: Mapped[str | None] = mapped_column(String(60), index=True)

    unidad_uso: Mapped[str] = mapped_column(String(10))  # g | ml | unidad
    # Shipped from day one and unused in Phase 1 (D11): converting ml of
    # aceite to g needs a real number nobody should invent, so until this is
    # populated a cross-dimension conversion is refused, not guessed.
    densidad_g_por_ml: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))

    merma_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal(0))
    # NULL means "use the app default from AppSetting" -- an insumo that has
    # never been given an opinion follows the global one as it changes,
    # rather than being frozen at whatever the default was the day it was
    # created.
    metodo_costeo: Mapped[str | None] = mapped_column(String(30))
    ventana_dias: Mapped[int | None] = mapped_column(Integer)

    notas: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    fuentes: Mapped[list[InsumoFuente]] = relationship(back_populates="insumo")

    __table_args__ = (
        CheckConstraint("merma_pct >= 0 AND merma_pct < 95", name="ck_insumo_merma_rango"),
    )


class InsumoFuente(Base):
    """A confirmed mapping from one supplier product to one insumo -- the
    "human-confirmed product mapping" upstream's D4 specified and never
    built.

    **Only `confirmed` rows feed a price series** (non-negotiable #4). An
    LLM suggestion lands as `suggested` and waits in the mapping queue; a
    confidence score orders that queue and is never a threshold that
    auto-confirms anything.
    """

    __tablename__ = "insumo_fuente"

    id: Mapped[int] = mapped_column(primary_key=True)
    insumo_id: Mapped[int] = mapped_column(ForeignKey("insumo.id"), index=True)
    catalog_product_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_product.id"), index=True
    )

    # How many `Insumo.unidad_uso` one `CatalogProduct.unidad_comercial_base`
    # yields -- 5000 for a 5 kg package costed in grams. NULL while
    # `factor_origen` is 'sin_resolver', and a NULL factor never contributes
    # to a price series: it is a queue item, not a zero (§7.2).
    factor_conversion: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    # Which rule produced that factor -- the app-wide "every automatic
    # decision records which rule fired" convention, inherited from
    # upstream. 'manual' is never overwritten by an automatic rule (D15
    # upstream).
    factor_origen: Mapped[str] = mapped_column(String(30), default="sin_resolver")

    status: Mapped[str] = mapped_column(String(20), default="suggested")
    mapped_by: Mapped[str] = mapped_column(String(10), default="llm")  # llm | human
    mapping_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    # A confirmed source can be switched off without losing the mapping --
    # a supplier you stopped buying from should stop moving the cost without
    # the operator having to re-map them if they come back.
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[dt.datetime] = _now()
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    insumo: Mapped[Insumo] = relationship(back_populates="fuentes")
    producto: Mapped[CatalogProduct] = relationship()

    __table_args__ = (
        # One producto belongs to at most one insumo. Scoped to exclude
        # rejected rows so a rejected suggestion does not block the correct
        # mapping from ever being made.
        Index(
            "uq_fuente_producto_no_rechazado",
            "catalog_product_id",
            unique=True,
            postgresql_where=text("status <> 'rejected'"),
        ),
        CheckConstraint(
            "status IN ('suggested','confirmed','rejected')", name="ck_fuente_status"
        ),
    )


class InsumoMapeoRechazo(Base):
    """A suggestion the operator rejected. Remembered so the same wrong
    grouping is not proposed again on the next sync -- a queue that
    re-offers what you already said no to stops being drained.
    """

    __tablename__ = "insumo_mapeo_rechazo"

    id: Mapped[int] = mapped_column(primary_key=True)
    catalog_product_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_product.id"), index=True
    )
    insumo_id: Mapped[int] = mapped_column(ForeignKey("insumo.id"), index=True)
    created_at: Mapped[dt.datetime] = _now()

    __table_args__ = (
        UniqueConstraint("catalog_product_id", "insumo_id", name="uq_rechazo_producto_insumo"),
    )


# --------------------------------------------------------------------------
# 3. Recetas
# --------------------------------------------------------------------------


class Receta(Base):
    """A preparación (insumo compuesto / subreceta) or a plato -- one table
    with a `tipo`, not two (D8). They have identical structure and identical
    costing; the only difference is that a preparación declares a yield
    (which is what lets other recipes consume it by the gram) and a plato
    declares portions.
    """

    __tablename__ = "receta"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    tipo: Mapped[str] = mapped_column(String(20), index=True)  # preparacion | plato

    descripcion: Mapped[str | None] = mapped_column(Text)
    categoria_menu: Mapped[str | None] = mapped_column(String(60), index=True)

    porciones: Mapped[int | None] = mapped_column(Integer)  # platos
    rendimiento_cantidad: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    rendimiento_unidad: Mapped[str | None] = mapped_column(String(10))

    notas_produccion: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = _now()
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lineas: Mapped[list[RecetaLinea]] = relationship(
        back_populates="receta",
        order_by="RecetaLinea.orden",
        foreign_keys="RecetaLinea.receta_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint("tipo IN ('preparacion','plato')", name="ck_receta_tipo"),
        # The pairing is the whole point of the single table: a preparación
        # without a yield cannot be consumed by another recipe, and a plato
        # with one is a modelling mistake nobody would notice until the
        # per-portion cost came out wrong.
        CheckConstraint(
            "(tipo = 'preparacion' AND rendimiento_cantidad IS NOT NULL "
            " AND rendimiento_unidad IS NOT NULL AND porciones IS NULL) "
            "OR (tipo = 'plato' AND porciones IS NOT NULL "
            " AND rendimiento_cantidad IS NULL)",
            name="ck_receta_rendimiento_o_porciones",
        ),
    )


class RecetaLinea(Base):
    """One line of a receta: a quantity of an insumo, or of another receta.

    The receta-to-receta case is what makes the menu a graph, and it is
    guarded on write by `services/graph.would_create_cycle` -- the edge is
    refused with the offending path named, never written and discovered
    later (non-negotiable #5).
    """

    __tablename__ = "receta_linea"

    id: Mapped[int] = mapped_column(primary_key=True)
    receta_id: Mapped[int] = mapped_column(
        ForeignKey("receta.id", ondelete="CASCADE"), index=True
    )
    orden: Mapped[int] = mapped_column(Integer, default=0)  # drag-and-drop order

    tipo_componente: Mapped[str] = mapped_column(String(10))  # insumo | receta
    insumo_id: Mapped[int | None] = mapped_column(ForeignKey("insumo.id"))
    componente_receta_id: Mapped[int | None] = mapped_column(ForeignKey("receta.id"))

    cantidad: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    unidad: Mapped[str] = mapped_column(String(10))

    # Column shipped now, UI deferred until a real case shows up (D7): a
    # brunoise wastes more than a rough chop, but nobody has asked yet, and
    # a field the operator can set without needing to is a field that gets
    # set wrong.
    merma_pct_override: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    nota: Mapped[str | None] = mapped_column(String(300))

    receta: Mapped[Receta] = relationship(back_populates="lineas", foreign_keys=[receta_id])
    insumo: Mapped[Insumo | None] = relationship()

    __table_args__ = (
        CheckConstraint(
            "tipo_componente IN ('insumo','receta')", name="ck_linea_tipo_componente"
        ),
        CheckConstraint(
            "(tipo_componente = 'insumo' AND insumo_id IS NOT NULL "
            " AND componente_receta_id IS NULL) "
            "OR (tipo_componente = 'receta' AND componente_receta_id IS NOT NULL "
            " AND insumo_id IS NULL)",
            name="ck_linea_un_solo_componente",
        ),
        CheckConstraint("cantidad > 0", name="ck_linea_cantidad_positiva"),
        CheckConstraint(
            "componente_receta_id IS NULL OR componente_receta_id <> receta_id",
            name="ck_linea_no_autoreferencia",
        ),
    )


class RecetaCostoHistorico(Base):
    """One stored cost per receta per day, with the inputs that produced it.

    Stored rather than recomputed (D13): recomputing history from today's
    mappings would rewrite the past every time a mapping, a merma or a
    costing method changed, and a cost chart that changes shape retroactively
    is worse than no chart.
    """

    __tablename__ = "receta_costo_historico"

    id: Mapped[int] = mapped_column(primary_key=True)
    receta_id: Mapped[int] = mapped_column(ForeignKey("receta.id"), index=True)
    fecha: Mapped[dt.date] = mapped_column(Date, index=True)

    costo_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    costo_por_porcion: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    estado: Mapped[str] = mapped_column(String(20))  # completo | incompleto

    metodo_costeo: Mapped[str] = mapped_column(String(30))
    ventana_dias: Mapped[int] = mapped_column(Integer)
    # The mirror's own as-of date, so a cost computed against a stale
    # catalog is legible as such after the fact, not just live.
    mirror_as_of: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[dt.datetime] = _now()

    __table_args__ = (
        UniqueConstraint("receta_id", "fecha", name="uq_costo_historico_receta_fecha"),
    )


class AppSetting(Base):
    """Single-row app configuration (id == 1), seeded on first boot."""

    __tablename__ = "app_setting"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    metodo_costeo_default: Mapped[str] = mapped_column(
        String(30), default="weighted_avg_window"
    )
    ventana_dias_default: Mapped[int] = mapped_column(Integer, default=90)
    upstream_api_url: Mapped[str | None] = mapped_column(String(300))
    moneda: Mapped[str] = mapped_column(String(3), default="CRC")
    llm_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
