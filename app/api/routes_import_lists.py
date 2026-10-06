"""Generic import-list status/sync API (issue #72 groundwork).

Starr-style status endpoints layered over app.import_lists: list configured
sources with their last-known status, and trigger a sync for one by id.
Liberatarr (issue #64) is the only concrete source today; this is
deliberately generic so a future pull-style source (e.g. #66 Hardcover, if
it ever needs one) can register in app.import_lists instead of inventing a
parallel status/sync API. The existing /api/v1/liberatarr/* routes are
untouched -- this is an additional, generic view onto the same sources.

Issue #79 adds reading-list imports (Goodreads public shelf RSS, Goodreads
and StoryGraph CSV exports) below the generic routes: saved-source
management, a read-only preview that matches entries through the metadata
provider chain, and an explicit selected import. See app/reading_lists.py
and app/reading_list_import.py. Errors from that flow use
``detail = {"code": ..., "message": ...}`` with fixed, safe messages (never
the feed URL, a CSV row or an upstream error body).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.api.routes_metadata import ChainDep
from app.connections.liberatarr import LiberatarrError
from app.import_lists import ImportListSource, SelectionRequiredError, list_sources, sync_source
from app.reading_list_import import (
    DEFAULT_PREVIEW_LIMIT,
    MAX_IMPORT_SELECTIONS,
    MAX_PREVIEW_LIMIT,
    ImportSelection,
    PreviewResult,
    ReadingListImportResult,
    SourceConflictError,
    add_goodreads_source,
    delete_goodreads_source,
    get_reading_list_source,
    import_selections,
    list_reading_list_sources,
    preview_entries,
    record_source_import,
    source_feed_ref,
    update_goodreads_source,
)
from app.reading_lists import (
    MAX_CSV_BYTES,
    MAX_FEED_PAGE,
    MAX_PARSED_ROWS,
    InputTooLargeError,
    ReadingListError,
    fetch_goodreads_feed,
    parse_goodreads_rss,
    parse_reading_list_csv,
    validate_goodreads_feed_url,
)

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
    except SelectionRequiredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "selection_required",
                "message": "Reading-list sources import only from a previewed selection.",
            },
        ) from exc
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


# -- reading lists (issue #79) -----------------------------------------------


def get_goodreads_client() -> httpx.AsyncClient | None:
    """Dependency seam for the Goodreads feed HTTP client.

    ``None`` makes app.reading_lists build its own client (no redirects,
    bounded timeout). Tests override this with a ``MockTransport`` client so
    nothing ever reaches the live site.
    """
    return None


FeedClientDep = Annotated[httpx.AsyncClient | None, Depends(get_goodreads_client)]


def _http_error(exc: ReadingListError) -> HTTPException:
    return HTTPException(status_code=exc.status_hint, detail={"code": exc.code, "message": exc.message})


class FeedUrlIn(BaseModel):
    # Deliberately unconstrained: pydantic validation errors echo the input,
    # and a feed URL must never come back in an error body.
    feed_url: str = ""


class FeedValidationOut(BaseModel):
    valid: bool = True
    goodreads_user_id: str
    shelf: str


class ReadingListSourceCreate(FeedUrlIn):
    name: str = Field(default="", max_length=200)
    enabled: bool = True


class ReadingListSourcePatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class ReadingListSourceOut(BaseModel):
    id: str
    type: str
    name: str
    goodreads_user_id: str
    shelf: str
    enabled: bool
    status: str
    last_sync_at: str
    last_error: str
    last_created: int
    last_skipped: int


class PreviewWindow(BaseModel):
    offset: int = Field(default=0, ge=0, le=MAX_PARSED_ROWS)
    limit: int = Field(default=DEFAULT_PREVIEW_LIMIT, ge=1, le=MAX_PREVIEW_LIMIT)
    shelf: str = Field(default="", max_length=64)  # optional shelf filter
    locale: str = Field(default="", max_length=8)  # metadata locale override


class FeedPreviewIn(PreviewWindow):
    page: int = Field(default=1, ge=1, le=MAX_FEED_PAGE)


class AdhocFeedPreviewIn(FeedPreviewIn, FeedUrlIn):
    pass


class CandidateOut(BaseModel):
    provider_name: str
    provider_uid: str
    asin: str
    title: str
    subtitle: str
    authors: list[str]
    narrators: list[str]
    series: str
    series_position: int
    cover_url: str | None
    locale: str
    score: float
    match_reasons: list[str]
    existing_book_id: int | None


class EntryOut(BaseModel):
    entry_key: str
    source: str
    source_book_id: str
    title: str
    search_title: str
    authors: list[str]
    isbn: str
    isbn13: str
    shelves: list[str]
    exclusive_shelf: str


class PreviewEntryOut(BaseModel):
    entry: EntryOut
    status: Literal["matched", "ambiguous", "unmatched", "lookup_failed"]
    candidates: list[CandidateOut]
    existing_book_id: int | None


class ObservationOut(BaseModel):
    kind: Literal["goodreads_rss_page", "csv_upload"]
    page: int | None
    complete: bool


class ReadingListPreviewOut(BaseModel):
    source_type: str
    entries: list[PreviewEntryOut]
    total_entries: int
    offset: int
    limit: int
    next_offset: int | None
    next_page: int | None
    observation: ObservationOut
    skipped_rows: int
    duplicate_rows: int
    notes: list[str]

    @classmethod
    def from_result(cls, result: PreviewResult) -> ReadingListPreviewOut:
        return cls(**asdict(result))


class SelectionIn(BaseModel):
    entry_key: str = Field(max_length=80)
    provider_name: str = Field(default="", max_length=40)
    provider_uid: str = Field(default="", max_length=64)
    locale: str = Field(default="", max_length=8)


class ReadingListImportIn(BaseModel):
    selections: list[SelectionIn] = Field(max_length=MAX_IMPORT_SELECTIONS)


class CsvImportIn(ReadingListImportIn):
    source_type: Literal["goodreads_csv", "storygraph_csv"] = "goodreads_csv"


class ImportItemOut(BaseModel):
    entry_key: str
    status: Literal[
        "created", "skipped_existing", "skipped_unmatched", "skipped_unverifiable", "skipped_invalid", "error"
    ]
    reason: str
    message: str
    book_id: int | None


class ReadingListImportOut(BaseModel):
    created: int
    skipped_existing: int
    skipped: int
    errors: int
    items: list[ImportItemOut]

    @classmethod
    def from_result(cls, result: ReadingListImportResult) -> ReadingListImportOut:
        return cls(**asdict(result))


def _source_out(source) -> ReadingListSourceOut:  # noqa: ANN001
    return ReadingListSourceOut(
        id=source.id,
        type=source.type,
        name=source.name,
        goodreads_user_id=source.goodreads_user_id,
        shelf=source.shelf,
        enabled=source.enabled,
        status=source.sync_status,
        last_sync_at=source.last_sync_at,
        last_error=source.last_sync_error,
        last_created=source.last_sync_created,
        last_skipped=source.last_sync_skipped,
    )


def _enabled_source(source_id: str):  # noqa: ANN202
    source = get_reading_list_source(source_id)
    if not source.enabled:
        raise SourceConflictError("source_disabled", "This reading-list source is disabled.")
    return source


@router.post("/api/v1/import-lists/goodreads/validate-feed", response_model=FeedValidationOut)
async def validate_goodreads_feed(body: FeedUrlIn) -> FeedValidationOut:
    """Validate a Goodreads shelf RSS URL without fetching it or saving anything."""
    try:
        ref = validate_goodreads_feed_url(body.feed_url)
    except ReadingListError as exc:
        raise _http_error(exc) from exc
    return FeedValidationOut(goodreads_user_id=ref.user_id, shelf=ref.shelf)


@router.get("/api/v1/import-lists/goodreads/sources", response_model=list[ReadingListSourceOut])
async def get_goodreads_sources() -> list[ReadingListSourceOut]:
    return [_source_out(s) for s in list_reading_list_sources()]


@router.post("/api/v1/import-lists/goodreads/sources", response_model=ReadingListSourceOut, status_code=201)
async def create_goodreads_source(body: ReadingListSourceCreate) -> ReadingListSourceOut:
    """Save a public Goodreads shelf feed; only its user id and shelf are stored."""
    try:
        return _source_out(add_goodreads_source(body.feed_url, body.name, body.enabled))
    except ReadingListError as exc:
        raise _http_error(exc) from exc


@router.patch("/api/v1/import-lists/goodreads/sources/{source_id}", response_model=ReadingListSourceOut)
async def patch_goodreads_source(source_id: str, body: ReadingListSourcePatch) -> ReadingListSourceOut:
    try:
        return _source_out(update_goodreads_source(source_id, body.name, body.enabled))
    except ReadingListError as exc:
        raise _http_error(exc) from exc


@router.delete("/api/v1/import-lists/goodreads/sources/{source_id}", status_code=204)
async def remove_goodreads_source(source_id: str) -> None:
    """Remove a saved source. Never touches books already created from it."""
    try:
        delete_goodreads_source(source_id)
    except ReadingListError as exc:
        raise _http_error(exc) from exc


async def _preview_feed(
    chain, client, ref, body: FeedPreviewIn  # noqa: ANN001
) -> ReadingListPreviewOut:
    data = await fetch_goodreads_feed(ref, body.page, client=client)
    parsed = parse_goodreads_rss(data)
    result = await preview_entries(
        chain,
        parsed,
        source_type="goodreads_rss",
        offset=body.offset,
        limit=body.limit,
        shelf=body.shelf,
        locale=body.locale,
        page=body.page,
    )
    return ReadingListPreviewOut.from_result(result)


@router.post(
    "/api/v1/import-lists/goodreads/sources/{source_id}/preview", response_model=ReadingListPreviewOut
)
async def preview_goodreads_source(
    source_id: str, body: FeedPreviewIn, chain: ChainDep, client: FeedClientDep
) -> ReadingListPreviewOut:
    """Fetch one page of a saved shelf feed and match it via the provider chain.

    Read-only: nothing is created or persisted. The feed is a partial,
    page-limited observation (``notes`` explains); books absent from it are
    never removed or unmonitored.
    """
    try:
        source = _enabled_source(source_id)
        return await _preview_feed(chain, client, source_feed_ref(source), body)
    except ReadingListError as exc:
        raise _http_error(exc) from exc


@router.post("/api/v1/import-lists/goodreads/preview", response_model=ReadingListPreviewOut)
async def preview_goodreads_feed_url(
    body: AdhocFeedPreviewIn, chain: ChainDep, client: FeedClientDep
) -> ReadingListPreviewOut:
    """Validate and preview a feed URL without saving it as a source."""
    try:
        ref = validate_goodreads_feed_url(body.feed_url)
        return await _preview_feed(chain, client, ref, body)
    except ReadingListError as exc:
        raise _http_error(exc) from exc


def _import_response(result: ReadingListImportResult) -> ReadingListImportOut:
    return ReadingListImportOut.from_result(result)


@router.post(
    "/api/v1/import-lists/goodreads/sources/{source_id}/import", response_model=ReadingListImportOut
)
async def import_goodreads_source(
    source_id: str, body: ReadingListImportIn, chain: ChainDep
) -> ReadingListImportOut:
    """Create monitored books for explicitly selected, provider-verified matches."""
    try:
        _enabled_source(source_id)
        result = await import_selections(
            chain, [ImportSelection(**s.model_dump()) for s in body.selections], source_type="goodreads_rss"
        )
    except ReadingListError as exc:
        raise _http_error(exc) from exc
    record_source_import(source_id, result)
    return _import_response(result)


async def _read_upload(request: Request, file: UploadFile) -> bytes:
    declared = request.headers.get("content-length", "")
    # Allow a little multipart overhead on top of the file cap.
    if declared.isdigit() and int(declared) > MAX_CSV_BYTES + 64 * 1024:
        raise _http_error(InputTooLargeError("csv_too_large", "The CSV file is too large."))
    data = await file.read(MAX_CSV_BYTES + 1)
    if len(data) > MAX_CSV_BYTES:
        raise _http_error(InputTooLargeError("csv_too_large", "The CSV file is too large."))
    return data


@router.post("/api/v1/import-lists/csv/preview", response_model=ReadingListPreviewOut)
async def preview_csv_upload(
    request: Request,
    chain: ChainDep,
    file: Annotated[UploadFile, File()],
    format: Annotated[Literal["auto", "goodreads", "storygraph"], Form()] = "auto",  # noqa: A002
    shelf: Annotated[str, Form(max_length=64)] = "",
    locale: Annotated[str, Form(max_length=8)] = "",
    offset: Annotated[int, Form(ge=0, le=MAX_PARSED_ROWS)] = 0,
    limit: Annotated[int, Form(ge=1, le=MAX_PREVIEW_LIMIT)] = DEFAULT_PREVIEW_LIMIT,
) -> ReadingListPreviewOut:
    """Preview an uploaded Goodreads or StoryGraph CSV (multipart field ``file``).

    The upload is parsed in memory and discarded -- never stored. To page
    through a large export, re-upload with a different ``offset``.
    """
    try:
        data = await _read_upload(request, file)
        parsed = parse_reading_list_csv(data, format)
        source_type = parsed.format
        result = await preview_entries(
            chain, parsed, source_type=source_type, offset=offset, limit=limit, shelf=shelf, locale=locale
        )
    except ReadingListError as exc:
        raise _http_error(exc) from exc
    return ReadingListPreviewOut.from_result(result)


@router.post("/api/v1/import-lists/csv/import", response_model=ReadingListImportOut)
async def import_csv_selection(body: CsvImportIn, chain: ChainDep) -> ReadingListImportOut:
    """Import selections made from a CSV preview.

    The CSV itself is not needed again: every selection is re-verified
    against the metadata provider, which is the only source of book data.
    """
    try:
        result = await import_selections(
            chain, [ImportSelection(**s.model_dump()) for s in body.selections], source_type=body.source_type
        )
    except ReadingListError as exc:
        raise _http_error(exc) from exc
    return _import_response(result)
