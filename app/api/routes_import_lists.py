"""Generic import-list status/sync API (issue #72 groundwork).

Starr-style status endpoints layered over app.import_lists: list configured
sources with their last-known status, and trigger a sync for one by id.
Liberatarr (issue #64) is the only concrete source today; this is
deliberately generic so a future pull-style source (e.g. #66 Hardcover, if
it ever needs one) can register in app.import_lists instead of inventing a
parallel status/sync API. The existing /api/v1/liberatarr/* routes are
untouched -- this is an additional, generic view onto the same sources.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.connections.liberatarr import LiberatarrError
from app.import_lists import ImportListSource, list_sources, sync_source

log = logging.getLogger("audiarr.api.import_lists")

router = APIRouter()


class ImportListSourceOut(BaseModel):
    id: str
    type: str
    name: str
    enabled: bool
    status: str
    last_sync_at: str
    last_error: str
    last_created: int
    last_skipped: int

    @classmethod
    def from_source(cls, source: ImportListSource) -> ImportListSourceOut:
        return cls(
            id=source.id,
            type=source.type,
            name=source.name,
            enabled=source.enabled,
            status=source.status,
            last_sync_at=source.last_sync_at,
            last_error=source.last_error,
            last_created=source.last_created,
            last_skipped=source.last_skipped,
        )


class ImportListSyncResponse(BaseModel):
    created: int
    skipped_existing: int
    skipped_no_id: int
    errors: list[str]


@router.get("/api/v1/import-lists", response_model=list[ImportListSourceOut])
async def get_import_lists() -> list[ImportListSourceOut]:
    """List configured import-list sources with their last-known status."""
    return [ImportListSourceOut.from_source(source) for source in list_sources()]


@router.post("/api/v1/import-lists/{source_id}/sync", response_model=ImportListSyncResponse)
async def sync_import_list(source_id: str) -> ImportListSyncResponse:
    """Sync one import-list source by id and return the created/skipped counts."""
    try:
        result = await sync_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LiberatarrError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    log.info(
        "Import-list sync %s: created=%d skipped_existing=%d skipped_no_id=%d errors=%d",
        source_id,
        result.created,
        result.skipped_existing,
        result.skipped_no_id,
        len(result.errors),
    )
    return ImportListSyncResponse(
        created=result.created,
        skipped_existing=result.skipped_existing,
        skipped_no_id=result.skipped_no_id,
        errors=result.errors,
    )
