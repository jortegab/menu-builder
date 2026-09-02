"""Engine, session and bootstrap.

`get_db` also publishes its Session on `request.state.db` for the same
reason upstream does: template code must read the session from there and
never open one of its own.

**No Alembic** (PLAN.md D15). `create_all` creates missing tables and
never alters an existing one, so a column added to `models.py` after a
table exists needs a manual `ALTER TABLE`, recorded in CLAUDE.md when it
happens. This is inherited debt, knowingly taken, not an oversight.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .models import Base

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db(request: Request) -> Iterator[Session]:
    db = SessionLocal()
    request.state.db = db
    try:
        yield db
    finally:
        db.close()
