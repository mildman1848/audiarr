"""Best-effort backfill of asin/release_date metadata for legacy books.

All prod books were imported before the metadata pipeline existed, so
``release_date`` (and now ``asin``) are NULL for every one of them and the
Calendar view has nothing to show. This module fills those two columns in
by looking a book up by title+author via the configured provider chain,
one small batch at a time so a single call (startup task or the manual
``POST /api/v1/metadata/backfill`` endpoint) never blocks for long.

Every lookup is best-effort: a provider miss, timeout, or malformed
response is logged at warning level and counted as a failure, never
raised -- this must not crash startup or the triggering request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.db import get_conn
from app.providers.chain import ProviderChain

log = logging.getLogger("audiarr.metadata.backfill")

DEFAULT_BATCH_SIZE = 10


@dataclass
class BackfillResult:
    updated: int = 0
    failed: int = 0
    remaining: int = 0


def _candidate_books(conn: Any, limit: int) -> list[dict[str, Any]]:
    """Books with both asin and release_date still NULL, oldest id first."""
    rows = conn.execute(
        """SELECT b.id, b.title,
                  (SELECT GROUP_CONCAT(a.name, ', ')
                     FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id ORDER BY ba.position) AS authors
             FROM books b
            WHERE b.asin IS NULL AND b.release_date IS NULL
            ORDER BY b.id
            LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _remaining_count(conn: Any) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM books WHERE asin IS NULL AND release_date IS NULL"
    ).fetchone()
    return int(row[0])


async def _lookup_one(
    chain: ProviderChain, title: str, authors: str
) -> tuple[str | None, str | None]:
    """Resolve (asin, release_date) for one book, or (None, None) on a miss.

    Search by title (+ first author, if any) via the provider chain, then
    resolve the release date from the same provider's get_detail() using
    the search hit's own asin -- BookQuickInfo (the search result shape)
    does not carry release_date, only BookDetailInfo does.
    """
    author = authors.split(", ")[0] if authors else ""
    response = await chain.search(title, author=author)
    if not response.results:
        return None, None

    hit = response.results[0]
    if not hit.asin:
        return None, None

    detail = await chain.get_detail(hit.provider_name, hit.asin)
    release_date = (detail.release_date or None) if detail is not None else None
    return hit.asin, release_date


async def run_backfill_batch(
    chain: ProviderChain, batch_size: int = DEFAULT_BATCH_SIZE
) -> BackfillResult:
    """Look up and persist metadata for one batch of candidate books."""
    result = BackfillResult()
    with get_conn() as conn:
        candidates = _candidate_books(conn, batch_size)

    for book in candidates:
        try:
            asin, release_date = await _lookup_one(chain, book["title"], book["authors"] or "")
        except Exception as exc:  # noqa: BLE001 -- provider failures must never crash the caller
            log.warning(
                "metadata backfill: lookup failed for book id=%s (%r): %s",
                book["id"],
                book["title"],
                exc,
            )
            result.failed += 1
            continue

        if not asin and not release_date:
            log.warning(
                "metadata backfill: no provider match for book id=%s (%r)",
                book["id"],
                book["title"],
            )
            result.failed += 1
            continue

        with get_conn() as conn:
            conn.execute(
                "UPDATE books SET asin = COALESCE(?, asin), "
                "release_date = COALESCE(?, release_date), updated_at = datetime('now') "
                "WHERE id = ?",
                (asin, release_date, book["id"]),
            )
        result.updated += 1

    with get_conn() as conn:
        result.remaining = _remaining_count(conn)
    return result
