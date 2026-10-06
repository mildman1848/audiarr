"""Author and series follows API (issue #80).

A follow is a named author or series. A manual refresh asks the provider
chain for that author's/series' books and stores them as *candidates*:

- ``future``: release date after today -> a monitored book is created
  (Wanted), using the default quality profile / root folder.
- ``backlog``: already released (or undated) -> only listed for review; a
  book is created only when the user explicitly adds the candidate.
- ``excluded``: hidden by the user; refresh never touches this status.
- ``added``: a book was created from this candidate; refresh never resets it.

"Owned" (book already in the library) is derived at read time from
provider_ids / books.asin and is never stored. Nothing here searches or
downloads releases, and there is no scheduler: refresh is manual only.

Refresh reports an honest status: ``ok``, ``partial`` (a provider failed but
another answered, or the provider has more hits than the single 50-hit page
we fetch) or ``failed`` (provider error and nothing usable -> HTTP 502, never
a silent empty success). Authors are queried via Audible's ``author``
parameter; Audnexus has no author search, so it contributes nothing to author
follows.

If a book created from a candidate is later deleted, the candidate is
reconciled back to ``future``/``backlog`` (or re-linked when another book now
owns the same provider id) so it can be reviewed again.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.routes_metadata import ChainDep
from app.db import get_conn, migrate
from app.library import BookCreate, create_book
from app.providers.base import BookQuickInfo

log = logging.getLogger("audiarr.api.follows")

router = APIRouter()

KINDS = ("author", "series")
SEARCH_PAGE_SIZE = 50
MAX_NAME_LENGTH = 200


class FollowIn(BaseModel):
    kind: str
    name: str


class FollowOut(BaseModel):
    id: int
    kind: str
    name: str
    created_at: str
    last_refreshed_at: str | None
    last_result: dict[str, Any]
    candidate_counts: dict[str, int]


class CandidateOut(BaseModel):
    id: int
    provider: str
    provider_book_id: str
    title: str
    authors: list[str]
    series: str
    series_position: int | None
    release_date: str
    cover_url: str | None
    status: str
    owned: bool
    book_id: int | None


class CandidateIdsIn(BaseModel):
    ids: list[int] = Field(default_factory=list)


class CandidateActionOut(BaseModel):
    changed: list[int]
    skipped: list[dict[str, Any]]


class RefreshOut(BaseModel):
    found: int
    new: int
    future_created: int
    owned: int
    status: str = "ok"
    result: dict[str, Any]


def _ensure_schema() -> None:
    migrate()


def _begin_write(conn: sqlite3.Connection) -> None:
    """Take the write lock up front so check-then-insert sequences (candidate
    upsert, book creation) serialize across concurrent requests."""
    conn.execute("BEGIN IMMEDIATE")


def _authors_to_text(authors: list[str]) -> str:
    # JSON, because names may contain commas ("Martin Luther King, Jr.").
    return json.dumps(authors, ensure_ascii=False)


def _authors_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        return [str(a) for a in parsed if str(a)]
    return [a for a in text.split(", ") if a]  # legacy comma-joined rows


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _classify(release_date: str) -> str:
    """'future' only for a parseable date strictly after today; undated -> backlog."""
    try:
        released = date.fromisoformat(release_date[:10])
    except ValueError:
        return "backlog"
    return "future" if released.isoformat() > _today() else "backlog"


def _get_follow(conn: sqlite3.Connection, follow_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM follows WHERE id = ?", (follow_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Follow not found")
    return row


def _owned_book_id(conn: sqlite3.Connection, provider: str, provider_book_id: str) -> int | None:
    """Library book matching this provider id, ignoring locale (same
    convention as the importer's duplicate check)."""
    # Join books: provider_ids rows are not removed when a book is deleted,
    # and a stale row must not make a deleted book look owned.
    row = conn.execute(
        "SELECT p.entity_id FROM provider_ids p JOIN books b ON b.id = p.entity_id "
        "WHERE p.entity_type = 'book' AND p.provider = ? AND p.provider_id = ? LIMIT 1",
        (provider, provider_book_id),
    ).fetchone()
    if row is not None:
        return int(row["entity_id"])
    row = conn.execute("SELECT id FROM books WHERE asin = ? LIMIT 1", (provider_book_id,)).fetchone()
    return int(row["id"]) if row is not None else None


def _reconcile_added(conn: sqlite3.Connection, follow_id: int | None = None) -> None:
    """Repair 'added' candidates whose book is gone.

    Deleting a book NULLs ``book_id`` (ON DELETE SET NULL). Such a candidate
    would otherwise stay 'added' forever and never be selectable again. If
    another library book now owns the provider id it is re-linked (no
    duplicate); otherwise the candidate returns to its date classification.
    """
    sql = "SELECT * FROM follow_candidates WHERE status = 'added' AND book_id IS NULL"
    args: tuple[Any, ...] = ()
    if follow_id is not None:
        sql += " AND follow_id = ?"
        args = (follow_id,)
    for cand in conn.execute(sql, args).fetchall():
        owner = _owned_book_id(conn, cand["provider"], cand["provider_book_id"])
        if owner is not None:
            conn.execute("UPDATE follow_candidates SET book_id = ? WHERE id = ?", (owner, cand["id"]))
        else:
            conn.execute(
                "UPDATE follow_candidates SET status = ? WHERE id = ?",
                (_classify(cand["release_date"]), cand["id"]),
            )


def _follow_out(conn: sqlite3.Connection, row: sqlite3.Row) -> FollowOut:
    counts = {"future": 0, "backlog": 0, "excluded": 0, "added": 0}
    for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM follow_candidates WHERE follow_id = ? GROUP BY status",
        (row["id"],),
    ):
        counts[r["status"]] = int(r["n"])
    try:
        last_result = json.loads(row["last_result"]) if row["last_result"] else {}
    except ValueError:
        last_result = {}
    return FollowOut(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        created_at=row["created_at"],
        last_refreshed_at=row["last_refreshed_at"],
        last_result=last_result,
        candidate_counts=counts,
    )


