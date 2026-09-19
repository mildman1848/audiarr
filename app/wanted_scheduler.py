"""Periodic wanted-search scheduler (issue #26).

Reuses the existing cutoff-upgrade candidate selection and Prowlarr search /
evaluate_quality_for_profile / SABnzbd grab path from app.api.routes_wanted
(the same logic backing GET /api/v1/wanted/cutoff and
POST /api/v1/wanted/cutoff/{book_id}/search, issue #21) -- no second
scoring engine.

Single-flight, mirroring app/import_scheduler.py: if a tick is still
running when the next one is due, the next tick is skipped rather than
queued or run in parallel.

Duplicate-grab guard: a small persistent table (``wanted_search_state``,
see app/db_migrations/010_wanted_search_state.sql) records the outcome of
the last search per book_id. A book already marked "grabbed" is skipped on
later ticks -- its existing file stays below cutoff until the grabbed
release is imported, so without this guard every tick would re-grab it.
Once the upgrade lands, the book drops out of the cutoff-candidate list and
its state row is pruned automatically on the next tick. Books with no
fitting release carry no such guard: retrying every tick as indexers gain
new content is exactly the point of a periodic wanted search.

``app.main``'s lifespan only creates the background task when a positive
interval is configured AND an enabled Prowlarr indexer + SABnzbd download
client are both present.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from app.api.routes_wanted import (
    _enabled_prowlarr,
    _enabled_sabnzbd,
    find_fitting_release,
    grab_cutoff_release,
    list_cutoff_candidates,
)
from app.config import get_db_path, load_settings, save_settings
from app.db import migrate

log = logging.getLogger("audiarr.wanted_scheduler")


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _open_db() -> sqlite3.Connection:
    migrate()
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


class WantedSearchScheduler:
    """Single-flight runner: cutoff-upgrade search+grab per candidate per tick."""

    def __init__(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def run_once(self) -> bool:
        """Search+grab for every cutoff candidate once.

        Returns False without doing anything if a previous run triggered
        by this instance is still in flight.
        """
        if self._running:
            log.debug("wanted scheduler: tick skipped, previous run still in flight")
            return False
        self._running = True
        try:
            await self._process_all()
            return True
        finally:
            self._running = False

    async def _process_all(self) -> None:
        indexer = _enabled_prowlarr()
        if indexer is None:
            log.debug("wanted scheduler: no enabled Prowlarr indexer configured")
            return
        sab = _enabled_sabnzbd()

        settings = load_settings()
        conn = _open_db()
        try:
            candidates = list_cutoff_candidates(conn, settings)
            self._prune_stale_state(conn, {c.id for c in candidates})

            grabbed = no_release = skipped = errors = 0
            for candidate in candidates:
                state = conn.execute(
                    "SELECT status FROM wanted_search_state WHERE book_id = ?", (candidate.id,)
                ).fetchone()
                if state is not None and state["status"] == "grabbed":
                    log.debug(
                        "wanted scheduler: book %d (%r) already grabbed, awaiting import",
                        candidate.id,
                        candidate.title,
                    )
                    skipped += 1
                    continue

                try:
                    release = await find_fitting_release(
                        candidate.id,
                        candidate.title,
                        candidate.authors,
                        candidate.profile,
                        indexer,
                        settings.quality_definitions,
                    )
                except Exception:  # noqa: BLE001 -- one bad book must not stop the tick
                    log.warning(
                        "wanted scheduler: search failed for book %d (%r)",
                        candidate.id,
                        candidate.title,
                        exc_info=True,
                    )
                    errors += 1
                    continue

                if release is None:
                    self._record_state(conn, candidate.id, "no_release", None)
                    conn.commit()
                    no_release += 1
                    continue

                if sab is None:
                    log.debug(
                        "wanted scheduler: fitting release found for book %d (%r) but no "
                        "SABnzbd client configured",
                        candidate.id,
                        candidate.title,
                    )
                    self._record_state(conn, candidate.id, "no_release", None)
                    conn.commit()
                    no_release += 1
                    continue

                try:
                    outcome = await grab_cutoff_release(candidate.id, release, indexer, sab)
                except Exception:  # noqa: BLE001 -- one bad book must not stop the tick
                    log.warning(
                        "wanted scheduler: grab failed for book %d (%r)",
                        candidate.id,
                        candidate.title,
                        exc_info=True,
                    )
                    errors += 1
                    continue

                if outcome.ok:
                    self._record_state(conn, candidate.id, "grabbed", outcome.nzo_id)
                    grabbed += 1
                    log.info(
                        "wanted scheduler: grabbed upgrade for book %d (%r) -> nzo_id=%s",
                        candidate.id,
                        candidate.title,
                        outcome.nzo_id,
                    )
                else:
                    self._record_state(conn, candidate.id, "no_release", None)
                    no_release += 1
                    log.debug(
                        "wanted scheduler: grab did not succeed for book %d (%r): %s",
                        candidate.id,
                        candidate.title,
                        outcome.message,
                    )
                conn.commit()
        finally:
            conn.close()

        settings = load_settings()
        settings.wanted.last_scheduled_search_at = _utcnow_iso()
        settings.wanted.last_search_grabbed = grabbed
        settings.wanted.last_search_no_release = no_release
        settings.wanted.last_search_skipped = skipped
        save_settings(settings)
        log.info(
            "wanted scheduler: tick complete (%d candidate(s), %d grabbed, %d no_release, "
            "%d skipped, %d error(s))",
            len(candidates),
            grabbed,
            no_release,
            skipped,
            errors,
        )

    @staticmethod
    def _prune_stale_state(conn: sqlite3.Connection, candidate_ids: set[int]) -> None:
        """Drop state rows for books that are no longer cutoff candidates.

        Covers both a successful upgrade (the imported file now meets
        cutoff) and any other reason the book dropped off the list
        (unmonitored, deleted, upgrade_allowed turned off, ...).
        """
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            conn.execute(
                f"DELETE FROM wanted_search_state WHERE book_id NOT IN ({placeholders})",
                tuple(candidate_ids),
            )
        else:
            conn.execute("DELETE FROM wanted_search_state")
        conn.commit()

    @staticmethod
    def _record_state(
        conn: sqlite3.Connection, book_id: int, status: str, nzo_id: str | None
    ) -> None:
        assert status in ("grabbed", "no_release")
        conn.execute(
            """INSERT INTO wanted_search_state (book_id, status, nzo_id)
                 VALUES (?, ?, ?)
               ON CONFLICT (book_id) DO UPDATE SET
                 status = excluded.status, nzo_id = excluded.nzo_id, updated_at = datetime('now')""",
            (book_id, status, nzo_id),
        )
