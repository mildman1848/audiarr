"""Import API: trigger scans/matching runs and query job history."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.routes_metadata import build_provider_chain
from app.config import get_db_path
from app.db import migrate
from app.library.importer import run_import
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
