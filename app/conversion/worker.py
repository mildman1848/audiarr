"""Conversion worker: dispatch queued jobs to the configured backend.

Runs as a lightweight asyncio loop started from the app lifespan. Each
tick claims the oldest queued job, marks it running, calls the backend,
and records completed/failed with the backend's detail message.

Deliberately single-flight: one job at a time per process. m4b-convertarr
itself processes the inbox sequentially, so parallel dispatch would only
create noise. Scale-out (multiple workers/containers) can come later via
a job lease column.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from typing import Any

from app.config import get_db_path, load_settings
from app.conversion import build_backend
from app.db import migrate

log = logging.getLogger("audiarr.conversion.worker")

POLL_INTERVAL_SECONDS = 10


async def process_one_job() -> bool:
    """Claim and run one queued job. Returns True if a job was processed."""
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
            "running conversion job %d (book %s, attempt %d)",
            job_id, row["book_id"], row["attempts"] + 1,
        )

        result = await backend.convert(row["source_path"], row["output_path"])

        if result.ok:
            conn.execute(
                """UPDATE conversion_jobs
                   SET status = 'completed', error = NULL,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (job_id,),
            )
            log.info("conversion job %d completed", job_id)
        else:
            conn.execute(
                """UPDATE conversion_jobs
                   SET status = 'failed', error = ?,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (result.detail[:500], job_id),
            )
            log.warning("conversion job %d failed: %s", job_id, result.detail)
        conn.commit()
        return True
    finally:
        conn.close()


async def worker_loop(stop_event: asyncio.Event) -> None:
    """Periodically drain queued conversion jobs until stopped."""
    migrate()
    log.info("conversion worker started (poll every %ss)", POLL_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            did_work = await process_one_job()
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
        return None

    active = conn.execute(
        """SELECT id FROM conversion_jobs
           WHERE book_id = ? AND status IN ('queued','running')""",
        (book_id,),
    ).fetchone()
    if active:
        return int(active["id"] if not isinstance(active, tuple) else active[0])

    cur = conn.execute(
        """INSERT INTO conversion_jobs (book_id, source_path, output_path, backend)
           VALUES (?, ?, ?, ?)""",
        (book_id, source_path, output_path, settings.backend),
    )
    conn.commit()
    job_id = int(cur.lastrowid or 0)
    log.info("auto-enqueued conversion job %d for imported book %d", job_id, book_id)
    return job_id
