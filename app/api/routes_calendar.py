"""Calendar API: books with a known release_date, windowed by date range.

Sonarr-style: the query is a plain [start, end] window over release_date,
with an optional ``monitored`` filter. Unlike ``monitored`` on
BookIn/BookPatch (a hard True/False on the book row), the API here treats
the query param as optional filtering: omit it to get every book in range
regardless of monitored state, or pass ``true``/``false`` to see only one
side of it. A caller that wants the classic "hide unmonitored" Sonarr
calendar view passes ``monitored=true`` explicitly.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_conn, migrate

log = logging.getLogger("audiarr.api.calendar")

router = APIRouter()


class CalendarBookOut(BaseModel):
    id: int
    title: str
    authors: list[str]
    series: str
    release_date: str | None
    cover_url: str | None
    monitored: bool
    asin: str | None


def _ensure_schema() -> None:
    """Defensive migration, mirroring the other stateful routers."""
    migrate()


@router.get("/api/v1/calendar", response_model=list[CalendarBookOut])
async def get_calendar(
    start: date, end: date, monitored: bool | None = None
) -> list[CalendarBookOut]:
    """Books with a release_date inside [start, end] (inclusive).

    ``start``/``end`` are ISO dates (``YYYY-MM-DD``); FastAPI/pydantic
    already 422s on an unparsable date, so the only manual check needed
    here is the domain rule that start must not be after end.
    """
    _ensure_schema()
    if start > end:
        raise HTTPException(400, "start must be on or before end")

    query = """SELECT b.id, b.title, b.release_date, b.cover_url, b.monitored, b.asin,
                      s.name AS series_name,
                      (SELECT GROUP_CONCAT(a.name, ', ')
                         FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                        WHERE ba.book_id = b.id ORDER BY ba.position) AS authors
                 FROM books b
                 LEFT JOIN series s ON s.id = b.series_id
                WHERE b.release_date IS NOT NULL
                  AND b.release_date >= ? AND b.release_date <= ?"""
    params: list[str | int] = [start.isoformat(), end.isoformat()]
    if monitored is not None:
        query += " AND b.monitored = ?"
        params.append(int(monitored))
    query += " ORDER BY b.release_date, b.title"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        CalendarBookOut(
            id=r["id"],
            title=r["title"],
            authors=r["authors"].split(", ") if r["authors"] else [],
            series=r["series_name"] or "",
            release_date=r["release_date"],
            cover_url=r["cover_url"],
            monitored=bool(r["monitored"]),
            asin=r["asin"],
        )
        for r in rows
    ]
