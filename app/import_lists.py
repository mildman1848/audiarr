"""Generic import-list abstraction (issue #72 groundwork).

Starr apps (Radarr/Sonarr/Lidarr) call external sources that confirm or
suggest wanted items "Import Lists". Audiarr's first and, for now, only
concrete source is Liberatarr (issue #64) -- a purchased-but-missing
Audible library synced in as monitored Wanted books. This module gives
that integration, and any future pull-style source (e.g. a later #66
Hardcover addition), one shared normalized shape instead of each one
inventing its own status/sync plumbing.

This module deliberately wraps the existing Liberatarr connection client
(app/connections/liberatarr.py) rather than replacing it, and the legacy
/api/v1/liberatarr/* routes (app/api/routes_liberatarr.py) keep calling
that client directly for their own fetch -- only the row-processing loop
(``sync_liberatarr_rows``) and status bookkeeping (``record_liberatarr_sync``)
are shared, so existing tests that monkeypatch
``app.api.routes_liberatarr.fetch_library`` keep working unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from sqlite3 import Connection

from app.config import load_settings, save_settings
from app.connections.liberatarr import LiberatarrError, fetch_library, is_not_liberated
from app.library import BookCreate, create_book, get_conn
from app.models.settings import Settings

log = logging.getLogger("audiarr.import_lists")

LIBERATARR_PROVIDER = "audible"
LIBERATARR_SOURCE_ID = "liberatarr"
LIBERATARR_SOURCE_TYPE = "liberatarr"


@dataclass
class ImportListSyncResult:
    """Normalized outcome of syncing one import-list source."""

    created: int = 0
    skipped_existing: int = 0
    skipped_no_id: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportListSource:
    """Starr-style status row for one configured import-list source."""

    id: str
    type: str
    name: str
    enabled: bool
    status: str  # "unknown" | "ok" | "error"
    last_sync_at: str
    last_error: str
    last_created: int
    last_skipped: int


def list_sources(settings: Settings | None = None) -> list[ImportListSource]:
    """List all currently known import-list sources and their persisted status.

    Liberatarr is the only concrete source today; more entries are added
    here as future sources register (see module docstring).
    """
    settings = settings or load_settings()
    return [_liberatarr_source(settings)]


def get_source(source_id: str, settings: Settings | None = None) -> ImportListSource | None:
    for source in list_sources(settings):
        if source.id == source_id:
            return source
    return None


def _liberatarr_source(settings: Settings) -> ImportListSource:
    lib = settings.connections.liberatarr
    return ImportListSource(
        id=LIBERATARR_SOURCE_ID,
        type=LIBERATARR_SOURCE_TYPE,
        name="Liberatarr",
        enabled=lib.enabled,
        status=lib.sync_status,
        last_sync_at=lib.last_sync_at,
        last_error=lib.last_sync_error,
        last_created=lib.last_sync_created,
        last_skipped=lib.last_sync_skipped,
    )


def sync_liberatarr_rows(conn: Connection, rows: list[dict]) -> ImportListSyncResult:
    """Create Wanted books from normalized NotLiberated Liberatarr rows.

    Extracted from the original loop in
    app/api/routes_liberatarr.py::sync_liberatarr so the legacy route and
    the generic /api/v1/import-lists/{id}/sync route share exactly one
    place that decides which rows become books. Idempotent: a row whose
    ASIN is already known under provider "audible" is skipped.
    """
    result = ImportListSyncResult()
    for row in rows:
        asin = row.get("asin", "")
        if not asin:
            result.skipped_no_id += 1
            log.debug("Import-list sync (liberatarr): skipping row with no ASIN (title=%r)", row.get("title"))
            continue
        if not is_not_liberated(row.get("status", "")):
            log.debug("Import-list sync (liberatarr): ASIN %s already liberated, skipping", asin)
            continue

        # Ignore locale in this lookup (mirrors app/library/importer.py's
        # duplicate check): a book already known under any locale for this
        # ASIN must not be recreated just because Liberatarr reports a
        # different/blank locale on a later sync.
        existing = conn.execute(
            "SELECT entity_id FROM provider_ids "
            "WHERE entity_type = 'book' AND provider = ? AND provider_id = ?",
            (LIBERATARR_PROVIDER, asin),
        ).fetchone()
        if existing is not None:
            result.skipped_existing += 1
            log.debug(
                "Import-list sync (liberatarr): ASIN %s already known (book %s), skipping",
                asin,
                existing["entity_id"],
            )
            continue

        try:
            book_id = create_book(
                conn,
                BookCreate(
                    title=row.get("title") or asin,
                    authors=row.get("authors") or [],
                    narrators=row.get("narrators") or [],
                    provider=LIBERATARR_PROVIDER,
                    provider_id=asin,
                    locale=row.get("locale") or "",
                    monitored=True,
                ),
            )
            result.created += 1
            log.info(
                "Import-list sync (liberatarr): created book %s for ASIN %s (%r)",
                book_id,
                asin,
                row.get("title"),
            )
        except Exception as exc:  # noqa: BLE001 -- one bad row must not abort the whole sync
            result.errors.append(f"{asin}: {exc}")
            log.warning("Import-list sync (liberatarr): failed to create book for ASIN %s: %s", asin, exc)

    return result


def record_liberatarr_sync(settings: Settings, result: ImportListSyncResult, error: str = "") -> None:
    """Persist Liberatarr's last-sync status/counts (Starr-style status row).

    Called after a sync from either entry point (the legacy
    /api/v1/liberatarr/sync route or the generic
    /api/v1/import-lists/{id}/sync route) so both keep the same status
    visible under Settings -> Import Lists.
    """
    lib = settings.connections.liberatarr
    lib.last_sync_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    if error:
        lib.sync_status = "error"
        lib.last_sync_error = error
    else:
        lib.sync_status = "error" if result.errors else "ok"
        lib.last_sync_error = result.errors[0] if result.errors else ""
        lib.last_sync_created = result.created
        lib.last_sync_skipped = result.skipped_existing + result.skipped_no_id
    save_settings(settings)


async def sync_source(source_id: str, settings: Settings | None = None) -> ImportListSyncResult:
    """Sync one configured import-list source by id, persisting its status.

    Raises ``ValueError`` for an unknown source id and ``LiberatarrError``
    (propagated) on an upstream fetch failure -- both are turned into HTTP
    responses by the caller (see app/api/routes_import_lists.py).
    """
    settings = settings or load_settings()
    if source_id != LIBERATARR_SOURCE_ID:
        raise ValueError(f"Unknown import-list source: {source_id!r}")

    lib = settings.connections.liberatarr
    try:
        rows = await fetch_library(lib)
    except LiberatarrError as exc:
        record_liberatarr_sync(settings, ImportListSyncResult(), error=str(exc))
        raise

    with get_conn() as conn:
        result = sync_liberatarr_rows(conn, rows)
    record_liberatarr_sync(settings, result)
    return result
