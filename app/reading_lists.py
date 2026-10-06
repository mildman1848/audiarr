"""Reading-list parsing and Goodreads feed client (issue #79).

Pure, bounded, deterministic building blocks for the reading-list import
flow; nothing in here touches the database, the settings file or the
metadata providers (see app/reading_list_import.py for that).

Supported inputs
- Goodreads public shelf RSS (``/review/list_rss/<user id>?shelf=<name>``),
  fetched from the fixed host ``www.goodreads.com`` only.
- Goodreads user-exported library CSV (uploaded by the user).
- StoryGraph user-exported CSV (uploaded by the user, never fetched).

Safety rules enforced here
- The feed URL a caller supplies is only *validated and decomposed* into a
  user id + shelf name; the URL that is actually requested is rebuilt from
  those parts against a hard-coded host, so a caller can never steer the
  request to another host/path. Redirects are never followed.
- The ``key=`` token of private Goodreads feeds is a credential: such URLs
  are rejected, never stored or logged. Use the CSV export instead.
- XML is parsed with expat directly with DOCTYPE/entity declarations
  forbidden (no DTD, no entity expansion, no external entities).
- Response/upload sizes and parsed row counts are capped.
- Error messages are fixed strings; they never echo the URL, a row, an
  upstream body or an exception text.

StoryGraph: only the user-uploaded CSV is supported, via explicit header
aliases for the columns that were corroborated (Title, Authors, Read
Status, optional ISBN/UID). There is no session/cookie login and no remote
fetch. The Read Status values are passed through as free text and nothing
depends on them.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit
from xml.parsers import expat

import httpx

log = logging.getLogger("audiarr.reading_lists")

GOODREADS_FEED_HOST = "www.goodreads.com"
GOODREADS_HOSTS = frozenset({"www.goodreads.com", "goodreads.com"})
GOODREADS_FEED_PATH = re.compile(r"^/review/list_rss/([0-9]{1,20})$")
_USER_ID_RE = re.compile(r"[0-9]{1,20}")  # ASCII digits only (str.isdigit/\d accept other scripts)
GOODREADS_ALL_SHELVES = "#ALL#"
_SHELF_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

MAX_URL_LENGTH = 2048
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_PARSED_ROWS = 2000
MAX_FEED_PAGE = 50
FEED_PER_PAGE = 100
FEED_TIMEOUT_SECONDS = 10.0
MAX_FIELD_LENGTH = 500
USER_AGENT = "Audiarr/1.1 (+https://github.com/mildman1848/audiarr)"



class _FeedUrlLogFilter(logging.Filter):
    """Drop httpx/httpcore records that would print a Goodreads feed URL.

    httpx logs every request URL at INFO ("HTTP Request: GET <url> ..."),
    which would put the shelf feed URL into the application log. Our own
    logging (counts and source type only) is unaffected.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "/review/list_rss/" not in record.getMessage()


for _name in ("httpx", "httpcore"):
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, _FeedUrlLogFilter) for f in _logger.filters):
        _logger.addFilter(_FeedUrlLogFilter())

Source = Literal["goodreads", "storygraph"]
CsvFormat = Literal["auto", "goodreads", "storygraph"]


class ReadingListError(Exception):
    """A user-facing failure with a stable ``code`` and a safe ``message``.

    Never constructed from URLs, rows, upstream bodies or exception text.
    """

    status_hint = 422

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FeedUrlError(ReadingListError):
    status_hint = 422


class FeedFetchError(ReadingListError):
    status_hint = 502


class ParseError(ReadingListError):
    status_hint = 422


class InputTooLargeError(ReadingListError):
    status_hint = 413


# -- text/identifier normalization ---------------------------------------------

_ENTITY_RE = re.compile(r"&(#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_WS_RE = re.compile(r"\s+")
_SERIES_SUFFIX_RE = re.compile(r"\s*\([^()]*#\s*\d+[^()]*\)\s*$")


def _unescape_entities(text: str) -> str:
    """Decode semicolon-terminated HTML entities (at most two passes).

    Goodreads feeds/exports sometimes carry escaped text that survives
    XML/CSV decoding (``&#39;``) or is double-escaped (``&amp;#39;``), so a
    second pass is applied. Only ``;``-terminated entities are touched:
    ``html.unescape`` would also rewrite legacy unterminated ones
    (``"Fish&notes"`` -> ``"Fish¬es"``).
    """
    for _ in range(2):
        decoded = _ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), text)
        if decoded == text:
            break
        text = decoded
    return text


