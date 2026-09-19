"""Audiarr FastAPI application entry point.

Run locally with:
    uvicorn app.main:app --reload

In the container this is started by root/usr/local/bin/start-audiarr-api
under s6-overlay, as the unprivileged `abc` user.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import (
    routes_auth,
    routes_calendar,
    routes_connections,
    routes_conversion,
    routes_import,
    routes_library,
    routes_metadata,
    routes_releases,
    routes_settings,
    routes_system,
    routes_wanted,
    routes_webhooks,
)
from app.auth import AuthMiddleware
from app.config import get_db_path, load_settings, save_settings
from app.db import init_db
from app.logging_conf import configure_logging
from app.web import routes as web_routes

configure_logging()
log = logging.getLogger("audiarr")

STATIC_DIR = Path(__file__).parent / "web" / "static"


class NoCacheHtmlMiddleware:
    """Add Cache-Control: no-cache to HTML responses.

    Rendered pages reference static assets with ?v= cache-busters, but the
    HTML document itself carries no version — a browser that heuristic-caches
    it keeps loading pre-deploy asset URLs after an update (seen live on a
    phone). ``no-cache`` still allows storage but forces revalidation with
    the server on every load. Non-HTML responses pass through untouched.
    """

    def __init__(self, app):  # ASGI app
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_cache_control(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"cache-control", b"no-cache"))
            await send(message)

        path = scope.get("path", "")
        # Static assets carry etags + ?v= cache-busters; everything else that
        # is HTML (rendered pages) gets no-cache. API JSON responses tolerate
        # no-cache harmlessly, so we keep the rule simple: skip /static only.
        if path.startswith("/static"):
            await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send_with_cache_control)


async def _run_startup_backfill() -> None:
    """Fire-and-forget: run one metadata backfill batch (see app.metadata.backfill).

    Best-effort by construction (the batch itself never raises for
    individual book failures), but this wrapper also swallows anything
    unexpected -- a broken provider chain or config must never take the
    app down, since this runs detached from the request/response cycle.
    """
    try:
        import app.providers  # noqa: F401 -- ensure built-ins are registered
        from app.api.routes_metadata import build_provider_chain
        from app.metadata.backfill import run_backfill_batch

        chain = build_provider_chain()
        result = await run_backfill_batch(chain)
        log.info(
            "metadata backfill: updated=%d failed=%d remaining=%d",
            result.updated,
            result.failed,
            result.remaining,
        )
    except Exception:  # noqa: BLE001 -- must never crash startup
        log.warning("metadata backfill: startup batch failed", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    configure_logging(settings.logging.level)
    init_db(get_db_path())
    log.info("Audiarr v%s starting up (language=%s)", __version__, settings.ui.language)

    # First-boot API-key bootstrap, mirrors Radarr/Sonarr: API clients need
    # a key even when auth.method == "none". Reuses the same settings-store
    # writer as the settings PUT route (no second persistence path).
    if not settings.auth.api_key:
        settings.auth.api_key = secrets.token_hex(16)
        save_settings(settings)
        log.info("auth: generated api key (see settings)")

    # Conversion worker: only runs when a backend is configured.
    stop_event = asyncio.Event()
    worker_task = None
    if settings.conversion.backend != "disabled":
        from app.conversion.worker import worker_loop

        worker_task = asyncio.create_task(worker_loop(stop_event))
        log.info("conversion worker enabled (backend=%s)", settings.conversion.backend)

    # Metadata backfill: opt-in (see MetadataSettings.backfill_on_start),
    # since prod imports predate the metadata pipeline and this makes
    # outbound provider requests. Runs detached so a slow/unreachable
    # provider never delays startup.
    backfill_task = None
    if settings.metadata.backfill_on_start:
        backfill_task = asyncio.create_task(_run_startup_backfill())

    # Periodic root-folder import scan (issue #24): only runs when a
    # positive interval is configured; 0 (the default) disables it.
    import_scan_stop_event = asyncio.Event()
    import_scan_task = None
    scan_interval_minutes = settings.media_management.import_scan_interval_minutes
    if scan_interval_minutes > 0:
        from app.import_scheduler import ImportScheduler, scheduler_loop

        import_scan_task = asyncio.create_task(
            scheduler_loop(ImportScheduler(), scan_interval_minutes, import_scan_stop_event)
        )
        log.info("import scheduler enabled (interval=%d minute(s))", scan_interval_minutes)

    yield

    if worker_task is not None:
        stop_event.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(worker_task, timeout=5)

    if import_scan_task is not None:
        import_scan_stop_event.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(import_scan_task, timeout=5)

    if backfill_task is not None:
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(backfill_task, timeout=5)


def create_app() -> FastAPI:
    app = FastAPI(title="Audiarr", version=__version__, lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Serve rendered HTML with no-cache so browsers always revalidate after a
    # deploy; static assets stay etag-cached and are cache-busted via ?v=.
    # Pure ASGI like AuthMiddleware — no new dependencies.
    app.add_middleware(NoCacheHtmlMiddleware)

    # Zero-arg callable so this module doesn't need to import app.config
    # eagerly at class-definition time (app.auth imports app.config itself,
    # which does not import app.main/app.auth back, so no cycle here either
    # way — the callable indirection keeps AuthMiddleware decoupled/testable).
    app.add_middleware(AuthMiddleware, get_auth_settings=load_settings)

    app.include_router(routes_system.router)
    app.include_router(routes_settings.router)
    app.include_router(routes_auth.router)
    app.include_router(routes_metadata.router)
    app.include_router(routes_connections.router)
    app.include_router(routes_releases.router)
    app.include_router(routes_library.router)
    app.include_router(routes_wanted.router)
    app.include_router(routes_calendar.router)
    app.include_router(routes_import.router)
    app.include_router(routes_conversion.router)
    app.include_router(routes_webhooks.router)
    app.include_router(web_routes.router)

    return app


app = create_app()
