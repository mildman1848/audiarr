"""Library API: root folders and book entries."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.routes_metadata import build_provider_chain
from app.config import load_settings
from app.connect import dispatch_event
from app.db import get_conn, migrate
from app.library import (
    BookCreate,
    RootFolderCreate,
    create_book,
    create_root_folder,
    delete_book,
    delete_root_folder,
    find_book_by_provider_id,
    get_book,
    get_provider_ids,
    get_root_folder,
    list_books,
    list_root_folders,
    set_book_root_folder,
    update_book,
    update_root_folder_strategy,
)
from app.library.folder_health import FolderHealth, probe_root_folder
from app.library.import_strategy import DEFAULT_STRATEGY
from app.library.organizer import apply_preview, build_preview

ImportStrategyLiteral = Literal["move", "copy", "hardlink"]

log = logging.getLogger("audiarr.api.library")

router = APIRouter()


# -- models -------------------------------------------------------------------


class TagRef(BaseModel):
    """Tag as attached to a book or root folder (see routes_tags.py for CRUD)."""

    id: int
    label: str
    color: str


class RootFolderIn(BaseModel):
    path: str
    label: str = ""
    import_strategy: ImportStrategyLiteral = DEFAULT_STRATEGY


class RootFolderTagsIn(BaseModel):
    tags: list[str] = Field(default_factory=list)


class RootFolderStrategyIn(BaseModel):
    import_strategy: ImportStrategyLiteral


class RootFolderOut(BaseModel):
    id: int
    path: str
    label: str
    import_strategy: str
    created_at: str
    updated_at: str
    tags: list[TagRef]
    free_bytes: int | None = None
    total_bytes: int | None = None
    writable: bool | None = None
    exists: bool | None = None


class BookIn(BaseModel):
    title: str
    subtitle: str = ""
    description: str = ""
    release_date: str | None = None
    language: str = ""
    publisher: str = ""
    duration_seconds: int = 0
    cover_url: str | None = None
    authors: list[str] = Field(default_factory=list)
    narrators: list[str] = Field(default_factory=list)
    series: str = ""
    series_position: float | None = None
    provider: str = ""
    provider_id: str = ""
    locale: str = ""
    monitored: bool = True
    quality_profile: str = ""
    root_folder_id: int | None = None


class BookPatch(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None
    release_date: str | None = None
    language: str | None = None
    publisher: str | None = None
    duration_seconds: int | None = None
    cover_url: str | None = None
    series_position: float | None = None
    monitored: bool | None = None
    quality_profile: str | None = None
    root_folder_id: int | None = None
    tags: list[str] | None = None


class ProviderIdOut(BaseModel):
    provider: str
    provider_id: str
    locale: str


class BookOut(BaseModel):
    id: int
    title: str
    subtitle: str
    description: str
    release_date: str | None
    language: str
    publisher: str
    duration_seconds: int
    cover_url: str | None
    series: str
    series_position: float | None
    authors: list[str]
    narrators: list[str]
    provider_ids: list[ProviderIdOut]
    file_count: int
    size_bytes: int
    formats: list[str]
    added_at: str | None
    monitored: bool
    quality_profile: str
    root_folder_id: int | None
    tags: list[TagRef]


class LibraryFileOut(BaseModel):
    id: int
    edition_id: int
    path: str
    size_bytes: int
    mtime: str | None
    format: str
    added_at: str


class OrganizeIn(BaseModel):
    root_folder_id: int | None = None
    pattern: str | None = None


class OrganizeItemOut(BaseModel):
    file_id: int
    source_path: str
    target_path: str
    status: str
    reason: str = ""


class OrganizePreviewOut(BaseModel):
    book_id: int
    root_folder: str
    pattern: str
    safe_to_apply: bool
    items: list[OrganizeItemOut]


class OrganizeApplyOut(BaseModel):
    book_id: int
    root_folder: str
    pattern: str
    moved_count: int
    items: list[OrganizeItemOut]


class LibraryStatsOut(BaseModel):
    book_count: int
    author_count: int
    narrator_count: int
    series_count: int
    file_count: int
    total_size_bytes: int
    root_folder_count: int


# -- root folders ---------------------------------------------------------------


def _ensure_schema() -> None:
    """Ensure library tables exist for direct/TestClient API calls.

    The ASGI lifespan migrates the DB in normal server startup, but some tests
    and local smoke probes call route handlers without a full lifespan cycle.
    Other stateful routers do the same defensive migration.
    """
    migrate()


def _get_or_create_tag_id(conn: Any, label: str) -> int | None:
    label = label.strip()
    if not label:
        return None
    row = conn.execute(
        "SELECT id FROM tags WHERE label = ? COLLATE NOCASE", (label,)
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cur = conn.execute("INSERT INTO tags (label) VALUES (?)", (label,))
    return int(cur.lastrowid or 0)


def _sync_entity_tags(
    conn: Any, mapping_table: str, entity_col: str, entity_id: int, labels: list[str]
) -> None:
    """Replace an entity's tag mapping rows with those matching `labels`.

    Unknown labels are created on the fly (see routes_tags.py for the
    dedicated tag CRUD API); duplicate/blank labels collapse naturally
    since tag ids are looked up by unique, non-empty label.
    """
    tag_ids = {
        tid for label in labels if (tid := _get_or_create_tag_id(conn, label)) is not None
    }
    conn.execute(f"DELETE FROM {mapping_table} WHERE {entity_col} = ?", (entity_id,))
    for tag_id in tag_ids:
        conn.execute(
            f"INSERT INTO {mapping_table} ({entity_col}, tag_id) VALUES (?, ?)",
            (entity_id, tag_id),
        )


def _get_entity_tags(
    conn: Any, mapping_table: str, entity_col: str, entity_id: int
) -> list[TagRef]:
    rows = conn.execute(
        f"""SELECT t.id, t.label, t.color
              FROM {mapping_table} m JOIN tags t ON t.id = m.tag_id
             WHERE m.{entity_col} = ?
             ORDER BY t.label COLLATE NOCASE""",
        (entity_id,),
    ).fetchall()
    return [TagRef(**dict(r)) for r in rows]


def _health_fields(health: FolderHealth) -> dict[str, Any]:
    return {
        "exists": health.exists,
        "writable": health.writable,
        "free_bytes": health.free_bytes,
        "total_bytes": health.total_bytes,
    }


_background_tasks: set[asyncio.Task] = set()


def _dispatch_health_issue_if_unhealthy(folder_id: int, path: str, health: FolderHealth) -> None:
    """Fire-and-forget health_issue dispatch for an unhealthy root folder.

    Runs on every GET while the folder stays unhealthy (no dedupe yet) --
    acceptable for now since dispatch_event is best-effort and idempotent
    on the receiving webhook side.
    """
    if health.exists and health.writable:
        return
    if not health.exists:
        message = f"Root folder {path!r} does not exist"
    else:
        message = f"Root folder {path!r} is not writable" + (
            f": {health.error}" if health.error else ""
        )
    task = asyncio.create_task(
        dispatch_event(
            "health_issue",
            {
                "integration": "root-folder",
                "root_folder_id": folder_id,
                "path": path,
                "message": message,
            },
        )
    )
    # Keep a strong reference until done; asyncio only weakly tracks tasks.
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@router.get("/api/v1/library/root-folders", response_model=list[RootFolderOut])
async def get_root_folders() -> list[RootFolderOut]:
    _ensure_schema()
    with get_conn() as conn:
        rows = list_root_folders(conn)
        paths = [r["path"] for r in rows]
        healths = await asyncio.gather(*(asyncio.to_thread(probe_root_folder, p) for p in paths))
        result = [
            RootFolderOut(
                **dict(r),
                tags=_get_entity_tags(conn, "root_folder_tags", "root_folder_id", r["id"]),
                **_health_fields(health),
            )
            for r, health in zip(rows, healths, strict=True)
        ]
    for r, health in zip(rows, healths, strict=True):
        _dispatch_health_issue_if_unhealthy(r["id"], r["path"], health)
    return result


@router.post("/api/v1/library/root-folders", response_model=RootFolderOut, status_code=201)
async def post_root_folder(data: RootFolderIn) -> RootFolderOut:
    _ensure_schema()
    with get_conn() as conn:
        try:
            folder_id = create_root_folder(conn, RootFolderCreate(**data.model_dump()))
        except Exception as exc:  # UNIQUE violation -> 409
            if "UNIQUE" in str(exc):
                raise HTTPException(409, f"Root folder {data.path!r} already exists") from exc
            raise
        row = get_root_folder(conn, folder_id)
        assert row is not None
        tags = _get_entity_tags(conn, "root_folder_tags", "root_folder_id", folder_id)
    health = await asyncio.to_thread(probe_root_folder, row["path"])
    return RootFolderOut(**dict(row), tags=tags, **_health_fields(health))


@router.delete("/api/v1/library/root-folders/{folder_id}", status_code=204)
async def remove_root_folder(folder_id: int) -> None:
    _ensure_schema()
    with get_conn() as conn:
        if not delete_root_folder(conn, folder_id):
            raise HTTPException(404, "Root folder not found")


@router.put("/api/v1/library/root-folders/{folder_id}/tags", response_model=list[TagRef])
async def put_root_folder_tags(folder_id: int, data: RootFolderTagsIn) -> list[TagRef]:
    _ensure_schema()
    with get_conn() as conn:
        if get_root_folder(conn, folder_id) is None:
            raise HTTPException(404, "Root folder not found")
        _sync_entity_tags(conn, "root_folder_tags", "root_folder_id", folder_id, data.tags)
        tags = _get_entity_tags(conn, "root_folder_tags", "root_folder_id", folder_id)
    return tags


@router.put("/api/v1/library/root-folders/{folder_id}/strategy", response_model=RootFolderOut)
async def put_root_folder_strategy(folder_id: int, data: RootFolderStrategyIn) -> RootFolderOut:
    """Change a root folder's import strategy (move/copy/hardlink, see #30)."""
    _ensure_schema()
    with get_conn() as conn:
        if not update_root_folder_strategy(conn, folder_id, data.import_strategy):
            raise HTTPException(404, "Root folder not found")
        row = get_root_folder(conn, folder_id)
        assert row is not None
        tags = _get_entity_tags(conn, "root_folder_tags", "root_folder_id", folder_id)
    health = await asyncio.to_thread(probe_root_folder, row["path"])
    return RootFolderOut(**dict(row), tags=tags, **_health_fields(health))


