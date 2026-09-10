"""Conversion worker: dispatch queued jobs to the configured backend.

Webhook-driven flow (m4b-convertarr HTTP backend):

  1. Worker claims a queued job, marks it ``running``, and POSTs to the
     converter's ``/api/convert``. The converter processes its inbox
     asynchronously (watchdog-driven), so "accepted" is NOT "done".
  2. When the converter finishes a book it runs its POST_CONVERT hook,
     which calls ``POST /api/v1/webhooks/m4b-convertarr`` on Audiarr with
     the converted .m4b path. The webhook matches the job by title,
     imports the file, and marks the job ``completed``.
  3. Jobs whose backend never calls back are failed after
     ``job_timeout_hours`` (converter crash, lost webhook).

Command backend jobs run synchronously in-process and complete inline.

Deliberately single-flight: one job at a time per process, mirroring
m4b-convertarr's sequential inbox processing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_db_path, load_settings
from app.conversion import build_backend
from app.db import migrate

log = logging.getLogger("audiarr.conversion.worker")

POLL_INTERVAL_SECONDS = 10


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


async def process_one_job() -> bool:
    """Claim and dispatch one queued job. Returns True if work happened."""
    settings = load_settings().conversion
    backend = build_backend(settings)
    if backend is None:
        return False

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM conversion_jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return False

        conn.execute(
            """UPDATE conversion_jobs
               SET status = 'running', attempts = attempts + 1,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (row["id"],),
        )
        conn.commit()
        job_id = row["id"]
        log.info(
            "dispatching conversion job %d (book %s, attempt %d)",
            job_id, row["book_id"], row["attempts"] + 1,
        )

        result = await backend.convert(row["source_path"], row["output_path"])

        if settings.backend == "command":
            # Synchronous backends finish inline.
            _finish_job(job_id, result.ok, result.detail)
        elif result.ok:
            # HTTP backend accepted the work; real completion arrives via
            # the POST_CONVERT webhook (routes_webhooks).
            log.info(
                "conversion job %d accepted by backend; waiting for webhook",
                job_id,
            )
        else:
            _finish_job(job_id, False, result.detail)
        return True
    finally:
        conn.close()


def _finish_job(job_id: int, ok: bool, detail: str, completed_path: str = "") -> None:
    """Record a job's terminal state."""
    conn = sqlite3.connect(get_db_path())
    try:
        if ok:
            conn.execute(
                """UPDATE conversion_jobs
                   SET status = 'completed', error = NULL, completed_path = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (completed_path, job_id),
            )
            suffix = f" ({completed_path})" if completed_path else ""
            log.info("conversion job %d completed%s", job_id, suffix)
        else:
            conn.execute(
                """UPDATE conversion_jobs
                   SET status = 'failed', error = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (detail[:500], job_id),
            )
            log.warning("conversion job %d failed: %s", job_id, detail)
        conn.commit()
    finally:
        conn.close()


async def process_timeouts() -> int:
    """Fail running jobs older than job_timeout_hours; returns count."""
    settings = load_settings().conversion
    hours = max(1, settings.job_timeout_hours)
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, updated_at FROM conversion_jobs
               WHERE status = 'running'"""
        ).fetchall()
        failed = 0
        for row in rows:
            try:
                updated = datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            age_hours = (datetime.utcnow() - updated).total_seconds() / 3600
            if age_hours > hours:
                _finish_job(
                    int(row["id"]),
                    False,
                    f"timeout: no webhook within {hours}h",
                )
                failed += 1
        return failed
    finally:
        conn.close()


def complete_job_from_webhook(
    job_id: int, completed_path: str
) -> tuple[bool, str]:
    """Mark a running job completed from the converter's webhook.

    Returns (ok, message). Caller (webhook route) handles the import of
    the converted file and original deletion.
    """
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM conversion_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return False, f"job {job_id} not found"
        if row["status"] != "running":
            return False, f"job {job_id} is {row['status']}, expected running"

        conn.execute(
            """UPDATE conversion_jobs
               SET status = 'completed', error = NULL, completed_path = ?,
                   updated_at = datetime('now')
               WHERE id = ?""",
            (completed_path, job_id),
        )
        conn.commit()
        return True, f"job {job_id} completed"
    finally:
        conn.close()


async def worker_loop(stop_event: asyncio.Event) -> None:
    """Periodically dispatch queued jobs and reap timeouts until stopped."""
    migrate()
    log.info("conversion worker started (poll every %ss)", POLL_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            did_work = await process_one_job()
            await process_timeouts()
            if not did_work:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=POLL_INTERVAL_SECONDS
                    )
        except Exception:  # noqa: BLE001 — the loop must survive bad jobs
            log.exception("conversion worker tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
    log.info("conversion worker stopped")


async def enqueue_for_book(conn: Any, book_id: int, source_path: str, output_path: str = "") -> int | None:
    """Enqueue a conversion job if the backend is enabled; None if disabled.

    Used by the import pipeline: after a successful import of MP3 files,
    offer them to the conversion backend automatically.
    """
    settings = load_settings().conversion
    if settings.backend == "disabled":
        backend = None
    else:
        backend = build_backend(settings)

    active = conn.execute(
        """SELECT id FROM conversion_jobs
           WHERE book_id = ? AND status IN ('queued','running')""",
        (book_id,),
    ).fetchone()
    if active:
        return int(active["id"] if not isinstance(active, tuple) else active[0])

    if backend is None:
        return None

    cur = conn.execute(
        """INSERT INTO conversion_jobs (book_id, source_path, output_path, backend)
           VALUES (?, ?, ?, ?)""",
        (book_id, source_path, output_path, settings.backend),
    )
    conn.commit()
    job_id = int(cur.lastrowid or 0)
    log.info("auto-enqueued conversion job %d for imported book %d", job_id, book_id)
    return job_id


def delete_originals_for_job(job_id: int) -> tuple[bool, int, str]:
    """Delete the source files of a completed job when delete_originals is on.

    Returns (deleted_ok, file_count, message). Only runs when the setting
    is enabled AND the job is completed AND the completed (converted)
    path exists on disk — never delete originals we cannot verify were
    successfully replaced.
    """
    settings = load_settings().conversion
    if not settings.delete_originals:
        return True, 0, "delete_originals disabled; keeping source files"

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM conversion_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return False, 0, f"job {job_id} not found"
        if row["status"] != "completed":
            return False, 0, f"job {job_id} not completed ({row['status']})"
        if row["originals_deleted"]:
            return True, 0, "originals already deleted"
        completed_path = Path(row["completed_path"])
        if not completed_path.is_file():
            return False, 0, f"converted file missing: {completed_path}"

        source = Path(row["source_path"])
        removed = 0
        # Source is a folder of audio files (import scanner convention).
        if source.is_dir():
            for f in sorted(source.rglob("*")):
                if f.is_file():
                    f.unlink()
                    removed += 1
            with contextlib.suppress(OSError):
                source.rmdir()
        elif source.is_file():
            source.unlink()
            removed = 1

        conn.execute(
            "UPDATE conversion_jobs SET originals_deleted = ? WHERE id = ?",
            (removed, job_id),
        )
        conn.commit()
        return True, removed, f"deleted {removed} original file(s)"
    finally:
        conn.close()
