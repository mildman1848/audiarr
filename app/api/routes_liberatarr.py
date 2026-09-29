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
from app.connections.liberatarr import LiberatarrError, fetch_library, test_connection
from app.import_lists import ImportListSyncResult, record_liberatarr_sync, sync_liberatarr_rows
from app.library import get_conn
from app.models.settings import LiberatarrSettings

log = logging.getLogger("audiarr.api.liberatarr")

router = APIRouter()


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
    settings_doc = load_settings()
    settings = settings_doc.connections.liberatarr
    try:
        rows = await fetch_library(settings)
    except LiberatarrError as exc:
        record_liberatarr_sync(settings_doc, ImportListSyncResult(), error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Row-processing loop lives in app.import_lists so the legacy route
    # here and the generic /api/v1/import-lists/{id}/sync route share
    # exactly one place that decides which rows become books (issue #72).
    with get_conn() as conn:
        result = sync_liberatarr_rows(conn, rows)
    record_liberatarr_sync(settings_doc, result)

    log.info(
        "Liberatarr sync complete: created=%d skipped_existing=%d skipped_no_asin=%d errors=%d",
        result.created,
        result.skipped_existing,
        result.skipped_no_id,
        len(result.errors),
    )
    return LiberatarrSyncResponse(
        created=result.created,
        skipped_existing=result.skipped_existing,
        skipped_no_asin=result.skipped_no_id,
        errors=result.errors,
    )
