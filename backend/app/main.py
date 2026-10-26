"""App wiring.

The app's own name is English ("Menu Builder"), the one deliberate
exception to the Spanish-UI rule -- the same exception restaurant-manager
makes and for the same reason: the app, the repo and the Compose project
carry one name rather than three. Not licence for English anywhere else in
the UI.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers import ajustes, busqueda, insumos, mapeo, recetas
from .templating import templates

logging.basicConfig(level=logging.INFO)

# No `lifespan` hook. It existed only to call `db.init_db`, which only ever
# called `create_all` -- and schema is Alembic's job since 2026-09-07
# (`alembic upgrade head` runs before uvicorn, see docker-compose.yml).
# This app has no seed data, so there is nothing left for a startup hook to
# do; `db.py`'s docstring has the full note.
app = FastAPI(title="Menu Builder")
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
app.include_router(ajustes.router)
app.include_router(busqueda.router)
# mapeo before insumos: `/insumos/mapeo` would otherwise be captured by
# `/insumos/{insumo_id}` and 422 on the int conversion, since FastAPI
# validates the matched route rather than falling through to the next one.
app.include_router(mapeo.router)
app.include_router(insumos.router)
# recetas last: its `/recetas/nueva` vs `/recetas/{receta_id}` ordering is
# handled inside the router itself, in declaration order.
app.include_router(recetas.router)

templates.env.globals["company_name"] = settings.company_name


@app.get("/")
def root():
    return RedirectResponse("/recetas")


@app.get("/salud")
def health():
    return {"ok": True}
