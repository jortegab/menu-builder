"""App wiring.

The app's own name is English ("Menu Builder"), the one deliberate
exception to the Spanish-UI rule -- the same exception restaurant-manager
makes and for the same reason: the app, the repo and the Compose project
carry one name rather than three. Not licence for English anywhere else in
the UI.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routers import ajustes, recetas
from .templating import templates

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Menu Builder", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)
app.include_router(ajustes.router)
app.include_router(recetas.router)

templates.env.globals["company_name"] = settings.company_name


@app.get("/")
def root():
    return RedirectResponse("/recetas")


@app.get("/salud")
def health():
    return {"ok": True}
