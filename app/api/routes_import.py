"""Import API: trigger scans/matching runs and query job history."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.routes_metadata import build_provider_chain
from app.config import get_db_path
from app.db import migrate
from app.library.importer import ImportMatchError, import_single_folder, run_import
from app.library.scanner import _guess_title_author
from app.providers.chain import ProviderChain

router = APIRouter(prefix="/api/v1/import", tags=["import"])

log = logging.getLogger("audiarr.api.import")


class ImportRunRequest(BaseModel):
    root_folder_id: int
    dry_run: bool = True
    locale: str = "us"


class CandidateOut(BaseModel):
    folder_path: str
    status: str
    matched_book_id: int | None = None
    matched_asin: str | None = None
    score: float | None = None
    method: str | None = None
    detail: str = ""


class ImportRunResponse(BaseModel):
    root_folder_id: int
    root_path: str
    dry_run: bool
    total_candidates: int
    matched: int
    unmatched: int
    errors: int
    file_count: int
    total_size_bytes: int
    message: str
    results: list[CandidateOut]


class ImportJobOut(BaseModel):
    id: int
    source_path: str
    status: str
    error: str | None
    created_at: str


class UnmatchedFolderOut(BaseModel):
    folder_path: str
    guessed_title: str
    guessed_author: str
    last_tried_at: str
    exists: bool


class IgnoreEntryOut(BaseModel):
    id: int
    path: str
    note: str
    created_at: str


class IgnoreRequest(BaseModel):
    path: str
    note: str = ""


class UnignoreRequest(BaseModel):
    path: str


class MatchRequest(BaseModel):
    folder_path: str
    asin: str
    locale: str = "us"


class MatchResponse(BaseModel):
    status: str
    book_id: int | None
    asin: str | None
    title: str


def _chain() -> ProviderChain:
    return build_provider_chain()


def _open_db() -> sqlite3.Connection:
    """Open a DB connection, ensuring migrations have run."""
    migrate()
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


@router.post("/run", response_model=ImportRunResponse)
async def run_import_endpoint(request: ImportRunRequest) -> Any:
    """Scan + match + (optionally) persist one root folder.

    Defaults to a dry run: nothing is written until ``dry_run=false``.
    """
    conn = _open_db()
    try:
        summary = await run_import(
            conn=conn,
            chain=_chain(),
            root_folder_id=request.root_folder_id,
            dry_run=request.dry_run,
            locale=request.locale,
        )
        return ImportRunResponse(
            root_folder_id=summary.root_folder_id,
            root_path=summary.root_path,
            dry_run=summary.dry_run,
            total_candidates=summary.total_candidates,
            matched=summary.matched,
            unmatched=summary.unmatched,
            errors=summary.errors,
            file_count=summary.file_count,
            total_size_bytes=summary.total_size_bytes,
            message=summary.message,
            results=[
                CandidateOut(
                    folder_path=r.folder_path,
                    status=r.status,
                    matched_book_id=r.matched_book_id,
                    matched_asin=r.matched_asin,
                    score=r.score,
                    method=r.method,
                    detail=r.detail,
                )
                for r in summary.results
            ],
        )
    finally:
        conn.commit()
        conn.close()


@router.get("/jobs", response_model=list[ImportJobOut])
async def list_jobs_endpoint() -> Any:
    """List recent import jobs (audit trail of past runs)."""
    conn = _open_db()
    try:
        rows = conn.execute(
            """SELECT id, source_path, status, error, created_at
               FROM import_jobs ORDER BY id DESC LIMIT 200"""
        ).fetchall()
        return [
            ImportJobOut(
                id=row[0],
                source_path=row[1],
                status=row[2],
                error=row[3],
                created_at=str(row[4]),
            )
            for row in rows
        ]
    finally:
        conn.close()


@router.get("/unmatched", response_model=list[UnmatchedFolderOut])
async def list_unmatched_endpoint() -> Any:
    """List folders that stayed unmatched after past import runs.

    Sourced from import_jobs, deduped by source_path with the LATEST row
    per path deciding the outcome (a later successful re-import removes a
    path from this list even though an older failed row still exists).
    Folders explicitly ignored are excluded.
    """
    conn = _open_db()
    try:
        rows = conn.execute(
            """SELECT source_path, status, error, created_at
               FROM import_jobs ORDER BY id"""
        ).fetchall()
        latest: dict[str, sqlite3.Row] = {}
        for row in rows:
            latest[row["source_path"]] = row  # ascending id -> last write wins

        ignored = {
            row[0] for row in conn.execute("SELECT path FROM import_ignores").fetchall()
        }

        out: list[UnmatchedFolderOut] = []
        for path, row in latest.items():
            if row["status"] != "failed" or row["error"] != "unmatched":
                continue
            if path in ignored:
                continue
            title, author = _guess_title_author(Path(path).name)
            out.append(
                UnmatchedFolderOut(
                    folder_path=path,
                    guessed_title=title,
                    guessed_author=author,
                    last_tried_at=str(row["created_at"]),
                    exists=Path(path).exists(),
                )
            )
        out.sort(key=lambda u: u.folder_path)
        return out
    finally:
        conn.close()


@router.get("/ignores", response_model=list[IgnoreEntryOut])
async def list_ignores_endpoint() -> Any:
    """List folders excluded from future import runs."""
    conn = _open_db()
    try:
        rows = conn.execute(
            """SELECT id, path, note, created_at
               FROM import_ignores ORDER BY path"""
        ).fetchall()
        return [
            IgnoreEntryOut(id=row[0], path=row[1], note=row[2], created_at=str(row[3]))
            for row in rows
        ]
    finally:
        conn.close()


@router.post("/ignore", status_code=204)
async def ignore_folder_endpoint(request: IgnoreRequest) -> None:
    """Mark a folder as ignored; idempotent, accepts a nonexistent path."""
    conn = _open_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO import_ignores (path, note) VALUES (?, ?)",
            (request.path, request.note),
        )
    finally:
        conn.commit()
        conn.close()


@router.post("/unignore", status_code=204)
async def unignore_folder_endpoint(request: UnignoreRequest) -> None:
    """Remove a folder from the ignore list."""
    conn = _open_db()
    try:
        conn.execute("DELETE FROM import_ignores WHERE path = ?", (request.path,))
    finally:
        conn.commit()
        conn.close()


@router.post("/match", response_model=MatchResponse)
async def match_folder_endpoint(request: MatchRequest) -> Any:
    """Manually match one unmatched folder to a provider ASIN.

    Persists the book and files (like a normal import match) and records
    an import_jobs row. 404 when the folder is gone; 502 when no configured
    provider can resolve the given ASIN.
    """
    conn = _open_db()
    try:
        try:
            result = await import_single_folder(
                conn, _chain(), request.folder_path, request.asin, request.locale
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ImportMatchError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — surface provider failures, don't crash the API
            raise HTTPException(status_code=502, detail=f"provider lookup failed: {exc}") from exc

        title = ""
        if result.matched_book_id is not None:
            row = conn.execute(
                "SELECT title FROM books WHERE id = ?", (result.matched_book_id,)
            ).fetchone()
            title = row["title"] if row else ""

        return MatchResponse(
            status=result.status,
            book_id=result.matched_book_id,
            asin=result.matched_asin,
            title=title,
        )
    finally:
        conn.commit()
        conn.close()
