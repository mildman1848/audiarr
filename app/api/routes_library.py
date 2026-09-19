"""Library API: root folders and book entries."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import load_settings
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
    update_book,
)

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


class RootFolderTagsIn(BaseModel):
    tags: list[str] = Field(default_factory=list)


class RootFolderOut(BaseModel):
    id: int
    path: str
    label: str
    created_at: str
    updated_at: str
    tags: list[TagRef]


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
    tags: list[TagRef]


class LibraryFileOut(BaseModel):
    id: int
    edition_id: int
    path: str
    size_bytes: int
    mtime: str | None
    format: str
    added_at: str


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


@router.get("/api/v1/library/root-folders", response_model=list[RootFolderOut])
async def get_root_folders() -> list[RootFolderOut]:
    _ensure_schema()
    with get_conn() as conn:
        rows = list_root_folders(conn)
        result = [
            RootFolderOut(
                **dict(r),
                tags=_get_entity_tags(conn, "root_folder_tags", "root_folder_id", r["id"]),
            )
            for r in rows
        ]
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
    return RootFolderOut(**dict(row), tags=tags)


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
                  b.monitored, b.quality_profile,
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
    _ensure_schema()
    with get_conn() as conn:
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


def _validate_quality_profile(name: str) -> None:
    """Empty string ("inherit default") is always valid; otherwise the name
    must match a configured quality_profiles[].name."""
    if not name:
        return
    profiles = {p.name for p in load_settings().quality_profiles}
    if name not in profiles:
        raise HTTPException(422, f"Unknown quality profile {name!r}")


@router.patch("/api/v1/library/books/{book_id}", response_model=BookOut)
async def patch_book(book_id: int, patch: BookPatch) -> BookOut:
    _ensure_schema()
    updates = patch.model_dump(exclude_unset=True)
    tags = updates.pop("tags", None)
    if "quality_profile" in updates:
        _validate_quality_profile(updates["quality_profile"])
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        updated_fields = update_book(conn, book_id, updates) if updates else False
        if tags is not None:
            _sync_entity_tags(conn, "book_tags", "book_id", book_id, tags)
        elif not updated_fields:
            raise HTTPException(422, "No updatable fields provided")
        result = _book_out(conn, book_id)
    return result


@router.delete("/api/v1/library/books/{book_id}", status_code=204)
async def remove_book(book_id: int) -> None:
    _ensure_schema()
    with get_conn() as conn:
        if not delete_book(conn, book_id):
            raise HTTPException(404, "Book not found")