def _candidate_out(conn: sqlite3.Connection, row: sqlite3.Row) -> CandidateOut:
    owned_id = _owned_book_id(conn, row["provider"], row["provider_book_id"])
    return CandidateOut(
        id=row["id"],
        provider=row["provider"],
        provider_book_id=row["provider_book_id"],
        title=row["title"],
        authors=_authors_from_text(row["authors"]),
        series=row["series"],
        series_position=row["series_position"],
        release_date=row["release_date"],
        cover_url=row["cover_url"],
        status=row["status"],
        owned=owned_id is not None,
        book_id=row["book_id"] or owned_id,
    )


def _create_candidate_book(conn: sqlite3.Connection, cand: sqlite3.Row, locale: str) -> int:
    """Create a monitored book from a candidate and mark it 'added'.

    Empty quality_profile / root_folder_id mean "inherit defaults", exactly
    like a book created through the library API without explicit choices.
    """
    book_id = create_book(
        conn,
        BookCreate(
            title=cand["title"] or cand["provider_book_id"],
            release_date=cand["release_date"] or None,
            cover_url=cand["cover_url"],
            authors=_authors_from_text(cand["authors"]),
            series=cand["series"],
            series_position=cand["series_position"] or None,
            provider=cand["provider"],
            provider_id=cand["provider_book_id"],
            locale=locale,
            monitored=True,
        ),
    )
    conn.execute(
        "UPDATE follow_candidates SET status = 'added', book_id = ? WHERE id = ?",
        (book_id, cand["id"]),
    )
    return book_id


