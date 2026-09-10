"""Inbound webhooks: m4b-convertarr POST_CONVERT completion callbacks.

Flow (roadmap #6 webhook mode):

  m4b-convertarr finishes a book
    -> POST_CONVERT script (audiarr-convertarr-hook, shipped in
       contrib/) fires with AUTO_M4B_* env vars
    -> hook POSTs here: /api/v1/webhooks/m4b-convertarr
    -> Audiarr matches the running conversion job (by title/author
       against the book, fuzzy when needed)
    -> the converted .m4b is imported as a new edition + library_file
    -> job completes; if delete_originals is enabled the source MP3s
       are removed (only after verifying the converted file exists)

Auth: X-Api-Key header when settings.conversion.webhook_api_key is set.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import get_db_path, load_settings
from app.db import migrate

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

log = logging.getLogger("audiarr.api.webhooks")


class ConvertarrPayload(BaseModel):
    """Payload the audiarr-convertarr-hook script sends."""

    converted_path: str = Field(default="", description="absolute .m4b path")
    title: str = ""
    author: str = ""
    source_job_id: int | None = Field(
        default=None, description="conversion job id, when known"
    )
    status: str = "completed"  # completed | failed


class WebhookResponse(BaseModel):
    accepted: bool
    job_id: int | None = None
    book_id: int | None = None
    detail: str = ""


def _open_db() -> sqlite3.Connection:
    migrate()
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _authorized(api_key_header: str) -> bool:
    expected = load_settings().conversion.webhook_api_key
    if not expected:
        return True  # unauthenticated mode (isolated networks)
    return bool(api_key_header) and api_key_header == expected


@router.post("/m4b-convertarr", response_model=WebhookResponse)
async def convertarr_webhook(
    payload: ConvertarrPayload,
    request: Request,
    x_api_key: str = Header(default="", alias="X-Api-Key"),
) -> Any:
    """Completion callback from the m4b-convertarr POST_CONVERT hook."""
    if not _authorized(x_api_key):
        raise HTTPException(401, "invalid or missing X-Api-Key")

    if payload.status == "failed":
        return _handle_failure(payload)

    if not payload.converted_path:
        raise HTTPException(422, "converted_path is required for completed status")

    converted = Path(payload.converted_path)
    if not converted.is_file():
        # The converter reports paths from ITS mount namespace; if the
        # file is not visible under that path here, record and let the
        # user reconcile mount differences.
        log.warning(
            "webhook: converted path not visible to audiarr: %s", converted
        )

    conn = _open_db()
    try:
        job = _match_running_job(conn, payload)
        if job is None:
            return WebhookResponse(
                accepted=False,
                detail="no running conversion job matched this callback",
            )
        job_id = int(job["id"])
        book_id = int(job["book_id"])

        # Import the converted file as a new edition + library_file.
        imported = _import_converted_file(conn, book_id, converted, payload)

        conn.execute(
            """UPDATE conversion_jobs
               SET status = 'completed', error = NULL, completed_path = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (str(converted), job_id),
        )
        conn.commit()

        detail = f"job {job_id}: imported={imported}"

        # Optional original cleanup — guarded, only when enabled.
        from app.conversion.worker import delete_originals_for_job

        del_ok, removed, del_msg = delete_originals_for_job(job_id)
        detail += f"; originals: {del_msg}"

        log.info("webhook completed job %d (book %d): %s", job_id, book_id, detail)
        return WebhookResponse(
            accepted=True, job_id=job_id, book_id=book_id, detail=detail
        )
    finally:
        conn.close()


def _handle_failure(payload: ConvertarrPayload) -> WebhookResponse:
    """Converter reported a failure for a job we dispatched."""
    conn = _open_db()
    try:
        job = _match_running_job(conn, payload)
        if job is None:
            return WebhookResponse(
                accepted=False, detail="no running job matched failure callback"
            )
        conn.execute(
            """UPDATE conversion_jobs
               SET status = 'failed', error = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (f"converter reported failure: {payload.title}", int(job["id"])),
        )
        conn.commit()
        log.warning("webhook failed job %d (%s)", job["id"], payload.title)
        return WebhookResponse(
            accepted=True, job_id=int(job["id"]), detail="job marked failed"
        )
    finally:
        conn.close()


def _match_running_job(
    conn: sqlite3.Connection, payload: ConvertarrPayload
) -> sqlite3.Row | None:
    """Find the running job this callback belongs to.

    Explicit source_job_id wins; otherwise match by fuzzy title against
    the book titles of running jobs (the converter knows file names,
    not our internal ids).
    """
    if payload.source_job_id is not None:
        row = conn.execute(
            """SELECT * FROM conversion_jobs
               WHERE id = ? AND status = 'running'""",
            (payload.source_job_id,),
        ).fetchone()
        if row is not None:
            return row
        # Fall through to title matching (id may reference an older run).

    running = conn.execute(
        """SELECT cj.*, b.title AS book_title
             FROM conversion_jobs cj
             JOIN books b ON b.id = cj.book_id
            WHERE cj.status = 'running'
            ORDER BY cj.id"""
    ).fetchall()
    if not running:
        return None

    from app.library.matcher import normalize_title

    want = normalize_title(payload.title or "")
    if not want:
        # No title hint: if exactly one job is running, assume it.
        return running[0] if len(running) == 1 else None

    best: sqlite3.Row | None = None
    best_score = 0.0
    for row in running:
        score = _title_similarity(want, normalize_title(row["book_title"]))
        if score > best_score:
            best, best_score = row, score
    if best is not None and best_score >= 0.5:
        return best
    return None


def _title_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


def _import_converted_file(
    conn: sqlite3.Connection,
    book_id: int,
    converted: Path,
    payload: ConvertarrPayload,
) -> bool:
    """Record the converted .m4b as an edition + library_file for the book."""
    try:
        size = converted.stat().st_size if converted.is_file() else 0
    except OSError:
        size = 0

    # Use the persisted default locale (read at call time, never cached)
    # so imported editions match the configured Audible locale — mirrors
    # how the import pipeline threads a locale through.
    locale = load_settings().metadata.audible_locale
    cur = conn.execute(
        """INSERT INTO editions (book_id, format, locale)
           VALUES (?, 'm4b', ?)""",
        (book_id, locale),
    )
    edition_id = int(cur.lastrowid or 0)
    conn.execute(
        """INSERT OR IGNORE INTO library_files
           (edition_id, path, size_bytes, format)
           VALUES (?, ?, ?, 'm4b')""",
        (edition_id, str(converted), size),
    )
    return True
