"""Periodic root-folder import scanner (issue #24).

Reuses the existing import pipeline (``run_import``) instead of a second
scan engine: on each tick, every root folder configured in the DB (NOT
``settings.root_folders``, which is a legacy/config-only shape — see
``app/models/settings.py::RootFolder``) is imported non-dry-run, exactly
like a manual "Import" run from the UI.

Single-flight, mirroring ``app/conversion/worker.py``: if a tick is still
running when the next one is due, the next tick is skipped rather than
queued or run in parallel.

``app.main``'s lifespan only creates the background task when
``settings.media_management.import_scan_interval_minutes > 0``; the
default of 0 means the scheduler never starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from datetime import UTC, datetime

from app.api.routes_metadata import build_provider_chain
from app.config import get_db_path, load_settings, save_settings
from app.db import migrate
from app.library import list_root_folders
from app.library.importer import run_import

log = logging.getLogger("audiarr.import_scheduler")


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class ImportScheduler:
    """Single-flight runner: imports every DB-configured root folder per tick."""

    def __init__(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def run_once(self) -> bool:
        """Scan all root folders once.

        Returns False without doing anything if a previous scan triggered
        by this instance is still in flight.
        """
        if self._running:
            log.debug("import scheduler: tick skipped, previous scan still running")
            return False
        self._running = True
        try:
            await self._scan_all()
            return True
        finally:
            self._running = False

    async def _scan_all(self) -> None:
        migrate()
        conn = sqlite3.connect(get_db_path())
        conn.row_factory = sqlite3.Row
        try:
            folders = list_root_folders(conn)
        finally:
            conn.close()

        if not folders:
            log.debug("import scheduler: no root folders configured, nothing to scan")

        settings = load_settings()
        chain = build_provider_chain()
        for folder in folders:
            conn = sqlite3.connect(get_db_path())
            conn.row_factory = sqlite3.Row
            try:
                summary = await run_import(
                    conn=conn,
                    chain=chain,
                    root_folder_id=folder["id"],
                    dry_run=False,
                    locale=settings.metadata.audible_locale,
                )
                conn.commit()
                log.info("import scheduler: %s", summary.message)
            except Exception:  # noqa: BLE001 — one bad root folder must not stop the rest
                log.warning(
                    "import scheduler: scan failed for root folder %s",
                    folder["id"],
                    exc_info=True,
                )
            finally:
                conn.close()

        settings = load_settings()
        settings.media_management.last_scheduled_scan_at = _utcnow_iso()
        save_settings(settings)
        log.info("import scheduler: tick complete (%d root folder(s))", len(folders))


async def scheduler_loop(
    scheduler: ImportScheduler,
    interval_minutes: int,
    stop_event: asyncio.Event,
    delay_first: bool = False,
) -> None:
    """Run ``scheduler.run_once()`` every ``interval_minutes`` until stopped.

    A non-positive interval is a no-op: ``app.main``'s lifespan only
    creates this task when the configured interval is > 0, but the guard
    is kept here too so the loop stays safe to call directly (as tests do).

    ``delay_first=True`` waits one full interval before the first tick
    instead of firing immediately at startup. The default (False) keeps
    the existing immediate-first-run behaviour used by the import/metadata
    /wanted-search schedulers; the backup scheduler (app/backup_scheduler.py)
    passes True, since an immediate run would mean surprise I/O at boot and
    a duplicate backup on every container restart.
    """
    if interval_minutes <= 0:
        return
    interval_seconds = interval_minutes * 60
    log.info("import scheduler started (interval=%d minute(s))", interval_minutes)
    if delay_first:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
    while not stop_event.is_set():
        await scheduler.run_once()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
    log.info("import scheduler stopped")
