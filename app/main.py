"""Audiarr FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --reload

In the container this is started by root/usr/local/bin/start-audiarr-api
under s6-overlay, as the unprivileged `abc` user.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import routes_connections, routes_metadata, routes_settings, routes_system
from app.config import get_db_path, load_settings
from app.db import init_db
from app.logging_conf import configure_logging
from app.web import routes as web_routes

configure_logging()
log = logging.getLogger("audiarr")

STATIC_DIR = Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    configure_logging(settings.logging.level)
    init_db(get_db_path())
    log.info("Audiarr v%s starting up (language=%s)", __version__, settings.ui.language)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Audiarr", version=__version__, lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(routes_system.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_metadata.router)
    app.include_router(routes_connections.router)
    app.include_router(web_routes.router)

    return app


app = create_app()
