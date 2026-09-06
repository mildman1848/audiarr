"""Conversion API: enqueue, list, retry, and cancel MP3->M4B jobs."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_db_path, load_settings
from app.conversion import M4BConvertarrClient, build_backend
from app.db import migrate

router = APIRouter(prefix="/api/v1/conversion", tags=["conversion"])

log = logging.getLogger("audiarr.api.conversion")


class ConversionJobOut(BaseModel):
    id: int
    book_id: int
    source_path: str
    output_path: str
    status: str
    backend: str
    error: str | None
    attempts: int
    created_at: str
    updated_at: str


class EnqueueRequest(BaseModel):
    book_id: int
    source_path: str
    output_path: str = ""


class EnqueueResponse(BaseModel):
    job_id: int
    status: str
    detail: str = ""


def _open_db() -> sqlite3.Connection:
    migrate()
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_out(row: sqlite3.Row) -> ConversionJobOut:
    return ConversionJobOut(
        id=row["id"],
        book_id=row["book_id"],
        source_path=row["source_path"],
        output_path=row["output_path"],
        status=row["status"],
        backend=row["backend"],
        error=row["error"],
        attempts=row["attempts"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


@router.get("/jobs", response_model=list[ConversionJobOut])
async def list_jobs() -> Any:
    """List all conversion jobs, newest first."""
    conn = _open_db()
    try:
        rows = conn.execute(
            "SELECT * FROM conversion_jobs ORDER BY id DESC LIMIT 500"
        ).fetchall()
        return [_row_to_out(r) for r in rows]
    finally:
        conn.close()


@router.post("/jobs", response_model=EnqueueResponse)
async def enqueue_job(request: EnqueueRequest) -> Any:
    """Create a queued conversion job; a worker picks it up asynchronously."""
    settings = load_settings().conversion
    if settings.backend == "disabled":
        raise HTTPException(409, "conversion backend is disabled")
    backend = build_backend(settings)
    if backend is None:
        raise HTTPException(409, "conversion backend is disabled")

    conn = _open_db()
    try:
        book = conn.execute(
            "SELECT id FROM books WHERE id = ?", (request.book_id,)
        ).fetchone()
        if book is None:
            raise HTTPException(404, f"book {request.book_id} not found")

        # One active job per book; re-enqueueing a finished book is allowed.
        active = conn.execute(
            """SELECT id FROM conversion_jobs
               WHERE book_id = ? AND status IN ('queued','running')""",
            (request.book_id,),
        ).fetchone()
        if active:
            raise HTTPException(409, f"active job {active['id']} already exists")

        cur = conn.execute(
            """INSERT INTO conversion_jobs
               (book_id, source_path, output_path, backend)
               VALUES (?, ?, ?, ?)""",
            (request.book_id, request.source_path, request.output_path, settings.backend),
        )
        conn.commit()
        job_id = int(cur.lastrowid or 0)
        log.info("enqueued conversion job %d for book %d (%s)", job_id, request.book_id, settings.backend)
        return EnqueueResponse(job_id=job_id, status="queued")
    finally:
        conn.close()


@router.post("/jobs/{job_id}/retry", response_model=ConversionJobOut)
async def retry_job(job_id: int) -> Any:
    """Reset a failed job back to queued for another worker attempt."""
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT * FROM conversion_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"job {job_id} not found")
        if row["status"] not in ("failed", "cancelled"):
            raise HTTPException(409, f"job {job_id} is {row['status']}, not failed/cancelled")

        conn.execute(
            """UPDATE conversion_jobs
               SET status = 'queued', error = NULL, updated_at = datetime('now')
               WHERE id = ?""",
            (job_id,),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM conversion_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_out(updated)
    finally:
        conn.close()


@router.post("/jobs/{job_id}/cancel", response_model=ConversionJobOut)
async def cancel_job(job_id: int) -> Any:
    """Cancel a queued job (running jobs cannot be cancelled via API)."""
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT * FROM conversion_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"job {job_id} not found")
        if row["status"] != "queued":
            raise HTTPException(409, f"job {job_id} is {row['status']}, only queued jobs can be cancelled")

        conn.execute(
            """UPDATE conversion_jobs
               SET status = 'cancelled', updated_at = datetime('now')
               WHERE id = ?""",
            (job_id,),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM conversion_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_out(updated)
    finally:
        conn.close()


@router.get("/status")
async def conversion_status() -> Any:
    """Backend configuration + health summary for the UI."""
    settings = load_settings().conversion
    backend = build_backend(settings)
    if backend is None:
        return {"backend": "disabled", "healthy": False}
    healthy = False
    if isinstance(backend, M4BConvertarrClient):
        healthy = await backend.health()
    return {
        "backend": settings.backend,
        "base_url": settings.base_url,
        "healthy": healthy,
        "delete_originals": settings.delete_originals,
    }
