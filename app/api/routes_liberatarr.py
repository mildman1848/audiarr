"""API routes for the Liberatarr integration (issue #64).

Liberatarr exposes a purchased Audible library; this module lets Audiarr
test the connection, proxy the normalized library, and sync
purchased-but-missing titles into Audiarr as monitored Wanted books.
Liberatarr is read-only from Audiarr's side: only GET /health and
GET /api/library are ever called (see app/connections/liberatarr.py).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_settings
from app.connections.liberatarr import LiberatarrError, fetch_library, is_not_liberated, test_connection
from app.library import BookCreate, create_book, get_conn
from app.models.settings import LiberatarrSettings

log = logging.getLogger("audiarr.api.liberatarr")

router = APIRouter()

PROVIDER = "audible"


class LiberatarrTestResponse(BaseModel):
    ok: bool
    message: str


class LiberatarrSyncResponse(BaseModel):
    created: int
    skipped_existing: int
    skipped_no_asin: int
    errors: list[str]


@router.get("/api/v1/liberatarr/test", response_model=LiberatarrTestResponse)
async def test_liberatarr(base_url: str = "") -> LiberatarrTestResponse:
    """Test the Liberatarr connection.

    ``base_url`` may be overridden via query param (e.g. to test an unsaved
    value from the Connections page before saving). The token is
    intentionally NOT accepted as a request parameter here -- unlike the
    SABnzbd/Prowlarr test endpoints, this is a GET route, and a token in a
    query string would end up in request logs. The persisted token is
    always used instead, which also means "fall back to the persisted
    token" always applies.
    """
    stored = load_settings().connections.liberatarr
    resolved = LiberatarrSettings(
        base_url=base_url or stored.base_url, token=stored.token, enabled=stored.enabled
    )
    result = await test_connection(resolved)
    log.info("Liberatarr connection test to %s -> %s", resolved.base_url, result["ok"])
    return LiberatarrTestResponse(**result)


@router.get("/api/v1/liberatarr/library")
async def get_liberatarr_library() -> list[dict]:
    """Proxy the normalized Liberatarr library using the persisted connection."""
    settings = load_settings().connections.liberatarr
    try:
        return await fetch_library(settings)
    except LiberatarrError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/api/v1/liberatarr/sync", response_model=LiberatarrSyncResponse)
async def sync_liberatarr() -> LiberatarrSyncResponse:
    """Sync purchased-but-missing Audible titles into Audiarr as Wanted books.

    For each Liberatarr row with a NotLiberated status and an ASIN: skip if
    the ASIN is already known under provider "audible" (provider_ids
    table); otherwise create a monitored book from the title/authors/
    narrators Liberatarr reports. Metadata enrichment (cover, description,
    release date, etc.) happens later via the existing metadata backfill/
    wanted schedulers, the same path a manually added book goes through --
    no second enrichment pipeline. Idempotent: running this twice creates
    nothing new on the second run.
    """
    settings = load_settings().connections.liberatarr
    try:
        rows = await fetch_library(settings)
    except LiberatarrError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    created = 0
    skipped_existing = 0
    skipped_no_asin = 0
    errors: list[str] = []

    with get_conn() as conn:
        for row in rows:
            asin = row.get("asin", "")
            if not asin:
                skipped_no_asin += 1
                log.debug("Liberatarr sync: skipping row with no ASIN (title=%r)", row.get("title"))
                continue
            if not is_not_liberated(row.get("status", "")):
                log.debug("Liberatarr sync: ASIN %s is already liberated, skipping", asin)
                continue

            # Ignore locale in this lookup (mirrors app/library/importer.py's
            # duplicate check): a book already known under any locale for
            # this ASIN must not be recreated just because Liberatarr
            # reports a different/blank locale on a later sync.
            existing = conn.execute(
                "SELECT entity_id FROM provider_ids "
                "WHERE entity_type = 'book' AND provider = ? AND provider_id = ?",
                (PROVIDER, asin),
            ).fetchone()
            if existing is not None:
                skipped_existing += 1
                log.debug(
                    "Liberatarr sync: ASIN %s already known (book %s), skipping",
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
                        provider=PROVIDER,
                        provider_id=asin,
                        locale=row.get("locale") or "",
                        monitored=True,
                    ),
                )
                created += 1
                log.info(
                    "Liberatarr sync: created book %s for ASIN %s (%r)", book_id, asin, row.get("title")
                )
            except Exception as exc:  # noqa: BLE001 -- one bad row must not abort the whole sync
                errors.append(f"{asin}: {exc}")
                log.warning("Liberatarr sync: failed to create book for ASIN %s: %s", asin, exc)

    log.info(
        "Liberatarr sync complete: created=%d skipped_existing=%d skipped_no_asin=%d errors=%d",
        created,
        skipped_existing,
        skipped_no_asin,
        len(errors),
    )
    return LiberatarrSyncResponse(
        created=created,
        skipped_existing=skipped_existing,
        skipped_no_asin=skipped_no_asin,
        errors=errors,
    )