# -- books ------------------------------------------------------------------------


def _split_names(value: Any) -> list[str]:
    return value.split(", ") if value else []


def _book_file_stats(conn: Any, book_id: int) -> dict[str, Any]:
    """Aggregate file_count/size_bytes/formats/added_at across a book's editions.

    Per-book helper SQL, kept simple on purpose (see task notes).
    """
    row = conn.execute(
        """SELECT COUNT(lf.id) AS file_count,
                  COALESCE(SUM(lf.size_bytes), 0) AS size_bytes,
                  GROUP_CONCAT(DISTINCT lf.format) AS formats,
                  MIN(lf.added_at) AS added_at
             FROM editions e
             LEFT JOIN library_files lf ON lf.edition_id = e.id
            WHERE e.book_id = ?""",
        (book_id,),
    ).fetchone()
    formats = [f for f in (row["formats"] or "").split(",") if f]
    return {
        "file_count": row["file_count"] or 0,
        "size_bytes": row["size_bytes"] or 0,
        "formats": formats,
        "added_at": row["added_at"],
    }


def _book_out(conn: Any, book_id: int) -> BookOut:
    book = get_book(conn, book_id)
    assert book is not None
    pids = get_provider_ids(conn, "book", book_id)
    stats = _book_file_stats(conn, book_id)
    tags = _get_entity_tags(conn, "book_tags", "book_id", book_id)
    return BookOut(
        id=book["id"],
        title=book["title"],
        subtitle=book["subtitle"],
        description=book["description"],
        release_date=book["release_date"],
        language=book["language"],
        publisher=book["publisher"],
        duration_seconds=book["duration_seconds"],
        cover_url=book["cover_url"],
        series=book["series_name"] or "",
        series_position=book["series_position"],
        authors=_split_names(book["authors"]),
        narrators=_split_names(book["narrators"]),
        provider_ids=[ProviderIdOut(**p) for p in pids],
        monitored=bool(book["monitored"]),
        quality_profile=book["quality_profile"],
        root_folder_id=book["root_folder_id"],
        tags=tags,
        **stats,
    )