def clean_text(value: str | None, limit: int = MAX_FIELD_LENGTH) -> str:
    """Entity-decode, NFC-normalize, drop control chars, collapse whitespace."""
    if not value:
        return ""
    text = unicodedata.normalize("NFC", _unescape_entities(value))
    text = "".join(" " if unicodedata.category(ch) in {"Cc", "Zl", "Zp"} else ch for ch in text)
    return _WS_RE.sub(" ", text).strip()[:limit]


def split_authors(value: str) -> list[str]:
    """Split a ``"A, B; C"`` author field into a de-duplicated ordered list."""
    seen: dict[str, str] = {}
    for part in re.split(r"[,;]", value):
        name = clean_text(part, 200)
        if name and name.casefold() not in seen:
            seen[name.casefold()] = name
    return list(seen.values())


def _isbn10_ok(s: str) -> bool:
    if not re.fullmatch(r"\d{9}[\dX]", s):
        return False
    total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
    return total % 11 == 0


def _isbn13_ok(s: str) -> bool:
    if not re.fullmatch(r"\d{13}", s):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s[:12]))
    return (10 - total % 10) % 10 == int(s[12])


def normalize_isbn(raw: str | None) -> str:
    """Return a checksum-valid ISBN-10/13 or ``""``.

    Handles Goodreads' Excel-style ``="0441172717"`` wrapper, hyphens and
    spaces. Anything that is not a valid ISBN (e.g. a StoryGraph UID that is
    not an ISBN) is dropped rather than guessed at.
    """
    if not raw:
        return ""
    s = re.sub(r"[=\"'\s-]", "", raw).upper()
    if _isbn13_ok(s) or _isbn10_ok(s):
        return s
    return ""


def isbn10_to_isbn13(isbn10: str) -> str:
    core = "978" + isbn10[:9]
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
    return core + str((10 - total % 10) % 10)


def isbn_variants(*isbns: str) -> set[str]:
    """All comparable forms (10 and 13) of the given normalized ISBNs."""
    out: set[str] = set()
    for isbn in isbns:
        if not isbn:
            continue
        out.add(isbn)
        if len(isbn) == 10:
            out.add(isbn10_to_isbn13(isbn))
    return out


# -- parsed entry --------------------------------------------------------------


@dataclass
class ReadingListEntry:
    """One normalized book row from a reading-list source.

    ``entry_key`` is stable for the same book across re-uploads/re-fetches
    (Goodreads book id when present, else a hash of the normalized
    identity), so a UI can refer to an entry between preview and import.
    """

    entry_key: str
    source: Source
    source_book_id: str
    title: str
    search_title: str
    authors: list[str] = field(default_factory=list)
    isbn: str = ""
    isbn13: str = ""
    shelves: list[str] = field(default_factory=list)
    exclusive_shelf: str = ""


@dataclass
class ParseResult:
    entries: list[ReadingListEntry] = field(default_factory=list)
    format: str = ""
    total_rows: int = 0
    skipped_rows: int = 0  # rows without a usable title
    duplicate_rows: int = 0  # rows repeating an earlier entry_key
    truncated: bool = False  # MAX_PARSED_ROWS reached; later rows ignored


def _search_title(title: str) -> str:
    """Drop a trailing Goodreads ``"(Series, #3)"`` suffix for provider search."""
    stripped = _SERIES_SUFFIX_RE.sub("", title).strip()
    return stripped or title


def _make_entry(
    *,
    source: Source,
    book_id: str,
    title: str,
    authors: list[str],
    isbn: str,
    isbn13: str,
    shelves: list[str],
    exclusive_shelf: str,
) -> ReadingListEntry:
    if book_id.isdigit() and source == "goodreads":
        key = f"gr:{book_id}"
    else:
        ident = "|".join(
            [title.casefold(), (authors[0].casefold() if authors else ""), isbn13 or isbn]
        )
        key = "h:" + hashlib.sha1(ident.encode("utf-8")).hexdigest()[:16]  # noqa: S324 -- not security
    return ReadingListEntry(
        entry_key=key,
        source=source,
        source_book_id=book_id if book_id.isdigit() else "",
        title=title,
        search_title=_search_title(title),
        authors=authors,
        isbn=isbn,
        isbn13=isbn13,
        shelves=shelves,
        exclusive_shelf=exclusive_shelf,
    )


def _add_entry(result: ParseResult, seen: set[str], entry: ReadingListEntry) -> None:
    if entry.entry_key in seen:
        result.duplicate_rows += 1
        return
    seen.add(entry.entry_key)
    result.entries.append(entry)


