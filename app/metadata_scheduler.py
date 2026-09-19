"""Periodic metadata-refresh scheduler (issue #26).

Reuses the existing best-effort asin/release_date backfill
(``run_backfill_batch``, see app/metadata/backfill.py) against the
configured provider chain (``build_provider_chain``) -- no second metadata
engine, exactly the same lookup the manual
``POST /api/v1/metadata/backfill`` endpoint and the startup backfill task
(see app.main::_run_startup_backfill) already use.

Single-flight, mirroring app/import_scheduler.py: if a tick is still
running when the next one is due, the next tick is skipped rather than
queued or run in parallel.

``app.main``'s lifespan only creates the background task when
``settings.metadata.refresh_interval_minutes > 0``; the default of 0 means
the scheduler never starts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.api.routes_metadata import build_provider_chain
from app.config import load_settings, save_settings
from app.metadata.backfill import run_backfill_batch

log = logging.getLogger("audiarr.metadata_scheduler")


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class MetadataRefreshScheduler:
    """Single-flight runner: one metadata backfill batch per tick."""

    def __init__(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def run_once(self) -> bool:
        """Run one backfill batch.

        Returns False without doing anything if a previous refresh
        triggered by this instance is still in flight.
        """
        if self._running:
            log.debug("metadata scheduler: tick skipped, previous run still in flight")
            return False
        self._running = True
        try:
            await self._refresh_once()
            return True
        finally:
            self._running = False

    async def _refresh_once(self) -> None:
        settings = load_settings()
        batch_size = settings.metadata.refresh_batch_size
        chain = build_provider_chain()
        log.debug(
            "metadata scheduler: tick starting (providers=%s locale=%s batch_size=%d)",
            chain.config.provider_order,
            chain.config.audible_locale,
            batch_size,
        )
        result = await run_backfill_batch(chain, batch_size)

        settings = load_settings()
        settings.metadata.last_scheduled_refresh_at = _utcnow_iso()
        settings.metadata.last_refresh_updated = result.updated
        settings.metadata.last_refresh_failed = result.failed
        settings.metadata.last_refresh_remaining = result.remaining
        save_settings(settings)
        log.info(
            "metadata scheduler: tick complete (updated=%d failed=%d remaining=%d)",
            result.updated,
            result.failed,
            result.remaining,
        )