def _list_books_by_tag(conn: Any, tag: str, limit: int, offset: int) -> list[dict[str, Any]]:
    """Same shape as app.library.list_books, filtered to a single tag label.

    Kept as a standalone query (rather than extending list_books) since the
    tag filter needs an extra join that only applies to this one endpoint.
    """
    rows = conn.execute(
        """SELECT b.id, b.title, b.subtitle, b.description, b.language,
                  b.duration_seconds, b.cover_url, b.release_date, b.publisher,
                  b.monitored, b.quality_profile, b.root_folder_id,
                  s.name AS series_name, b.series_position,
                  (SELECT GROUP_CONCAT(a.name, ', ')
                     FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id ORDER BY ba.position) AS authors,
                  (SELECT GROUP_CONCAT(n.name, ', ')
                     FROM book_narrators bn JOIN narrators n ON n.id = bn.narrator_id
                    WHERE bn.book_id = b.id ORDER BY bn.position) AS narrators
             FROM books b
             LEFT JOIN series s ON s.id = b.series_id
             JOIN book_tags bt ON bt.book_id = b.id
             JOIN tags t ON t.id = bt.tag_id AND t.label = ? COLLATE NOCASE
            ORDER BY b.title LIMIT ? OFFSET ?""",
        (tag, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/api/v1/library/books", response_model=list[BookOut])
async def get_books(limit: int = 50, offset: int = 0, tag: str | None = None) -> list[BookOut]:
    _ensure_schema()
    with get_conn() as conn:
        books = (
            _list_books_by_tag(conn, tag, limit=limit, offset=offset)
            if tag is not None
            else list_books(conn, limit=limit, offset=offset)
        )
        result = []
        for b in books:
            pids = get_provider_ids(conn, "book", b["id"])
            stats = _book_file_stats(conn, b["id"])
            tags = _get_entity_tags(conn, "book_tags", "book_id", b["id"])
            result.append(
                BookOut(
                    id=b["id"],
                    title=b["title"],
                    subtitle=b["subtitle"],
                    description=b["description"],
                    release_date=b["release_date"],
                    language=b["language"],
                    publisher=b["publisher"],
                    duration_seconds=b["duration_seconds"],
                    cover_url=b["cover_url"],
                    series=b["series_name"] or "",
                    series_position=b["series_position"],
                    authors=_split_names(b["authors"]),
                    narrators=_split_names(b["narrators"]),
                    provider_ids=[ProviderIdOut(**p) for p in pids],
                    monitored=bool(b["monitored"]),
                    quality_profile=b["quality_profile"],
                    root_folder_id=b["root_folder_id"],
                    tags=tags,
                    **stats,
                )
            )
    return result


@router.get("/api/v1/library/stats", response_model=LibraryStatsOut)
async def get_library_stats() -> LibraryStatsOut:
    _ensure_schema()
    with get_conn() as conn:
        book_count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        author_count = conn.execute("SELECT COUNT(*) FROM authors").fetchone()[0]
        narrator_count = conn.execute("SELECT COUNT(*) FROM narrators").fetchone()[0]
        series_count = conn.execute("SELECT COUNT(*) FROM series").fetchone()[0]
        file_count = conn.execute("SELECT COUNT(*) FROM library_files").fetchone()[0]
        total_size_bytes = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM library_files"
        ).fetchone()[0]
        root_folder_count = conn.execute("SELECT COUNT(*) FROM root_folders").fetchone()[0]
    return LibraryStatsOut(
        book_count=book_count,
        author_count=author_count,
        narrator_count=narrator_count,
        series_count=series_count,
        file_count=file_count,
        total_size_bytes=total_size_bytes,
        root_folder_count=root_folder_count,
    )


@router.post("/api/v1/library/books", response_model=BookOut, status_code=201)
async def post_book(data: BookIn) -> BookOut:
    """Create a book, optionally with a root folder / quality profile chosen
    up front (Add wizard, #50). Both are validated before anything is
    persisted so an invalid choice never leaves a half-configured book row
    behind (see review note on the original PATCH-after-create flow)."""
    _ensure_schema()
    _validate_quality_profile(data.quality_profile)
    with get_conn() as conn:
        _validate_root_folder_id(conn, data.root_folder_id)
        if data.provider and data.provider_id:
            existing = find_book_by_provider_id(
                conn, data.provider, data.provider_id, data.locale
            )
            if existing is not None:
                raise HTTPException(
                    409,
                    f"Book with {data.provider} id {data.provider_id!r} already exists",
                )
        book_id = create_book(conn, BookCreate(**data.model_dump()))
        result = _book_out(conn, book_id)
    return result


@router.get("/api/v1/library/books/{book_id}", response_model=BookOut)
async def get_book_endpoint(book_id: int) -> BookOut:
    _ensure_schema()
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        result = _book_out(conn, book_id)
    return result


@router.get("/api/v1/library/books/{book_id}/files", response_model=list[LibraryFileOut])
async def get_book_files(book_id: int) -> list[LibraryFileOut]:
    _ensure_schema()
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        rows = conn.execute(
            """SELECT lf.id, lf.edition_id, lf.path, lf.size_bytes, lf.mtime,
                      lf.format, lf.added_at
                 FROM library_files lf
                 JOIN editions e ON e.id = lf.edition_id
                WHERE e.book_id = ?
                ORDER BY lf.path""",
            (book_id,),
        ).fetchall()
    return [LibraryFileOut(**dict(r)) for r in rows]


def _resolve_organize_root_and_pattern(
    conn: Any, book_id: int, data: OrganizeIn
) -> tuple[str, str]:
    """Resolve the (root folder path, pattern) an organize request runs against.

    ``root_folder_id`` on the request picks a specific configured root
    folder; otherwise the book's own root-folder preference (set at add
    time, see #50) is used; otherwise the first one by path order (see
    list_root_folders) is used, matching the MVP root-folder-selection
    rule in issue #29.
    """
    pattern = data.pattern or load_settings().media_management.file_name_pattern
    folder_id = data.root_folder_id
    if folder_id is None:
        book = get_book(conn, book_id)
        folder_id = book["root_folder_id"] if book else None
    if folder_id is not None:
        row = get_root_folder(conn, folder_id)
        if row is None:
            raise HTTPException(422, f"Root folder {folder_id} not found")
        return row["path"], pattern
    rows = list_root_folders(conn)
    if not rows:
        raise HTTPException(
            422, "No root folder configured; add one under Settings > Media Management first"
        )
    return rows[0]["path"], pattern


def _organize_items_out(items: Any) -> list[OrganizeItemOut]:
    return [
        OrganizeItemOut(
            file_id=i.file_id, source_path=i.source_path, target_path=i.target_path,
            status=i.status, reason=i.reason,
        )
        for i in items
    ]


@router.post(
    "/api/v1/library/books/{book_id}/organize/preview", response_model=OrganizePreviewOut
)
async def post_organize_preview(book_id: int, data: OrganizeIn) -> OrganizePreviewOut:
    """Preview a rename/organize run: never touches the filesystem beyond
    existence checks, never writes to the DB. See app/library/organizer.py."""
    _ensure_schema()
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        root_path, pattern = _resolve_organize_root_and_pattern(conn, book_id, data)
        preview = build_preview(conn, book_id, root_path, pattern)
    return OrganizePreviewOut(
        book_id=preview.book_id,
        root_folder=preview.root_folder,
        pattern=preview.pattern,
        safe_to_apply=preview.safe_to_apply,
        items=_organize_items_out(preview.items),
    )


@router.post("/api/v1/library/books/{book_id}/organize/apply", response_model=OrganizeApplyOut)
async def post_organize_apply(book_id: int, data: OrganizeIn) -> OrganizeApplyOut:
    """Apply a rename/organize run: only moves files when a fresh preview is
    entirely safe (every item ready/unchanged); rolls back best-effort on a
    partial failure. Requires media_management.rename_files to be enabled
    (opt-in MVP, see issue #29)."""
    _ensure_schema()
    if not load_settings().media_management.rename_files:
        raise HTTPException(
            400,
            "File organizing is disabled; enable Media Management > Rename files in Settings first",
        )
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        root_path, pattern = _resolve_organize_root_and_pattern(conn, book_id, data)
        preview = build_preview(conn, book_id, root_path, pattern)
        if not preview.safe_to_apply:
            raise HTTPException(
                409, "Preview is not safe to apply; resolve conflicts or missing files first"
            )
        result = apply_preview(conn, preview)
        if not result.success:
            raise HTTPException(500, {"message": result.error, "rollback": result.rollback})
        items_out = [
            OrganizeItemOut(
                file_id=i.file_id,
                source_path=i.source_path,
                target_path=i.target_path,
                status="moved" if i.status == "ready" else i.status,
                reason=i.reason,
            )
            for i in result.items
        ]
    return OrganizeApplyOut(
        book_id=book_id,
        root_folder=preview.root_folder,
        pattern=preview.pattern,
        moved_count=len(result.moved),
        items=items_out,
    )


def _validate_quality_profile(name: str) -> None:
    """Empty string ("inherit default") is always valid; otherwise the name
    must match a configured quality_profiles[].name."""
    if not name:
        return
    profiles = {p.name for p in load_settings().quality_profiles}
    if name not in profiles:
        raise HTTPException(422, f"Unknown quality profile {name!r}")


def _validate_root_folder_id(conn: Any, folder_id: int | None) -> None:
    """None ("no preference") is always valid; otherwise the id must match
    a configured root folder (Add flow / book detail root-folder picker, #50)."""
    if folder_id is None:
        return
    if get_root_folder(conn, folder_id) is None:
        raise HTTPException(422, f"Root folder {folder_id} not found")


@router.patch("/api/v1/library/books/{book_id}", response_model=BookOut)
async def patch_book(book_id: int, patch: BookPatch) -> BookOut:
    _ensure_schema()
    updates = patch.model_dump(exclude_unset=True)
    tags = updates.pop("tags", None)
    # root_folder_id is handled separately from the bulk update_book() path
    # below so that an explicit `null` (clear back to "no preference") is
    # distinguishable from "field not sent" -- update_book() otherwise drops
    # None values wholesale (see its docstring).
    root_folder_set = "root_folder_id" in updates
    root_folder_value = updates.pop("root_folder_id", None)
    if "quality_profile" in updates:
        _validate_quality_profile(updates["quality_profile"])
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        if root_folder_set:
            _validate_root_folder_id(conn, root_folder_value)
        updated_fields = update_book(conn, book_id, updates) if updates else False
        if root_folder_set:
            updated_fields = set_book_root_folder(conn, book_id, root_folder_value) or updated_fields
        if tags is not None:
            _sync_entity_tags(conn, "book_tags", "book_id", book_id, tags)
        elif not updated_fields:
            raise HTTPException(422, "No updatable fields provided")
        result = _book_out(conn, book_id)
    return result


@router.post("/api/v1/library/books/{book_id}/refresh", response_model=BookOut)
async def refresh_book(book_id: int) -> BookOut:
    """Re-fetch this book's metadata from its linked provider (Starr-style
    "Refresh metadata" toolbar action, #50).

    Minimal trigger: reuses the existing provider chain's get_detail() and
    update_book(), no new domain logic. A book with no linked provider id
    (e.g. one added by hand) has nothing to refresh from.
    """
    _ensure_schema()
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        pids = get_provider_ids(conn, "book", book_id)
    if not pids:
        raise HTTPException(422, "This book has no linked metadata provider to refresh from")
    pid = pids[0]

    chain = build_provider_chain()
    if pid["locale"]:
        chain.config.audible_locale = pid["locale"]
    detail = await chain.get_detail(pid["provider"], pid["provider_id"])
    if detail is None:
        raise HTTPException(502, "Metadata provider lookup failed; try again later")

    updates: dict[str, Any] = {
        "title": detail.title or None,
        "subtitle": detail.subtitle or None,
        "description": detail.description or None,
        "release_date": detail.release_date or None,
        "language": detail.language or None,
        "publisher": ", ".join(detail.publishers) if detail.publishers else None,
        "duration_seconds": detail.duration_seconds or None,
        "cover_url": detail.cover_url or None,
    }
    with get_conn() as conn:
        update_book(conn, book_id, updates)
        result = _book_out(conn, book_id)
    return result


@router.delete("/api/v1/library/books/{book_id}", status_code=204)
async def remove_book(book_id: int) -> None:
    _ensure_schema()
    with get_conn() as conn:
        if not delete_book(conn, book_id):
            raise HTTPException(404, "Book not found")
