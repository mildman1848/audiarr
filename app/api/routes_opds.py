"""OPDS (Open Publication Distribution System) export feed (issue #65).

Read-only Atom/OPDS 1.2 catalog feeds so e-reader / audiobook client apps
(KOReader, Chunky, ...) can browse and download books from the Audiarr
library without a bespoke integration. Stdlib ``xml.etree.ElementTree``
only -- no new dependency; element ``.text`` assignment escapes entities
automatically, so no manual XML escaping is needed either.

These routes are intentionally NOT added to ``app.auth.EXEMPT_PATHS``: they
go through the same AuthMiddleware (X-Api-Key header or session cookie) as
every other route, so "protect OPDS like everything else" falls out of not
touching that list rather than from any code here.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement, register_namespace, tostring

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.db import get_conn, migrate

router = APIRouter()

ATOM_NS = "http://www.w3.org/2005/Atom"
OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"
OPDS_ACQUISITION_REL = "http://opds-spec.org/acquisition"
OPDS_MIME = "application/atom+xml;profile=opds-catalog;kind=%s"

# Acquisition file preference, most-preferred first; anything else falls
# back to the first file by path (see _resolve_primary_file).
_FORMAT_PRIORITY = ["m4b", "m4a", "mp3", "flac", "opus", "ogg", "aac"]

_MIME_BY_EXT = {
    "m4b": "audio/x-m4b",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
    "aac": "audio/aac",
}

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

register_namespace("", ATOM_NS)


def _ensure_schema() -> None:
    """Same defensive migrate() every other router does for bare TestClient calls."""
    migrate()


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def _mime_for_path(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


def _split_names(value: Any) -> list[str]:
    return value.split(", ") if value else []


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_iso(value: str | None) -> str:
    """SQLite ``datetime('now')`` values are "YYYY-MM-DD HH:MM:SS" (UTC, no
    offset); reshape into a valid Atom/RFC-3339 timestamp."""
    if not value:
        return _now_iso()
    return value.replace(" ", "T") + "Z"


# -- path safety: only ever serve files that exist and live under a
# configured root folder, even though book_id/author_id/etc. are already
# int path params resolved through the DB (no user-supplied path ever
# reaches the filesystem) -- defense in depth against a stray/bogus
# library_files.path row. --------------------------------------------------


def _root_folder_paths(conn: Any) -> list[Path]:
    rows = conn.execute("SELECT path FROM root_folders").fetchall()
    paths = []
    for r in rows:
        try:
            paths.append(Path(r["path"]).resolve())
        except OSError:
            continue
    return paths


def _is_within_roots(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            if os.path.commonpath([str(root), str(path)]) == str(root):
                return True
        except ValueError:
            continue
    return False


def _resolve_primary_file(conn: Any, book_id: int) -> dict[str, Any] | None:
    """Pick the acquisition file for a book (see _FORMAT_PRIORITY), skipping
    files that no longer exist on disk or that fall outside every
    configured root folder. Returns None if nothing servable remains."""
    rows = conn.execute(
        """SELECT lf.id, lf.path, lf.format
             FROM library_files lf
             JOIN editions e ON e.id = lf.edition_id
            WHERE e.book_id = ?
            ORDER BY lf.path""",
        (book_id,),
    ).fetchall()
    if not rows:
        return None

    roots = _root_folder_paths(conn)
    candidates: list[dict[str, Any]] = []
    for r in rows:
        try:
            resolved = Path(r["path"]).resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if roots and not _is_within_roots(resolved, roots):
            continue
        candidates.append(dict(r))
    if not candidates:
        return None

    def sort_key(row: dict[str, Any]) -> tuple[int, str]:
        ext = Path(row["path"]).suffix.lower().lstrip(".")
        rank = _FORMAT_PRIORITY.index(ext) if ext in _FORMAT_PRIORITY else len(_FORMAT_PRIORITY)
        return (rank, row["path"])

    candidates.sort(key=sort_key)
    return candidates[0]


# -- XML building --------------------------------------------------------------

_BOOK_COLUMNS = """b.id, b.title, b.description, b.created_at, b.updated_at,
                  (SELECT GROUP_CONCAT(a.name, ', ')
                     FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id ORDER BY ba.position) AS authors,
                  (SELECT GROUP_CONCAT(n.name, ', ')
                     FROM book_narrators bn JOIN narrators n ON n.id = bn.narrator_id
                    WHERE bn.book_id = b.id ORDER BY bn.position) AS narrators"""


def _sub_text(parent: Element, tag: str, text: str) -> Element:
    el = SubElement(parent, f"{{{ATOM_NS}}}{tag}")
    el.text = text
    return el


def _add_link(parent: Element, rel: str, href: str, type_: str) -> Element:
    return SubElement(parent, f"{{{ATOM_NS}}}link", {"rel": rel, "href": href, "type": type_})


def _xml_response(root: Element, media_type: str = "application/atom+xml;charset=utf-8") -> Response:
    body = tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(content=body, media_type=media_type)


def _feed(request: Request, feed_id: str, title: str, kind: str) -> Element:
    feed = Element(f"{{{ATOM_NS}}}feed")
    _sub_text(feed, "id", feed_id)
    _sub_text(feed, "title", title)
    _sub_text(feed, "updated", _now_iso())
    _add_link(feed, "self", str(request.url), OPDS_MIME % kind)
    return feed


def _nav_entry(entry_id: str, title: str, summary: str, href: str, type_: str) -> Element:
    entry = Element(f"{{{ATOM_NS}}}entry")
    _sub_text(entry, "title", title)
    _sub_text(entry, "id", entry_id)
    _sub_text(entry, "updated", _now_iso())
    content = SubElement(entry, f"{{{ATOM_NS}}}content", {"type": "text"})
    content.text = summary
    _add_link(entry, "subsection", href, type_)
    return entry


def _book_acquisition_entry(conn: Any, request: Request, book: dict[str, Any]) -> Element:
    base = str(request.base_url).rstrip("/")
    entry = Element(f"{{{ATOM_NS}}}entry")
    _sub_text(entry, "title", book["title"])
    _sub_text(entry, "id", f"urn:audiarr:book:{book['id']}")
    _sub_text(entry, "updated", _to_iso(book.get("updated_at") or book.get("created_at")))

    for name in _split_names(book.get("authors")) or ["Unknown"]:
        author_el = SubElement(entry, f"{{{ATOM_NS}}}author")
        _sub_text(author_el, "name", name)
    for name in _split_names(book.get("narrators")):
        contributor_el = SubElement(entry, f"{{{ATOM_NS}}}contributor")
        _sub_text(contributor_el, "name", name)

    if book.get("description"):
        content_el = SubElement(entry, f"{{{ATOM_NS}}}content", {"type": "text"})
        content_el.text = book["description"]

    primary = _resolve_primary_file(conn, book["id"])
    if primary is not None:
        _add_link(
            entry,
            OPDS_ACQUISITION_REL,
            f"{base}/opds/books/{book['id']}/download",
            _mime_for_path(primary["path"]),
        )
    return entry


def _maybe_add_next_link(feed: Element, request: Request, row_count: int, limit: int, offset: int) -> None:
    """Simple "more may exist" pagination: a next link appears whenever a
    full page came back, without a separate COUNT(*) query."""
    if row_count == limit:
        next_url = request.url.include_query_params(limit=limit, offset=offset + limit)
        _add_link(feed, "next", str(next_url), OPDS_MIME % "acquisition")


# -- routes -----------------------------------------------------------------


@router.get("/opds")
async def opds_root(request: Request) -> Response:
    _ensure_schema()
    base = str(request.base_url).rstrip("/")
    feed = _feed(request, "urn:audiarr:opds:root", "Audiarr", "navigation")
    _add_link(feed, "start", f"{base}/opds", OPDS_MIME % "navigation")
    _add_link(feed, "search", f"{base}/opds/search.xml", "application/opensearchdescription+xml")

    feed.append(
        _nav_entry(
            "urn:audiarr:opds:new",
            "New",
            "Recently added audiobooks",
            f"{base}/opds/new",
            OPDS_MIME % "acquisition",
        )
    )
    feed.append(
        _nav_entry(
            "urn:audiarr:opds:authors",
            "Authors",
            "Browse the library by author",
            f"{base}/opds/authors",
            OPDS_MIME % "navigation",
        )
    )
    feed.append(
        _nav_entry(
            "urn:audiarr:opds:narrators",
            "Narrators",
            "Browse the library by narrator",
            f"{base}/opds/narrators",
            OPDS_MIME % "navigation",
        )
    )
    return _xml_response(feed)


@router.get("/opds/new")
async def opds_new(request: Request, limit: int = DEFAULT_LIMIT, offset: int = 0) -> Response:
    _ensure_schema()
    limit = _clamp_limit(limit)
    offset = max(0, offset)
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT {_BOOK_COLUMNS}
                 FROM books b
                ORDER BY b.created_at DESC, b.id DESC
                LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        feed = _feed(request, "urn:audiarr:opds:new", "Recently Added", "acquisition")
        for r in rows:
            feed.append(_book_acquisition_entry(conn, request, dict(r)))
    _maybe_add_next_link(feed, request, len(rows), limit, offset)
    return _xml_response(feed)


@router.get("/opds/authors")
async def opds_authors(request: Request) -> Response:
    _ensure_schema()
    base = str(request.base_url).rstrip("/")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT a.id, a.name, COUNT(ba.book_id) AS book_count
                 FROM authors a
                 LEFT JOIN book_authors ba ON ba.author_id = a.id
                GROUP BY a.id
                ORDER BY a.name COLLATE NOCASE"""
        ).fetchall()
    feed = _feed(request, "urn:audiarr:opds:authors", "Authors", "navigation")
    for r in rows:
        feed.append(
            _nav_entry(
                f"urn:audiarr:opds:author:{r['id']}",
                r["name"],
                f"{r['book_count']} book(s)",
                f"{base}/opds/authors/{r['id']}",
                OPDS_MIME % "acquisition",
            )
        )
    return _xml_response(feed)