def _materialize_future(conn: sqlite3.Connection, follow_id: int, locale: str) -> int:
    """Create monitored books for not-yet-owned 'future' candidates."""
    created = 0
    for cand in conn.execute(
        "SELECT * FROM follow_candidates WHERE follow_id = ? AND status = 'future'",
        (follow_id,),
    ).fetchall():
        if _owned_book_id(conn, cand["provider"], cand["provider_book_id"]) is not None:
            continue
        _create_candidate_book(conn, cand, locale)
        created += 1
    return created


def _matches(follow: sqlite3.Row, hit: BookQuickInfo) -> bool:
    """Providers match fuzzily; keep only exact (case-insensitive) hits."""
    wanted = follow["name"].casefold()
    if follow["kind"] == "author":
        return any(a.casefold() == wanted for a in hit.authors)
    return hit.series.casefold() == wanted


def _upsert_candidate(
    conn: sqlite3.Connection, follow_id: int, hit: BookQuickInfo, provider_book_id: str
) -> bool:
    """Insert/refresh one candidate; returns True when newly inserted.

    Existing rows only get their metadata (and, while still future/backlog,
    their date classification) refreshed -- excluded/added are never reset.
    """
    existing = conn.execute(
        "SELECT id, status FROM follow_candidates "
        "WHERE follow_id = ? AND provider = ? AND provider_book_id = ?",
        (follow_id, hit.provider_name, provider_book_id),
    ).fetchone()
    fields = (
        hit.title,
        _authors_to_text(hit.authors),
        hit.series,
        hit.series_position or None,
        hit.release_date,
        hit.cover_url,
    )
    if existing is None:
        cur = conn.execute(
            "INSERT OR IGNORE INTO follow_candidates (follow_id, provider, provider_book_id, "
            "title, authors, series, series_position, release_date, cover_url, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (follow_id, hit.provider_name, provider_book_id, *fields, _classify(hit.release_date)),
        )
        return cur.rowcount > 0
    conn.execute(
        "UPDATE follow_candidates SET title = ?, authors = ?, series = ?, series_position = ?, "
        "release_date = ?, cover_url = ? WHERE id = ?",
        (*fields, existing["id"]),
    )
    if existing["status"] in ("future", "backlog"):
        conn.execute(
            "UPDATE follow_candidates SET status = ? WHERE id = ?",
            (_classify(hit.release_date), existing["id"]),
        )
    return False


# -- follows ------------------------------------------------------------------


@router.get("/api/v1/follows", response_model=list[FollowOut])
async def list_follows() -> list[FollowOut]:
    _ensure_schema()
    with get_conn() as conn:
        _begin_write(conn)
        _reconcile_added(conn)
        rows = conn.execute("SELECT * FROM follows ORDER BY kind, name COLLATE NOCASE").fetchall()
        return [_follow_out(conn, r) for r in rows]


@router.post("/api/v1/follows", response_model=FollowOut, status_code=201)
async def create_follow(data: FollowIn) -> FollowOut:
    _ensure_schema()
    name = data.name.strip()
    if data.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    if not name:
        raise HTTPException(422, "name must not be empty")
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(422, f"name must be at most {MAX_NAME_LENGTH} characters")
    with get_conn() as conn:
        try:
            cur = conn.execute("INSERT INTO follows (kind, name) VALUES (?, ?)", (data.kind, name))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, f"Already following {data.kind} {name!r}") from exc
        return _follow_out(conn, _get_follow(conn, int(cur.lastrowid or 0)))


@router.delete("/api/v1/follows/{follow_id}", status_code=204)
async def delete_follow(follow_id: int) -> None:
    """Delete a follow and its candidates; books created from it are kept."""
    _ensure_schema()
    with get_conn() as conn:
        _get_follow(conn, follow_id)
        conn.execute("DELETE FROM follows WHERE id = ?", (follow_id,))


