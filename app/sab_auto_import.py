"""Auto-import completed SABnzbd downloads (issue #25).

Polls the enabled SABnzbd client's history for completed items in the
configured category, derives the completed download folder from SABnzbd's
``storage`` field, and feeds it into the existing import pipeline
(``app.library.importer``) — no second scoring engine. A folder whose name
carries a resolvable ASIN goes through ``import_single_folder`` directly;
everything else falls back to a ``run_import`` of the DB root folder that
contains it, exactly like a manual Import run.

Every processed history item is recorded in ``sab_import_state`` (keyed by
``nzo_id``, or a stable fallback key when SABnzbd doesn't report one) so a
later poll never re-imports the same download. Failures are logged and
recorded with a reason, never raised past a single item — one bad history
entry must not stop the rest of the tick. Source files are never moved,
renamed, or deleted (the import pipeline itself only records what exists).

Single-flight, mirroring app/import_scheduler.py and
app/conversion/worker.py: an overlapping tick is skipped rather than queued
or run in parallel. app.main's lifespan only creates the background task
when both ``settings.media_management.sab_auto_import_enabled`` is true and
an enabled SABnzbd download client is configured.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.api.routes_metadata import build_provider_chain
from app.api.routes_releases import _enabled_sabnzbd
from app.config import get_db_path, load_settings
from app.connections.sabnzbd import SABnzbdClient
from app.db import migrate
from app.library.importer import ImportMatchError, import_single_folder, run_import
from app.library.scanner import scan_folder
from app.providers.chain import ProviderChain

log = logging.getLogger("audiarr.sab_auto_import")

# SABnzbd's history "status" field for a fully completed download. Anything
# else (Downloading, Extracting, Failed, ...) is not a candidate yet.
COMPLETED_STATUS = "completed"

_TERMINAL_STATUSES = {"imported", "failed", "skipped"}


def history_item_key(item: dict[str, Any]) -> str:
    """Stable dedup key for one SABnzbd history item.

    Prefers ``nzo_id`` (stable across SABnzbd restarts); falls back to a
    hash of name/completed/storage/category when absent, since some SAB
    setups (or manually-seeded fixtures) may omit it.
    """
    nzo_id = item.get("nzo_id")
    if nzo_id:
        return str(nzo_id)
    raw = "|".join(
        str(item.get(field, ""))
        for field in ("name", "completed_at", "storage", "category")
    )
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _open_db() -> sqlite3.Connection:
    migrate()
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


class SabAutoImportScheduler:
    """Single-flight runner: imports completed SABnzbd history per tick."""

    def __init__(self) -> None:
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    async def run_once(self) -> bool:
        """Poll SABnzbd history once and import new completed downloads.

        Returns False without doing anything if a previous run triggered by
        this instance is still in flight.
        """
        if self._running:
            log.debug("sab auto-import: tick skipped, previous run still in flight")
            return False
        self._running = True
        try:
            await self._process_all()
            return True
        finally:
            self._running = False

    async def _process_all(self) -> None:
        sab_client_settings = _enabled_sabnzbd()
        if sab_client_settings is None:
            log.debug("sab auto-import: no enabled SABnzbd client configured")
            return

        settings = load_settings()
        category = (
            settings.media_management.sab_auto_import_category
            or sab_client_settings.category
        )

        sab = SABnzbdClient(
            base_url=sab_client_settings.base_url(),
            api_key=sab_client_settings.api_key or None,
        )
        history = await sab.history(limit=100)
        candidates = [
            item
            for item in history
            if str(item.get("status") or "").strip().lower() == COMPLETED_STATUS
            and (not category or (item.get("category") or "") == category)
        ]
        if not candidates:
            log.debug("sab auto-import: no completed history items in category %r", category)
            return

        chain = build_provider_chain()
        locale = settings.metadata.audible_locale
        imported = 0
        for item in candidates:
            key = history_item_key(item)
            conn = _open_db()
            try:
                already = conn.execute(
                    "SELECT status FROM sab_import_state WHERE nzo_key = ?", (key,)
                ).fetchone()
                if already is not None:
                    continue
                await self._process_item(conn, chain, item, key, locale)
                conn.commit()
                imported += 1
            except Exception:  # noqa: BLE001 — one bad item must not stop the tick
                conn.rollback()
                log.warning(
                    "sab auto-import: failed processing %r", item.get("name"), exc_info=True
                )
                self._record_state(conn, key, item, "failed", "unexpected error during import")
                conn.commit()
            finally:
                conn.close()
        log.info(
            "sab auto-import: tick complete (%d completed item(s) seen, %d processed)",
            len(candidates), imported,
        )

    async def _process_item(
        self,
        conn: sqlite3.Connection,
        chain: ProviderChain,
        item: dict[str, Any],
        key: str,
        locale: str,
    ) -> None:
        name = str(item.get("name") or "")
        folder = str(item.get("storage") or "").strip()
        if not folder:
            log.warning("sab auto-import: history item %r has no storage path", name)
            self._record_state(conn, key, item, "skipped", "no storage path reported by SABnzbd")
            return

        folder_path = Path(folder)
        if not folder_path.is_dir():
            log.warning("sab auto-import: completed path %s is not a directory", folder)
            self._record_state(conn, key, item, "skipped", f"path not found: {folder}")
            return

        scanned = scan_folder(folder_path.parent)
        candidate = next((c for c in scanned if c.folder_path == str(folder_path)), None)

        if candidate is not None and candidate.asin_hints:
            asin = candidate.asin_hints[0]
            try:
                result = await import_single_folder(conn, chain, str(folder_path), asin, locale)
            except ImportMatchError as exc:
                log.info(
                    "sab auto-import: ASIN %s for %r did not resolve (%s); "
                    "falling back to root-folder import",
                    asin, name, exc,
                )
            else:
                if result.status in ("matched", "skipped-duplicate"):
                    self._record_state(
                        conn, key, item, "imported",
                        f"matched via ASIN {asin}", result.matched_book_id,
                    )
                else:
                    self._record_state(
                        conn, key, item, "failed", f"import_single_folder: {result.status}"
                    )
                return

        root_folder_id = self._find_root_folder(conn, str(folder_path))
        if root_folder_id is None:
            log.warning(
                "sab auto-import: no configured root folder contains %s", folder_path
            )
            self._record_state(
                conn, key, item, "failed", "no configured root folder contains this path"
            )
            return

        summary = await run_import(conn, chain, root_folder_id, dry_run=False, locale=locale)
        match = next(
            (r for r in summary.results if r.folder_path == str(folder_path)), None
        )
        if match is None:
            self._record_state(
                conn, key, item, "failed", "folder not found by scan after import"
            )
        elif match.status in ("matched", "skipped-duplicate"):
            self._record_state(
                conn, key, item, "imported", match.detail or match.status, match.matched_book_id
            )
        else:
            self._record_state(conn, key, item, "failed", match.detail or match.status)

    @staticmethod
    def _find_root_folder(conn: sqlite3.Connection, folder_path: str) -> int | None:
        """Return the id of the DB root folder that contains ``folder_path``, if any."""
        rows = conn.execute("SELECT id, path FROM root_folders").fetchall()
        for row in rows:
            root = row["path"].rstrip("/")
            if folder_path == root or folder_path.startswith(root + "/"):
                return int(row["id"])
        return None

    @staticmethod
    def _record_state(
        conn: sqlite3.Connection,
        key: str,
        item: dict[str, Any],
        status: str,
        reason: str,
        book_id: int | None = None,
    ) -> None:
        assert status in _TERMINAL_STATUSES
        conn.execute(
            """INSERT INTO sab_import_state
                 (nzo_key, name, folder_path, status, reason, book_id)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (nzo_key) DO UPDATE SET
                 status = excluded.status, reason = excluded.reason,
                 book_id = excluded.book_id, updated_at = datetime('now')""",
            (
                key,
                item.get("name") or "",
                item.get("storage") or "",
                status,
                reason[:500],
                book_id,
            ),
        )