# -- Goodreads feed URL --------------------------------------------------------


@dataclass(frozen=True)
class GoodreadsFeedRef:
    """The only parts of a feed URL Audiarr keeps: user id + shelf."""

    user_id: str
    shelf: str


def validate_goodreads_feed_url(url: str) -> GoodreadsFeedRef:
    """Validate a public Goodreads shelf RSS URL and decompose it.

    Accepts ``https://[www.]goodreads.com/review/list_rss/<digits>?shelf=<s>``
    and nothing else: no HTTP, no userinfo, no port, no fragment, no other
    host/path, and no query parameter besides ``shelf`` (a private-feed
    ``key=`` gets a dedicated message because it is the common mistake).
    """
    if not isinstance(url, str) or not url.strip():
        raise FeedUrlError("feed_url_missing", "A Goodreads feed URL is required.")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise FeedUrlError("feed_url_invalid", "The feed URL is not a valid Goodreads shelf RSS URL.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FeedUrlError(
            "feed_url_invalid", "The feed URL is not a valid Goodreads shelf RSS URL."
        ) from None

    if parts.scheme != "https":
        raise FeedUrlError("feed_url_not_https", "The feed URL must use https.")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise FeedUrlError("feed_url_userinfo", "The feed URL must not contain credentials.")
    if port is not None or parts.hostname is None or parts.netloc.lower() != parts.hostname:
        raise FeedUrlError("feed_url_port", "The feed URL must not specify a port.")
    if parts.hostname not in GOODREADS_HOSTS:
        raise FeedUrlError("feed_url_host", "Only goodreads.com feed URLs are supported.")
    if "#" in url or parts.fragment:
        raise FeedUrlError("feed_url_fragment", "The feed URL must not contain a fragment.")
    match = GOODREADS_FEED_PATH.match(parts.path)
    if match is None:
        raise FeedUrlError(
            "feed_url_path", "Expected a Goodreads shelf RSS URL (/review/list_rss/<user id>)."
        )

    try:
        params = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=10)
    except ValueError:
        raise FeedUrlError(
            "feed_url_invalid", "The feed URL is not a valid Goodreads shelf RSS URL."
        ) from None
    names = [name for name, _ in params]
    if "key" in names:
        raise FeedUrlError(
            "feed_url_private_key",
            "Private feed URLs (with a key) are not supported; make the shelf public "
            "or upload the CSV export instead.",
        )
    if len(set(names)) != len(names) or any(name != "shelf" for name in names):
        raise FeedUrlError("feed_url_query", "The feed URL contains unsupported query parameters.")
    if not params:
        raise FeedUrlError("feed_url_shelf", "The feed URL must include a shelf (shelf=<name>).")
    return GoodreadsFeedRef(user_id=match.group(1), shelf=normalize_shelf(params[0][1]))


def normalize_shelf(shelf: str) -> str:
    value = (shelf or "").strip()
    if value == GOODREADS_ALL_SHELVES:
        return value
    value = value.lower()
    if not _SHELF_RE.match(value):
        raise FeedUrlError("feed_url_shelf", "The shelf name is not valid.")
    return value


def build_goodreads_feed_url(ref: GoodreadsFeedRef, page: int = 1) -> str:
    """Rebuild the canonical fetch URL from validated parts (fixed host)."""
    if not _USER_ID_RE.fullmatch(ref.user_id):
        raise FeedUrlError("feed_url_invalid", "The stored feed reference is not valid.")
    shelf = normalize_shelf(ref.shelf)
    if not 1 <= page <= MAX_FEED_PAGE:
        raise FeedUrlError("feed_page_invalid", "The feed page is out of range.")
    query = urlencode({"shelf": shelf, "page": page, "per_page": FEED_PER_PAGE})
    return f"https://{GOODREADS_FEED_HOST}/review/list_rss/{ref.user_id}?{query}"


