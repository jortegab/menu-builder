"""Engine and session.

`get_db` also publishes its Session on `request.state.db` for the same
reason upstream does: template code must read the session from there and
never open one of its own.

**This module no longer builds the schema.** `create_all` and the
`init_db` that called it were retired on 2026-09-07 in favour of Alembic
(`backend/migrations/`), which is PLAN.md D15's known debt paid off.
`docker compose up` runs `alembic upgrade head` before uvicorn -- spelled
out in docker-compose.yml's `command` rather than hidden in an entrypoint
-- so by the time anything here opens a connection the schema is at head.

There is nothing left for a bootstrap hook to do, which is why `init_db`
is gone entirely rather than hollowed out. Unlike upstream, this app has
no seed data to write: the one row that has to exist,
`app_setting`, is created lazily on first read by `mapeo._fila_ajustes`,
so the column defaults are the app defaults and a fresh install and an
old one answer the same thing. Everything else -- the catalog mirror, the
insumos, the recetas -- arrives from a sync or from the operator.

If something here raises `relation "..." does not exist`, the answer is
`alembic upgrade head`, never a re-added `create_all`.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db(request: Request) -> Iterator[Session]:
    db = SessionLocal()
    request.state.db = db
    try:
        yield db
    finally:
        db.close()