def _refresh_status(hits: list[BookQuickInfo], response: Any) -> tuple[str, dict[str, Any]]:
    """Classify a provider response as ok / partial / failed (see module doc)."""
    meta = response.provider_metadata
    errors: dict[str, str] = dict(meta.get("provider_errors") or {})
    extra: dict[str, Any] = {}
    if errors:
        extra["errors"] = errors
    total = meta.get("total_results")
    if isinstance(total, int) and total > len(response.results):
        extra["truncated"] = True
        extra["total_results"] = total
    if errors and not hits:
        return "failed", extra
    if errors or extra.get("truncated"):
        return "partial", extra
    return "ok", extra


@router.post("/api/v1/follows/{follow_id}/refresh", response_model=RefreshOut)
async def refresh_follow(follow_id: int, chain: ChainDep = None) -> RefreshOut:  # type: ignore[assignment]
    """Manual, idempotent refresh: upsert candidates, create future books only.

    A provider failure that leaves nothing usable is recorded on the follow
    and answered with 502 instead of an empty "successful" refresh.
    """
    _ensure_schema()
    with get_conn() as conn:
        follow = _get_follow(conn, follow_id)

    if follow["kind"] == "author":
        response = await chain.search("", author=follow["name"], page_size=SEARCH_PAGE_SIZE)
    else:
        response = await chain.search(follow["name"], page_size=SEARCH_PAGE_SIZE)
    hits = [h for h in response.results if _matches(follow, h)]
    status, extra = _refresh_status(hits, response)
    locale = chain.config.audible_locale

    if status == "failed":
        result = {"status": status, "found": 0, "new": 0, "future_created": 0, **extra}
        with get_conn() as conn:
            _begin_write(conn)
            _get_follow(conn, follow_id)
            # last_refreshed_at is left alone: it marks the last real refresh.
            conn.execute(
                "UPDATE follows SET last_result = ? WHERE id = ?", (json.dumps(result), follow_id)
            )
        log.warning("Follow %s (%s %r) refresh failed: %s", follow_id, follow["kind"], follow["name"], extra)
        raise HTTPException(502, f"Provider search failed: {'; '.join(extra['errors'].values())}")

    new = 0
    with get_conn() as conn:
        _begin_write(conn)
        _get_follow(conn, follow_id)  # may have been deleted while we waited on the provider
        _reconcile_added(conn, follow_id)
        for hit in hits:
            provider_book_id = hit.asin or hit.provider_uid
            if not provider_book_id:
                continue
            if _upsert_candidate(conn, follow_id, hit, provider_book_id):
                new += 1
        future_created = _materialize_future(conn, follow_id, locale)
        owned = sum(
            1
            for c in conn.execute(
                "SELECT provider, provider_book_id FROM follow_candidates WHERE follow_id = ?",
                (follow_id,),
            )
            if _owned_book_id(conn, c["provider"], c["provider_book_id"]) is not None
        )
        result = {
            "status": status,
            "found": len(hits),
            "new": new,
            "future_created": future_created,
            **extra,
        }
        conn.execute(
            "UPDATE follows SET last_refreshed_at = datetime('now'), last_result = ? WHERE id = ?",
            (json.dumps(result), follow_id),
        )
    log.info("Follow %s (%s %r) refreshed: %s", follow_id, follow["kind"], follow["name"], result)
    return RefreshOut(
        found=len(hits),
        new=new,
        future_created=future_created,
        owned=owned,
        status=status,
        result=result,
    )


# -- candidates ---------------------------------------------------------------


@router.get("/api/v1/follows/{follow_id}/candidates", response_model=list[CandidateOut])
async def list_candidates(follow_id: int) -> list[CandidateOut]:
    _ensure_schema()
    with get_conn() as conn:
        _begin_write(conn)
        _get_follow(conn, follow_id)
        _reconcile_added(conn, follow_id)
        rows = conn.execute(
            "SELECT * FROM follow_candidates WHERE follow_id = ? "
            "ORDER BY release_date DESC, title COLLATE NOCASE",
            (follow_id,),
        ).fetchall()
        return [_candidate_out(conn, r) for r in rows]


