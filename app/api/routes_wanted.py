"""Wanted/Missing API: monitored books without any library files.

First slice of Arr-style Wanted/Missing tracking. No indexer search or
grab actions live here yet — this only surfaces the gap between "the user
wants this book" (monitored=1) and "it's actually on disk" (zero files
across all editions).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import get_conn, migrate

log = logging.getLogger("audiarr.api.wanted")

router = APIRouter()


class WantedBookOut(BaseModel):
    id: int
    title: str
    authors: list[str]
    series: str
    release_date: str | None
    language: str
    publisher: str
    cover_url: str | None
    monitored: bool
    reason: str


def _ensure_schema() -> None:
    """Defensive migration, mirroring the other stateful routers."""
    migrate()


@router.get("/api/v1/wanted/missing", response_model=list[WantedBookOut])
async def get_wanted_missing() -> list[WantedBookOut]:
    _ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT b.id, b.title, b.release_date, b.language, b.publisher,
                      b.cover_url, s.name AS series_name,
                      (SELECT GROUP_CONCAT(a.name, ', ')
                         FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                        WHERE ba.book_id = b.id ORDER BY ba.position) AS authors,
                      COALESCE(COUNT(lf.id), 0) AS file_count
                 FROM books b
                 LEFT JOIN series s ON s.id = b.series_id
                 LEFT JOIN editions e ON e.book_id = b.id
                 LEFT JOIN library_files lf ON lf.edition_id = e.id
                WHERE b.monitored = 1
                GROUP BY b.id
               HAVING COALESCE(COUNT(lf.id), 0) = 0
                ORDER BY b.title""",
        ).fetchall()

    return [
        WantedBookOut(
            id=r["id"],
            title=r["title"],
            authors=r["authors"].split(", ") if r["authors"] else [],
            series=r["series_name"] or "",
            release_date=r["release_date"],
            language=r["language"],
            publisher=r["publisher"],
            cover_url=r["cover_url"],
            monitored=True,
            reason="missingFiles",
        )
        for r in rows
    ]