@router.get("/opds/authors/{author_id}")
async def opds_author_books(
    request: Request, author_id: int, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> Response:
    _ensure_schema()
    limit = _clamp_limit(limit)
    offset = max(0, offset)
    with get_conn() as conn:
        author = conn.execute("SELECT id, name FROM authors WHERE id = ?", (author_id,)).fetchone()
        if author is None:
            raise HTTPException(404, "Author not found")
        rows = conn.execute(
            f"""SELECT {_BOOK_COLUMNS}
                 FROM books b
                 JOIN book_authors ba ON ba.book_id = b.id
                WHERE ba.author_id = ?
                ORDER BY b.title COLLATE NOCASE
                LIMIT ? OFFSET ?""",
            (author_id, limit, offset),
        ).fetchall()
        feed = _feed(request, f"urn:audiarr:opds:author:{author_id}", author["name"], "acquisition")
        for r in rows:
            feed.append(_book_acquisition_entry(conn, request, dict(r)))
    _maybe_add_next_link(feed, request, len(rows), limit, offset)
    return _xml_response(feed)


@router.get("/opds/narrators")
async def opds_narrators(request: Request) -> Response:
    _ensure_schema()
    base = str(request.base_url).rstrip("/")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT n.id, n.name, COUNT(bn.book_id) AS book_count
                 FROM narrators n
                 LEFT JOIN book_narrators bn ON bn.narrator_id = n.id
                GROUP BY n.id
                ORDER BY n.name COLLATE NOCASE"""
        ).fetchall()
    feed = _feed(request, "urn:audiarr:opds:narrators", "Narrators", "navigation")
    for r in rows:
        feed.append(
            _nav_entry(
                f"urn:audiarr:opds:narrator:{r['id']}",
                r["name"],
                f"{r['book_count']} book(s)",
                f"{base}/opds/narrators/{r['id']}",
                OPDS_MIME % "acquisition",
            )
        )
    return _xml_response(feed)


@router.get("/opds/narrators/{narrator_id}")
async def opds_narrator_books(
    request: Request, narrator_id: int, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> Response:
    _ensure_schema()
    limit = _clamp_limit(limit)
    offset = max(0, offset)
    with get_conn() as conn:
        narrator = conn.execute(
            "SELECT id, name FROM narrators WHERE id = ?", (narrator_id,)
        ).fetchone()
        if narrator is None:
            raise HTTPException(404, "Narrator not found")
        rows = conn.execute(
            f"""SELECT {_BOOK_COLUMNS}
                 FROM books b
                 JOIN book_narrators bn ON bn.book_id = b.id
                WHERE bn.narrator_id = ?
                ORDER BY b.title COLLATE NOCASE
                LIMIT ? OFFSET ?""",
            (narrator_id, limit, offset),
        ).fetchall()
        feed = _feed(
            request, f"urn:audiarr:opds:narrator:{narrator_id}", narrator["name"], "acquisition"
        )
        for r in rows:
            feed.append(_book_acquisition_entry(conn, request, dict(r)))
    _maybe_add_next_link(feed, request, len(rows), limit, offset)
    return _xml_response(feed)


@router.get("/opds/search.xml")
async def opds_search_description(request: Request) -> Response:
    """OpenSearch description document advertised by the root feed's
    rel="search" link, so OPDS clients can offer a search box."""
    base = str(request.base_url).rstrip("/")
    root = Element("OpenSearchDescription", {"xmlns": OPENSEARCH_NS})
    for tag, text in (
        ("ShortName", "Audiarr"),
        ("Description", "Search the Audiarr audiobook library"),
        ("InputEncoding", "UTF-8"),
    ):
        el = SubElement(root, tag)
        el.text = text
    SubElement(
        root,
        "Url",
        {"type": OPDS_MIME % "acquisition", "template": f"{base}/opds/search?query={{searchTerms}}"},
    )
    body = tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(content=body, media_type="application/opensearchdescription+xml")


@router.get("/opds/search")
async def opds_search(
    request: Request, query: str = "", limit: int = DEFAULT_LIMIT, offset: int = 0
) -> Response:
    _ensure_schema()
    limit = _clamp_limit(limit)
    offset = max(0, offset)
    like = f"%{query}%"
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT {_BOOK_COLUMNS}
                 FROM books b
                 LEFT JOIN book_authors ba ON ba.book_id = b.id
                 LEFT JOIN authors a1 ON a1.id = ba.author_id
                 LEFT JOIN book_narrators bn ON bn.book_id = b.id
                 LEFT JOIN narrators n1 ON n1.id = bn.narrator_id
                WHERE b.title LIKE ? OR a1.name LIKE ? OR n1.name LIKE ?
                ORDER BY b.title COLLATE NOCASE
                LIMIT ? OFFSET ?""",
            (like, like, like, limit, offset),
        ).fetchall()
        feed = _feed(request, "urn:audiarr:opds:search", f"Search: {query}", "acquisition")
        for r in rows:
            feed.append(_book_acquisition_entry(conn, request, dict(r)))
    _maybe_add_next_link(feed, request, len(rows), limit, offset)
    return _xml_response(feed)


@router.get("/opds/books/{book_id}/download")
async def opds_download(book_id: int) -> FileResponse:
    """Stream a book's primary acquisition file.

    ``book_id`` is the only client input and is resolved purely through the
    DB (see _resolve_primary_file) -- no path ever comes from the request.
    Starlette's FileResponse handles Range/If-Range/ETag itself, so partial
    downloads/resume work in this Starlette version without extra code here.
    """
    _ensure_schema()
    with get_conn() as conn:
        book = conn.execute("SELECT id FROM books WHERE id = ?", (book_id,)).fetchone()
        if book is None:
            raise HTTPException(404, "Book not found")
        primary = _resolve_primary_file(conn, book_id)
    if primary is None:
        raise HTTPException(404, "No downloadable file for this book")

    path = Path(primary["path"])
    return FileResponse(path, media_type=_mime_for_path(primary["path"]), filename=path.name)
