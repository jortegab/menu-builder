"""A line typed on a factura física, and a line that can change (D53)

Revision ID: cb3a0f10e839
Revises: b7439e886233
Create Date: 2026-10-26

restaurant-manager's `/api/v3` serves a product's lines typed on a factura
física beside its electronic ones -- each with no clave, keyed on its
factura -- and a served line is a current figure there, which an edit of
its factura or an Ajuste de cantidades restates, moves to another product
or removes.

  * `catalog_price_point.clave` nullable; `origen` (electronica | fisica)
    NOT NULL DEFAULT 'electronica', which every row on file is;
    `factura_fisica_id` nullable; a unique constraint on
    `(factura_fisica_id, numero_linea)` beside the clave's (a NULL never
    collides in either); a CHECK that each row carries exactly its
    origin's key.
  * `catalog_sync_run.precios_actualizados` and `precios_borrados`, NOT
    NULL DEFAULT 0: what a sync restated or removed, shown on Ajustes.
  * **Data step**: every `catalog_product.upstream_updated_at` set NULL, so
    the first sync after this revision walks the whole catalog. The
    watermark is the newest of that column (`catalog_sync.last_watermark`):
    left as it was, the first v3 sync would re-walk only what changed since
    the last v2 one, and a line restated before it -- an Ajuste de
    cantidades the append-only sync never took in -- would stay as it was.
    The column is the mirror's own copy of upstream's, refilled by that
    sync.

Written as a real change on the production database (stamped at
`b7439e886233`) and on a fresh one alike: both have the baseline's table.

**The downgrade refuses while a typed line is in the mirror**, counting
them: the schema before it has no row for a line with no clave.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "cb3a0f10e839"
down_revision: str | None = "b7439e886233"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLAVE = sa.String(length=50)


def filas(conn) -> int:
    """How many typed lines the mirror holds, on any SQLAlchemy connection
    -- what the schema before this revision cannot."""
    return conn.execute(
        text("select count(*) from catalog_price_point where origen = 'fisica'")
    ).scalar()


def upgrade() -> None:
    op.add_column(
        "catalog_price_point",
        sa.Column(
            "origen", sa.String(length=12), nullable=False, server_default=sa.text("'electronica'")
        ),
    )
    op.add_column(
        "catalog_price_point", sa.Column("factura_fisica_id", sa.Integer(), nullable=True)
    )
    op.alter_column("catalog_price_point", "clave", existing_type=_CLAVE, nullable=True)
    op.create_unique_constraint(
        "uq_price_point_fisica_linea", "catalog_price_point", ["factura_fisica_id", "numero_linea"]
    )
    op.create_check_constraint(
        "ck_price_point_origen",
        "catalog_price_point",
        "(origen = 'electronica' AND clave IS NOT NULL AND factura_fisica_id IS NULL)"
        " OR (origen = 'fisica' AND factura_fisica_id IS NOT NULL AND clave IS NULL)",
    )
    for columna in ("precios_actualizados", "precios_borrados"):
        op.add_column(
            "catalog_sync_run",
            sa.Column(columna, sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    op.execute("update catalog_product set upstream_updated_at = null")


def downgrade() -> None:
    tipadas = filas(op.get_bind())
    if tipadas:
        raise RuntimeError(
            f"El espejo tiene {tipadas} líneas de facturas físicas, y el esquema anterior "
            "no tiene dónde guardar una línea sin clave: borralas (o el espejo entero, que "
            "se reconstruye sincronizando) antes de bajar. No se bajó nada."
        )
    op.drop_column("catalog_sync_run", "precios_borrados")
    op.drop_column("catalog_sync_run", "precios_actualizados")
    op.drop_constraint("ck_price_point_origen", "catalog_price_point", type_="check")
    op.drop_constraint("uq_price_point_fisica_linea", "catalog_price_point", type_="unique")
    op.alter_column("catalog_price_point", "clave", existing_type=_CLAVE, nullable=False)
    op.drop_column("catalog_price_point", "factura_fisica_id")
    op.drop_column("catalog_price_point", "origen")
