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

qBittorrent (issue #81) rides the same tick: when an enabled qBittorrent
client is configured, completed torrents (``filter=completed``, narrowed to
the client's fixed category *and* Audiarr tag) are imported through the same
``_import_folder`` helper, so the importer, quality pipeline and import
strategy are shared. State is keyed by (client, info-hash) in
``download_client_import_state`` so hashes never collide with SAB ids. The
remote path mapping is applied to ``content_path`` before any filesystem
probe; a single-file torrent (``content_path`` is a file in the shared save
path) is recorded as skipped rather than importing a shared directory.
Torrents and their data are never deleted, stopped, or paused. The poller
only imports what a user manually sent (tag marker); it never grabs.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.api.routes_metadata import build_provider_chain
from app.api.routes_releases import _enabled_qbittorrent, _enabled_sabnzbd
from app.config import get_db_path, load_settings
from app.connections.qbittorrent import QBittorrentClient
from app.connections.sabnzbd import SABnzbdClient
from app.db import migrate
from app.library.importer import ImportMatchError, import_single_folder, run_import
from app.library.scanner import scan_folder
from app.models.settings import DownloadClient, RemotePathMapping
from app.providers.chain import ProviderChain
from app.remote_path_mapping import resolve_remote_path

log = logging.getLogger("audiarr.sab_auto_import")

# SABnzbd's history "status" field for a fully completed download. Anything
# else (Downloading, Extracting, Failed, ...) is not a candidate yet.
COMPLETED_STATUS = "completed"

_TERMINAL_STATUSES = {"imported", "failed", "skipped"}

QBITTORRENT_CLIENT_KEY = "qbittorrent"

# A torrent whose path is not available *yet* (volume not mounted, mapping not
# configured) or whose processing hit a transient error is stored as
# status="failed" with this reason prefix. The poller treats such rows as
# pending and retries them on later ticks; the (client, item_id) upsert keeps
# it a single row. Imported rows and permanent decisions stay terminal.
RETRY_PREFIX = "retry: "


def _is_retryable_state(row: sqlite3.Row | None) -> bool:
    return row is not None and row["status"] == "failed" and row["reason"].startswith(RETRY_PREFIX)


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


def _find_root_folder(conn: sqlite3.Connection, folder_path: str) -> int | None:
    """Return the id of the DB root folder that contains ``folder_path``, if any.

    Both sides are resolved with ``realpath`` so a root folder configured as
    (or beneath) a symlink still matches the resolved download path, while a
    path that resolves outside every root never does.
    """
    resolved = os.path.realpath(folder_path)
    rows = conn.execute("SELECT id, path FROM root_folders").fetchall()
    for row in rows:
        root = os.path.realpath(row["path"]).rstrip("/")
        if resolved == root or resolved.startswith(root + "/"):
            return int(row["id"])
    return None


def _find_root_folder_strict(conn: sqlite3.Connection, folder_path: str) -> str | None:
    """Return the resolved path if it lies strictly beneath a configured root folder.

    Both sides are fully resolved (``..`` and symlinks), so an escape via
    either is rejected. Only ``realpath``/lstat is used: no scan, no
    is_dir/is_file probe, and nothing is read from the target.
    """
    resolved = os.path.realpath(folder_path)
    for row in conn.execute("SELECT path FROM root_folders").fetchall():
        root = os.path.realpath(row["path"])
        if resolved != root and resolved.startswith(root.rstrip("/") + "/"):
            return resolved
    return None


async def _import_folder(
    conn: sqlite3.Connection,
    chain: ProviderChain,
    folder_path: Path,
    name: str,
    locale: str,
) -> tuple[str, str, int | None]:
    """Run one completed download folder through the import pipeline.

    Shared by the SABnzbd and qBittorrent pollers. ``folder_path`` must
    already be an existing, Audiarr-local directory. Returns
    ``(status, reason, book_id)`` with status ``imported`` or ``failed``.
    """
    real_folder = os.path.realpath(folder_path)
    scanned = scan_folder(folder_path.parent)
    candidate = next(
        (c for c in scanned if os.path.realpath(c.folder_path) == real_folder), None
    )

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
                return "imported", f"matched via ASIN {asin}", result.matched_book_id
            return "failed", f"import_single_folder: {result.status}", None

    root_folder_id = _find_root_folder(conn, str(folder_path))
    if root_folder_id is None:
        log.warning("sab auto-import: no configured root folder contains %s", folder_path)
        return "failed", "no configured root folder contains this path", None

    summary = await run_import(conn, chain, root_folder_id, dry_run=False, locale=locale)
    # The scanner reports paths under the root as configured (possibly a
    # symlink or non-canonical), so compare canonical paths on both sides.
    match = next(
        (r for r in summary.results if os.path.realpath(r.folder_path) == real_folder),
        None,
    )
    if match is None:
        return "failed", "folder not found by scan after import", None
    if match.status in ("matched", "skipped-duplicate"):
        return "imported", match.detail or match.status, match.matched_book_id
    return "failed", match.detail or match.status, None


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
        await self._process_sab()
        await self._process_qbittorrent()

    async def _process_sab(self) -> None:
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
                await self._process_item(
                    conn, chain, item, key, locale, settings.remote_path_mappings
                )
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
        remote_path_mappings: list[RemotePathMapping],
    ) -> None:
        name = str(item.get("name") or "")
        folder = str(item.get("storage") or "").strip()
        if not folder:
            log.warning("sab auto-import: history item %r has no storage path", name)
            self._record_state(conn, key, item, "skipped", "no storage path reported by SABnzbd")
            return

        mapped_folder = resolve_remote_path(folder, remote_path_mappings)
        if mapped_folder != folder:
            log.debug("sab auto-import: mapped remote path %r -> %r", folder, mapped_folder)
            # Mutate in place: every downstream use of this item (including
            # _record_state below) reads item["storage"], so this is the
            # single point where the rest of the pipeline switches to the
            # Audiarr-local path.
            item["storage"] = mapped_folder
            folder = mapped_folder

        folder_path = Path(folder)
        if not folder_path.is_dir():
            log.warning("sab auto-import: completed path %s is not a directory", folder)
            self._record_state(conn, key, item, "skipped", f"path not found: {folder}")
            return

        status, reason, book_id = await _import_folder(conn, chain, folder_path, name, locale)
        self._record_state(conn, key, item, status, reason, book_id)

    async def _process_qbittorrent(self) -> None:
        """Import completed qBittorrent torrents; contained, never raises."""
        qb_settings = _enabled_qbittorrent()
        if qb_settings is None:
            log.debug("torrent auto-import: no enabled qBittorrent client configured")
            return
        try:
            await self._import_qbittorrent(qb_settings)
        except Exception:  # noqa: BLE001 — a qBittorrent problem must not break the tick
            log.warning("torrent auto-import: qBittorrent poll failed", exc_info=True)

    async def _import_qbittorrent(self, qb_settings: DownloadClient) -> None:
        settings = load_settings()
        client = QBittorrentClient(
            base_url=qb_settings.base_url(),
            api_key=qb_settings.api_key or None,
            username=qb_settings.username or None,
            password=qb_settings.password or None,
        )
        category = qb_settings.category.strip()
        tag = qb_settings.tag.strip()
        if not tag or not category:
            # Fail closed: never broaden to all/category-only completed torrents.
            log.warning(
                "torrent auto-import: qBittorrent category and tag must both be set; "
                "skipping poll"
            )
            return
        try:
            completed = await client.list_completed(
                category=category, tag=tag
            )
        except Exception as exc:  # noqa: BLE001 — QBittorrentError, or any injected fake
            # Message only (QBittorrentError text is secret/URL-free); no repr/traceback.
            log.warning("torrent auto-import: could not list completed torrents (%s)", exc)
            return

        chain = None
        locale = settings.metadata.audible_locale
        processed = 0
        for item in completed:
            item_id = str(item.get("hash") or "").lower()
            if not item_id:
                continue
            conn = _open_db()
            try:
                seen = conn.execute(
                    "SELECT status, reason FROM download_client_import_state "
                    "WHERE client = ? AND item_id = ?",
                    (QBITTORRENT_CLIENT_KEY, item_id),
                ).fetchone()
                if seen is not None and not _is_retryable_state(seen):
                    continue
                if chain is None:
                    chain = build_provider_chain()
                await self._process_qb_item(
                    conn, chain, item, item_id, locale, settings.remote_path_mappings
                )
                conn.commit()
                processed += 1
            except Exception as exc:  # noqa: BLE001 — one bad item must not stop the tick
                conn.rollback()
                log.warning(
                    "torrent auto-import: failed processing %r (%s); will retry",
                    item.get("name"), type(exc).__name__, exc_info=log.isEnabledFor(logging.DEBUG),
                )
                self._record_retry(conn, item_id, item, "", "unexpected error during import")
                conn.commit()
            finally:
                conn.close()
        log.info(
            "torrent auto-import: tick complete (%d completed torrent(s) seen, %d processed)",
            len(completed), processed,
        )

    async def _process_qb_item(
        self,
        conn: sqlite3.Connection,
        chain: ProviderChain,
        item: dict[str, Any],
        item_id: str,
        locale: str,
        remote_path_mappings: list[RemotePathMapping],
    ) -> None:
        name = str(item.get("name") or "")
        # content_path, never save_path: save_path is the shared download root.
        reported = str(item.get("content_path") or "").strip()
        if not reported:
            log.debug("torrent auto-import: torrent %r has no content path yet", name)
            self._record_retry(conn, item_id, item, "", "no content path reported by qBittorrent")
            return

        # Resolve the remote path BEFORE any filesystem probe.
        folder = resolve_remote_path(reported, remote_path_mappings)
        if folder != reported:
            log.debug("torrent auto-import: mapped remote path %r -> %r", reported, folder)
        folder_path = Path(folder)
        if not folder_path.is_absolute() or ".." in folder_path.parts:
            self._record_client_state(
                conn, item_id, item, folder, "skipped", "reported path is not a safe absolute path"
            )
            return
        # Containment BEFORE any is_file/is_dir/scan/importer call (qB supplies
        # the path, so it is untrusted until proven beneath a root folder).
        contained = _find_root_folder_strict(conn, folder)
        if contained is None:
            log.debug("torrent auto-import: %r is outside every configured root folder", name)
            # Retryable: a root folder or remote path mapping may be added later.
            self._record_retry(
                conn, item_id, item, folder, "path is not beneath any configured root folder"
            )
            return
        folder_path = Path(contained)
        if folder_path.is_file():
            # Single-file torrent: content_path is the file itself, sitting
            # directly in the shared save path. Importing its parent would
            # treat every sibling download as one book, so skip deliberately.
            log.info("torrent auto-import: %r is a single-file torrent; skipping", name)
            self._record_client_state(
                conn, item_id, item, folder, "skipped",
                "single-file torrent: content is a file in the shared save path; "
                "use a torrent content layout that creates a subfolder",
            )
            return
        if not folder_path.is_dir():
            log.debug("torrent auto-import: completed path %s is not a directory yet", folder)
            # Retryable: the volume may simply not be mounted/synced yet.
            self._record_retry(conn, item_id, item, folder, f"path not found: {folder}")
            return

        status, reason, book_id = await _import_folder(conn, chain, folder_path, name, locale)
        self._record_client_state(conn, item_id, item, folder, status, reason, book_id)

    @classmethod
    def _record_retry(
        cls, conn: sqlite3.Connection, item_id: str, item: dict[str, Any], folder: str, reason: str
    ) -> None:
        cls._record_client_state(conn, item_id, item, folder, "failed", RETRY_PREFIX + reason)

    @staticmethod
    def _record_client_state(
        conn: sqlite3.Connection,
        item_id: str,
        item: dict[str, Any],
        folder: str,
        status: str,
        reason: str,
        book_id: int | None = None,
    ) -> None:
        assert status in _TERMINAL_STATUSES
        conn.execute(
            """INSERT INTO download_client_import_state
                 (client, item_id, name, folder_path, status, reason, book_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (client, item_id) DO UPDATE SET
                 folder_path = excluded.folder_path,
                 status = excluded.status, reason = excluded.reason,
                 book_id = excluded.book_id, updated_at = datetime('now')""",
            (
                QBITTORRENT_CLIENT_KEY,
                item_id,
                item.get("name") or "",
                folder,
                status,
                reason[:500],
                book_id,
            ),
        )

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