async def fetch_goodreads_feed(
    ref: GoodreadsFeedRef, page: int = 1, *, client: httpx.AsyncClient | None = None
) -> bytes:
    """GET one page of a public shelf feed: no redirects, bounded time/bytes.

    ``client`` is injectable for tests (``httpx.MockTransport``); an injected
    client is never closed here. Failures raise :class:`FeedFetchError`
    with a fixed message -- the URL and httpx's exception text are never
    surfaced or logged.
    """
    url = build_goodreads_feed_url(ref, page)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=FEED_TIMEOUT_SECONDS, follow_redirects=False)
    try:
        async with http.stream(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"},
            timeout=FEED_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as response:
            if 300 <= response.status_code < 400:
                log.warning("Goodreads feed fetch: redirect refused (HTTP %s)", response.status_code)
                raise FeedFetchError(
                    "feed_redirect", "Goodreads redirected the request; the shelf may be private."
                )
            if response.status_code != 200:
                log.warning("Goodreads feed fetch: HTTP %s", response.status_code)
                raise FeedFetchError(
                    "feed_http_error", f"Goodreads returned HTTP {response.status_code}."
                )
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_FEED_BYTES:
                raise FeedFetchError("feed_too_large", "The Goodreads feed response is too large.")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_FEED_BYTES:
                    raise FeedFetchError("feed_too_large", "The Goodreads feed response is too large.")
            return bytes(body)
    except httpx.HTTPError as exc:
        # Only the exception class is logged: httpx messages can contain the URL.
        log.warning("Goodreads feed fetch failed (%s)", type(exc).__name__)
        raise FeedFetchError("feed_unreachable", "Could not reach Goodreads.") from None
    finally:
        if owns_client:
            await http.aclose()


# -- Goodreads RSS parsing -----------------------------------------------------


class _ForbiddenXml(Exception):
    pass


def _local(name: str) -> str:
    return name.rsplit(":", 1)[-1].lower()


def parse_goodreads_rss(data: bytes) -> ParseResult:
    """Parse a Goodreads shelf RSS document into entries.

    DOCTYPE and entity declarations are rejected (expat handlers raise), so
    no DTD is read and no entity can expand. At most MAX_PARSED_ROWS items
    are considered; ``truncated`` reports when more were present.
    """
    if len(data) > MAX_FEED_BYTES:
        raise InputTooLargeError("feed_too_large", "The feed document is too large.")

    items: list[dict[str, str]] = []
    state = {"depth": 0, "item_depth": 0, "field": "", "root": "", "truncated": False}
    current: dict[str, str] = {}
    buffer: list[str] = []

    def forbid(*_args: object) -> None:
        raise _ForbiddenXml

    def start(name: str, _attrs: object) -> None:
        state["depth"] += 1
        local = _local(name)
        if state["depth"] == 1:
            state["root"] = local
        elif local == "item" and not state["item_depth"]:
            state["item_depth"] = state["depth"]
            current.clear()
        elif state["item_depth"] and state["depth"] == state["item_depth"] + 1:
            state["field"] = local
            buffer.clear()

    def end(name: str) -> None:
        local = _local(name)
        if state["item_depth"] and state["depth"] == state["item_depth"] + 1 and state["field"] == local:
            current.setdefault(local, "".join(buffer))
            state["field"] = ""
        elif state["item_depth"] and state["depth"] == state["item_depth"]:
            if len(items) < MAX_PARSED_ROWS:
                items.append(dict(current))
            else:
                state["truncated"] = True
            state["item_depth"] = 0
        state["depth"] -= 1

    def chars(text: str) -> None:
        if state["field"]:
            buffer.append(text)

    parser = expat.ParserCreate()
    parser.buffer_text = True
    parser.StartDoctypeDeclHandler = forbid
    parser.EntityDeclHandler = forbid
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except _ForbiddenXml:
        raise ParseError("xml_forbidden", "DTD/entity declarations are not allowed in the feed.") from None
    except expat.ExpatError:
        raise ParseError("xml_malformed", "The feed is not well-formed XML.") from None
    if state["root"] != "rss":
        raise ParseError("feed_not_rss", "The document is not an RSS feed.")

    result = ParseResult(format="goodreads_rss", truncated=bool(state["truncated"]))
    seen: set[str] = set()
    for item in items:
        result.total_rows += 1
        title = clean_text(item.get("title"))
        if not title:
            result.skipped_rows += 1
            continue
        book_id = clean_text(item.get("book_id"), 20)
        author = clean_text(item.get("author_name"), 200)
        shelves = [s for s in (clean_text(p, 64) for p in (item.get("user_shelves") or "").split(",")) if s]
        _add_entry(
            result,
            seen,
            _make_entry(
                source="goodreads",
                book_id=book_id,
                title=title,
                authors=[author] if author else [],
                isbn=normalize_isbn(item.get("isbn")),
                isbn13=normalize_isbn(item.get("isbn13")),
                shelves=shelves,
                exclusive_shelf="",
            ),
        )
    return result


# -- CSV parsing ---------------------------------------------------------------

_GOODREADS_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "author": ("author", "author(s)", "authors"),
    "additional_authors": ("additional authors",),
    "isbn": ("isbn",),
    "isbn13": ("isbn13",),
    "book_id": ("book id", "goodreads book id", "book_id"),
    "bookshelves": ("bookshelves",),
    "exclusive_shelf": ("exclusive shelf",),
}
_STORYGRAPH_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "author": ("authors",),
    "isbn": ("isbn/uid", "isbn"),
    "status": ("read status",),
}
_GOODREADS_MARKERS = {"book id", "goodreads book id", "exclusive shelf", "bookshelves", "isbn13"}


