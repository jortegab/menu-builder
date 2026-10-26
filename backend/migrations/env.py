"""Alembic's entry point -- the one place the migration runner is wired to
this app's own configuration and models.

Two things here are load-bearing:

**The URL comes from `app.config.settings`, never from alembic.ini.**
`sqlalchemy.url` in that file is deliberately empty. The app reads its
database from `DATABASE_URL` (a .env the operator owns and that is not
committed); a second copy in alembic.ini would be a second source of
truth, and the failure mode is silent -- migrations applied to one
database while the app runs against another, which looks exactly like
"the migration didn't work". On this machine that failure is one typo
away, because `restaurant-manager`'s database is a live Postgres on the
next port over (5433 against this app's 5434).

**`target_metadata` is `models.Base.metadata`**, the same object
`create_all` used to build from, so `alembic revision --autogenerate`
diffs the live database against the models the app actually maps. Note
the import of `app.models` must happen for its side effect of defining
every mapped class -- an unimported model is an invisible table, and
autogenerate would cheerfully emit a `drop_table` for it.

`sys.path` is patched below because this file is run by the `alembic`
console script, not by uvicorn, so nothing has put the `app` package on
the path yet. Inside the container `app` sits at /srv/app next to this
file's own parent (/srv/migrations); on the host it is backend/app.
`parents[1]` is `backend/` (or /srv) in both cases, which is the
directory that contains `app` either way.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def _compare_options() -> dict:
    """Autogenerate settings shared by the offline and online paths.

    `compare_type` is on, and in this schema it is not a nicety.
    Non-negotiable #1 says money is `NUMERIC`, never float, and every cost
    column here carries a precision and scale that means something --
    `Numeric(14, 4)` on a price point, `Numeric(12, 4)` on a recipe line's
    quantity. Without this, autogenerate compares only the *kind* of type,
    so a narrowed scale would pass through unnoticed and start silently
    rounding a per-gram cost.

    `compare_server_default` is off, and that is deliberate rather than an
    oversight. Postgres normalizes a server default it stores
    (`func.now()` comes back as `now()`, a boolean default as `false`
    however it was written), so turning it on produces a steady trickle of
    false diffs on columns nothing has touched -- and a migration file
    full of noise is one nobody reads.
    """
    return {"compare_type": True, "compare_server_default": False}


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade --sql`).

    Useful for reviewing exactly what a migration would do to the live
    database before letting it near real data. The catalog mirror here is
    derived and disposable (non-negotiable #3), but the insumos, the
    mappings a human confirmed, the recetas and the cost history are not:
    nothing rebuilds those.
    """
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_compare_options(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    ).connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            **_compare_options(),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
