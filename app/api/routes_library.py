"""Library API: root folders and book entries."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_conn
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


class RootFolderIn(BaseModel):
    path: str
    label: str = ""


class RootFolderOut(BaseModel):
    id: int
    path: str
    label: str
    created_at: str
    updated_at: str


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


# -- root folders ---------------------------------------------------------------


@router.get("/api/v1/library/root-folders", response_model=list[RootFolderOut])
async def get_root_folders() -> list[RootFolderOut]:
    with get_conn() as conn:
        rows = list_root_folders(conn)
    return [RootFolderOut(**dict(r)) for r in rows]


@router.post("/api/v1/library/root-folders", response_model=RootFolderOut, status_code=201)
async def post_root_folder(data: RootFolderIn) -> RootFolderOut:
    with get_conn() as conn:
        try:
            folder_id = create_root_folder(conn, RootFolderCreate(**data.model_dump()))
        except Exception as exc:  # UNIQUE violation -> 409
            if "UNIQUE" in str(exc):
                raise HTTPException(409, f"Root folder {data.path!r} already exists") from exc
            raise
        row = get_root_folder(conn, folder_id)
    assert row is not None
    return RootFolderOut(**dict(row))


@router.delete("/api/v1/library/root-folders/{folder_id}", status_code=204)
async def remove_root_folder(folder_id: int) -> None:
    with get_conn() as conn:
        if not delete_root_folder(conn, folder_id):
            raise HTTPException(404, "Root folder not found")


# -- books ------------------------------------------------------------------------


def _split_names(value: Any) -> list[str]:
    return value.split(", ") if value else []


def _book_out(conn: Any, book_id: int) -> BookOut:
    book = get_book(conn, book_id)
    assert book is not None
    pids = get_provider_ids(conn, "book", book_id)
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
    )


@router.get("/api/v1/library/books", response_model=list[BookOut])
async def get_books(limit: int = 50, offset: int = 0) -> list[BookOut]:
    with get_conn() as conn:
        books = list_books(conn, limit=limit, offset=offset)
        result = []
        for b in books:
            pids = get_provider_ids(conn, "book", b["id"])
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
                )
            )
    return result


@router.post("/api/v1/library/books", response_model=BookOut, status_code=201)
async def post_book(data: BookIn) -> BookOut:
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
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        result = _book_out(conn, book_id)
    return result


@router.patch("/api/v1/library/books/{book_id}", response_model=BookOut)
async def patch_book(book_id: int, patch: BookPatch) -> BookOut:
    updates = patch.model_dump(exclude_unset=True)
    with get_conn() as conn:
        if get_book(conn, book_id) is None:
            raise HTTPException(404, "Book not found")
        if not update_book(conn, book_id, updates):
            raise HTTPException(422, "No updatable fields provided")
        result = _book_out(conn, book_id)
    return result


@router.delete("/api/v1/library/books/{book_id}", status_code=204)
async def remove_book(book_id: int) -> None:
    with get_conn() as conn:
        if not delete_book(conn, book_id):
            raise HTTPException(404, "Book not found")