def _norm_header(header: str) -> str:
    return _WS_RE.sub(" ", header.replace("﻿", "").strip().lower())


def _column_map(headers: list[str], aliases: dict[str, tuple[str, ...]]) -> dict[str, int]:
    normalized = [_norm_header(h) for h in headers]
    mapping: dict[str, int] = {}
    for key, names in aliases.items():
        for name in names:
            if name in normalized:
                mapping[key] = normalized.index(name)
                break
    return mapping


def detect_csv_format(headers: list[str]) -> str:
    normalized = {_norm_header(h) for h in headers}
    if "title" not in normalized:
        return ""
    if normalized & _GOODREADS_MARKERS:
        return "goodreads"
    if "authors" in normalized and "read status" in normalized:
        return "storygraph"
    if "author" in normalized or "author(s)" in normalized:
        return "goodreads"
    return ""


def parse_reading_list_csv(data: bytes, fmt: CsvFormat = "auto") -> ParseResult:
    """Parse an uploaded Goodreads or StoryGraph CSV export.

    UTF-8 only (a BOM is accepted); non-ASCII text is preserved. The upload
    is never stored. ``fmt="auto"`` detects the export from its headers; an
    unrecognised header row is rejected rather than guessed at.
    """
    if len(data) > MAX_CSV_BYTES:
        raise InputTooLargeError("csv_too_large", "The CSV file is too large.")
    if b"\x00" in data:
        raise ParseError("csv_invalid", "The file is not a text CSV.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseError("csv_encoding", "The CSV must be UTF-8 encoded.") from None

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        headers = next(reader, None)
        if not headers:
            raise ParseError("csv_empty", "The CSV file is empty.")
        chosen = detect_csv_format(headers) if fmt == "auto" else fmt
        aliases = _GOODREADS_ALIASES if chosen == "goodreads" else _STORYGRAPH_ALIASES
        cols = _column_map(headers, aliases)
        if chosen not in {"goodreads", "storygraph"} or "title" not in cols or "author" not in cols:
            raise ParseError(
                "csv_format_unrecognized",
                "The CSV header is not a recognized Goodreads or StoryGraph export.",
            )

        result = ParseResult(format=f"{chosen}_csv")
        seen: set[str] = set()
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            if result.total_rows >= MAX_PARSED_ROWS:
                result.truncated = True
                break
            result.total_rows += 1

            def cell(key: str, _row: list[str] = row) -> str:
                idx = cols.get(key)
                return _row[idx] if idx is not None and idx < len(_row) else ""

            title = clean_text(cell("title"))
            if not title:
                result.skipped_rows += 1
                continue
            if chosen == "goodreads":
                authors = split_authors(
                    ", ".join(p for p in (cell("author"), cell("additional_authors")) if p.strip())
                )
                shelves = [s for s in (clean_text(p, 64) for p in cell("bookshelves").split(",")) if s]
                exclusive = clean_text(cell("exclusive_shelf"), 64)
                if exclusive and exclusive not in shelves:
                    shelves.append(exclusive)
                isbn, isbn13 = normalize_isbn(cell("isbn")), normalize_isbn(cell("isbn13"))
                book_id = clean_text(cell("book_id"), 20)
                source: Source = "goodreads"
            else:
                authors = split_authors(cell("author"))
                status = clean_text(cell("status"), 64).lower()
                shelves = [status] if status else []
                exclusive = status
                isbn, isbn13 = normalize_isbn(cell("isbn")), ""
                book_id = ""
                source = "storygraph"
            if len(isbn) == 13 and not isbn13:
                isbn, isbn13 = "", isbn
            _add_entry(
                result,
                seen,
                _make_entry(
                    source=source,
                    book_id=book_id,
                    title=title,
                    authors=authors,
                    isbn=isbn,
                    isbn13=isbn13,
                    shelves=shelves,
                    exclusive_shelf=exclusive,
                ),
            )
        return result
    except csv.Error:
        raise ParseError("csv_invalid", "The CSV file could not be parsed.") from None