def _selected(conn: sqlite3.Connection, follow_id: int, ids: list[int]) -> list[tuple[int, Any]]:
    """(id, row-or-None) for each requested id, restricted to this follow."""
    out: list[tuple[int, Any]] = []
    for cid in dict.fromkeys(ids):
        row = conn.execute(
            "SELECT * FROM follow_candidates WHERE id = ? AND follow_id = ?", (cid, follow_id)
        ).fetchone()
        out.append((cid, row))
    return out


@router.post("/api/v1/follows/{follow_id}/candidates/exclude", response_model=CandidateActionOut)
async def exclude_candidates(follow_id: int, data: CandidateIdsIn) -> CandidateActionOut:
    """Hide future/backlog candidates; 'added' ones already have a book."""
    _ensure_schema()
    changed: list[int] = []
    skipped: list[dict[str, Any]] = []
    with get_conn() as conn:
        _begin_write(conn)
        _get_follow(conn, follow_id)
        _reconcile_added(conn, follow_id)
        for cid, row in _selected(conn, follow_id, data.ids):
            if row is None:
                skipped.append({"id": cid, "reason": "not_found"})
            elif row["status"] not in ("future", "backlog"):
                skipped.append({"id": cid, "reason": row["status"]})
            else:
                conn.execute("UPDATE follow_candidates SET status = 'excluded' WHERE id = ?", (cid,))
                changed.append(cid)
    return CandidateActionOut(changed=changed, skipped=skipped)


@router.post("/api/v1/follows/{follow_id}/candidates/restore", response_model=CandidateActionOut)
async def restore_candidates(
    follow_id: int, data: CandidateIdsIn, chain: ChainDep = None  # type: ignore[assignment]
) -> CandidateActionOut:
    """Un-exclude candidates, re-classifying by release date. A restored
    future candidate becomes a monitored book right away (future-only rule)."""
    _ensure_schema()
    changed: list[int] = []
    skipped: list[dict[str, Any]] = []
    with get_conn() as conn:
        _begin_write(conn)
        _get_follow(conn, follow_id)
        _reconcile_added(conn, follow_id)
        for cid, row in _selected(conn, follow_id, data.ids):
            if row is None:
                skipped.append({"id": cid, "reason": "not_found"})
            elif row["status"] != "excluded":
                skipped.append({"id": cid, "reason": row["status"]})
            else:
                conn.execute(
                    "UPDATE follow_candidates SET status = ? WHERE id = ?",
                    (_classify(row["release_date"]), cid),
                )
                changed.append(cid)
        if changed:
            _materialize_future(conn, follow_id, chain.config.audible_locale)
    return CandidateActionOut(changed=changed, skipped=skipped)


@router.post("/api/v1/follows/{follow_id}/candidates/add", response_model=CandidateActionOut)
async def add_candidates(
    follow_id: int, data: CandidateIdsIn, chain: ChainDep = None  # type: ignore[assignment]
) -> CandidateActionOut:
    """Explicitly add selected back-catalog candidates as monitored books.

    Only 'backlog' candidates qualify; excluded, already added and owned
    ones are skipped. Never triggers a search or download.
    """
    _ensure_schema()
    changed: list[int] = []
    skipped: list[dict[str, Any]] = []
    locale = chain.config.audible_locale
    with get_conn() as conn:
        _begin_write(conn)
        _get_follow(conn, follow_id)
        _reconcile_added(conn, follow_id)
        for cid, row in _selected(conn, follow_id, data.ids):
            if row is None:
                skipped.append({"id": cid, "reason": "not_found"})
            elif row["status"] != "backlog":
                skipped.append({"id": cid, "reason": row["status"]})
            elif _owned_book_id(conn, row["provider"], row["provider_book_id"]) is not None:
                skipped.append({"id": cid, "reason": "owned"})
            else:
                _create_candidate_book(conn, row, locale)
                changed.append(cid)
    return CandidateActionOut(changed=changed, skipped=skipped)
