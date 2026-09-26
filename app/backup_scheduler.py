"""Periodic config-backup scheduler (issue #32, phase 5).

Single-flight, mirroring app/wanted_scheduler.py: if a tick is still
running when the next one is due, the next tick is skipped rather than
queued or run in parallel. Reuses app.backup_service.create_backup instead
of a second backup engine.

app.main's lifespan only creates the background task when
settings.backup.interval_hours > 0, converting hours to the minutes unit
app.import_scheduler.scheduler_loop expects, and passes delay_first=True so
the first backup runs after one full interval rather than immediately at
boot (see scheduler_loop's docstring for why).
"""

from __future__ import annotations

import asyncio
import logging

from app.backup_service import BackupError, create_backup

log = logging.getLogger("audiarr.backup_scheduler")


class BackupScheduler:
    """Single-flight runner: one create_backup(reason="scheduled") per tick."""

    def __init__(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def run_once(self) -> bool:
        """Create one scheduled backup.

        Returns False without doing anything if a previous run triggered
        by this instance is still in flight.
        """
        if self._running:
            log.debug("backup scheduler: tick skipped, previous run still in flight")
            return False
        self._running = True
        try:
            await self._backup_once()
            return True
        finally:
            self._running = False

    async def _backup_once(self) -> None:
        try:
            result = await asyncio.to_thread(create_backup, "scheduled")
        except BackupError:
            log.warning("backup scheduler: scheduled backup failed", exc_info=True)
            return
        log.info(
            "backup scheduler: created %s (%d bytes)", result["path"], result["size_bytes"]
        )
