"""Reading-list import: preview, provider matching and selected import (#79).

Flow (see app/reading_lists.py for the parsers/feed client):

1. *Preview* (read-only): entries parsed from a Goodreads shelf RSS page or
   an uploaded Goodreads/StoryGraph CSV are looked up through the configured
   ``ProviderChain`` and returned with scored, verifiable candidates. Nothing
   is written -- no books, no settings, no status.
2. *Import* (explicit): the caller submits only ``(entry_key, provider_name,
   provider_uid, locale)`` selections. Every selection is re-resolved through
   ``ProviderChain.get_detail`` and the book is created from the provider's
   own detail record, never from client-supplied title/author fields. Rows
   without a verifiable provider identity are skipped, so no book is created
   that Wanted could not search for.

Idempotency: a selection whose provider id (or ASIN) is already known for
any book is skipped. Observation semantics: a reading-list observation is a
*partial* view; absence of a book from a later feed/CSV never deletes a
local book or clears monitoring -- this module has no delete/unmonitor path
at all. There is no auto-download: created books are only monitored, and
searching/grabbing stays with the existing Wanted flow.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from sqlite3 import Connection

from app.config import load_settings, save_settings
from app.library import BookCreate, create_book, get_conn
from app.models.settings import ReadingListSource, Settings
from app.providers.audible import AUDIBLE_API_HOSTS
from app.providers.base import BookQuickInfo
from app.providers.chain import ProviderChain
from app.reading_lists import (
    FEED_PER_PAGE,
    MAX_FEED_PAGE,
    GoodreadsFeedRef,
    ParseResult,
    ReadingListEntry,
    ReadingListError,
    clean_text,
    isbn_variants,
    validate_goodreads_feed_url,
)

log = logging.getLogger("audiarr.reading_list_import")

DEFAULT_PREVIEW_LIMIT = 25
MAX_PREVIEW_LIMIT = 50
MAX_CANDIDATES = 5
SEARCH_PAGE_SIZE = 10
LOOKUP_CONCURRENCY = 4
MATCH_THRESHOLD = 0.85  # top score at/above this -> status "matched"
MAX_IMPORT_SELECTIONS = 50
MAX_SOURCES = 20
_PROVIDER_UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SOURCE_ID_PREFIX = "goodreads-"


class SourceNotFoundError(ReadingListError):
    status_hint = 404


class SourceConflictError(ReadingListError):
    status_hint = 409


# -- saved Goodreads sources (settings-backed) --------------------------------


def list_reading_list_sources(settings: Settings | None = None) -> list[ReadingListSource]:
    return list((settings or load_settings()).import_lists.reading_list_sources)


def get_reading_list_source(source_id: str, settings: Settings | None = None) -> ReadingListSource:
    for source in list_reading_list_sources(settings):
        if source.id == source_id:
            return source
    raise SourceNotFoundError("source_not_found", "Unknown reading-list source.")


def source_feed_ref(source: ReadingListSource) -> GoodreadsFeedRef:
    return GoodreadsFeedRef(user_id=source.goodreads_user_id, shelf=source.shelf)


def add_goodreads_source(feed_url: str, name: str = "", enabled: bool = True) -> ReadingListSource:
    """Validate a feed URL and persist only its user id + shelf."""
    ref = validate_goodreads_feed_url(feed_url)
    settings = load_settings()
    sources = settings.import_lists.reading_list_sources
    if len(sources) >= MAX_SOURCES:
        raise SourceConflictError("too_many_sources", "Too many saved reading-list sources.")
    if any(s.goodreads_user_id == ref.user_id and s.shelf == ref.shelf for s in sources):
        raise SourceConflictError("source_exists", "This Goodreads shelf is already saved.")
    source = ReadingListSource(
        id=SOURCE_ID_PREFIX + secrets.token_hex(6),
        name=clean_text(name, 80) or f"Goodreads ({ref.shelf})",
        goodreads_user_id=ref.user_id,
        shelf=ref.shelf,
        enabled=enabled,
    )
    sources.append(source)
    save_settings(settings)
    log.info("Reading-list source added: type=goodreads_rss")
    return source


def update_goodreads_source(
    source_id: str, name: str | None = None, enabled: bool | None = None
) -> ReadingListSource:
    settings = load_settings()
    source = get_reading_list_source(source_id, settings)
    if name is not None:
        source.name = clean_text(name, 80) or source.name
    if enabled is not None:
        source.enabled = enabled
    save_settings(settings)
    return source


def delete_goodreads_source(source_id: str) -> None:
    settings = load_settings()
    sources = settings.import_lists.reading_list_sources
    kept = [s for s in sources if s.id != source_id]
    if len(kept) == len(sources):
        raise SourceNotFoundError("source_not_found", "Unknown reading-list source.")
    settings.import_lists.reading_list_sources = kept
    save_settings(settings)
    log.info("Reading-list source removed: type=goodreads_rss")


def record_source_import(source_id: str, result: ReadingListImportResult) -> None:
    """Persist the last-import status of a saved source (no-op if it vanished)."""
    settings = load_settings()
    for source in settings.import_lists.reading_list_sources:
        if source.id == source_id:
            source.last_sync_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
            source.sync_status = "error" if result.errors else "ok"
            source.last_sync_error = (
                "One or more selected books could not be imported." if result.errors else ""
            )
            source.last_sync_created = result.created
            source.last_sync_skipped = result.skipped_existing + result.skipped
            save_settings(settings)
            return


# -- matching -------------------------------------------------------------------


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", stripped.casefold()).split())


def _title_score(entry: ReadingListEntry, hit: BookQuickInfo) -> float:
    wanted = _fold(entry.search_title)
    options = [_fold(hit.title)]
    if hit.subtitle:
        options.append(_fold(f"{hit.title} {hit.subtitle}"))
    return max(SequenceMatcher(None, wanted, o).ratio() for o in options if o) if wanted else 0.0


def _author_score(entry: ReadingListEntry, hit: BookQuickInfo) -> float | None:
    if not entry.authors:
        return None
    candidate_tokens = {t for a in hit.authors for t in _fold(a).split()}
    best = 0.0
    for author in entry.authors:
        tokens = set(_fold(author).split())
        if tokens:
            best = max(best, len(tokens & candidate_tokens) / len(tokens))
    return best


@dataclass
class Candidate:
    provider_name: str
    provider_uid: str
    asin: str
    title: str
    subtitle: str
    authors: list[str]
    narrators: list[str]
    series: str
    series_position: int
    cover_url: str | None
    locale: str
    score: float
    match_reasons: list[str]
    existing_book_id: int | None = None


def _candidate(entry: ReadingListEntry, hit: BookQuickInfo, wanted_isbns: set[str]) -> Candidate:
    title = _title_score(entry, hit)
    author = _author_score(entry, hit)
    reasons: list[str] = []
    if hit.isbn and isbn_variants(hit.isbn.replace("-", "").upper()) & wanted_isbns:
        score = 1.0
        reasons.append("isbn")
    else:
        score = title if author is None else 0.7 * title + 0.3 * author
    if title >= 0.85:
        reasons.append("title")
    if author is not None and author >= 0.5:
        reasons.append("author")
    return Candidate(
        provider_name=hit.provider_name,
        provider_uid=hit.provider_uid or hit.asin or "",
        asin=hit.asin or "",
        title=hit.title,
        subtitle=hit.subtitle,
        authors=list(hit.authors),
        narrators=list(hit.narrators),
        series=hit.series,
        series_position=hit.series_position,
        cover_url=hit.cover_url,
        locale=hit.locale or "",
        score=round(score, 3),
        match_reasons=reasons,
    )


async def _lookup_hits(
    chain: ProviderChain, entry: ReadingListEntry, wanted_isbns: set[str]
) -> list[BookQuickInfo]:
    hits: list[BookQuickInfo] = []
    if wanted_isbns:
        # Keyword search by ISBN; only hits that echo a matching ISBN count.
        isbn = sorted(wanted_isbns, key=len)[-1]
        resp = await chain.search(isbn, page_size=SEARCH_PAGE_SIZE)
        hits += [
            h for h in resp.results
            if h.isbn and isbn_variants(h.isbn.replace("-", "").upper()) & wanted_isbns
        ]
    author = entry.authors[0] if entry.authors else ""
    kwargs: dict = {"page_size": SEARCH_PAGE_SIZE}
    resp = await chain.search(entry.search_title, **({"author": author, **kwargs} if author else kwargs))
    if not resp.results and author:
        resp = await chain.search(entry.search_title, **kwargs)
    hits += resp.results
    return hits


def find_existing_book(conn: Connection, *ids: str) -> int | None:
    """Book already known under any of these provider ids / ASINs (any provider/locale)."""
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return None
    marks = ",".join("?" * len(wanted))
    row = conn.execute(
        f"SELECT entity_id FROM provider_ids WHERE entity_type = 'book' "
        f"AND provider_id IN ({marks}) ORDER BY entity_id LIMIT 1",
        wanted,
    ).fetchone()
    if row is None:
        row = conn.execute(
            f"SELECT id AS entity_id FROM books WHERE asin IN ({marks}) ORDER BY id LIMIT 1", wanted
        ).fetchone()
    return int(row["entity_id"]) if row else None


# -- preview --------------------------------------------------------------------


@dataclass
class PreviewEntry:
    entry: ReadingListEntry
    status: str  # "matched" | "ambiguous" | "unmatched" | "lookup_failed"
    candidates: list[Candidate] = field(default_factory=list)
    existing_book_id: int | None = None  # top candidate already in the library


@dataclass
class PreviewResult:
    source_type: str  # "goodreads_rss" | "goodreads_csv" | "storygraph_csv"
    entries: list[PreviewEntry]
    total_entries: int  # entries in this observation after the shelf filter
    offset: int
    limit: int
    next_offset: int | None
    next_page: int | None  # RSS only: the next feed page to preview, if there may be one
    observation: dict
    skipped_rows: int
    duplicate_rows: int
    notes: list[str]


def apply_locale(chain: ProviderChain, locale: str) -> None:
    if not locale:
        return
    if locale.lower() not in AUDIBLE_API_HOSTS:
        raise ReadingListError("locale_invalid", "Unsupported metadata locale.")
    chain.config.audible_locale = locale.lower()


async def preview_entries(
    chain: ProviderChain,
    parsed: ParseResult,
    *,
    source_type: str,
    offset: int = 0,
    limit: int = DEFAULT_PREVIEW_LIMIT,
    shelf: str = "",
    locale: str = "",
    page: int | None = None,
) -> PreviewResult:
    """Look up one bounded window of ``parsed`` via the provider chain. Read-only."""
    apply_locale(chain, locale)
    offset = max(0, offset)
    limit = max(1, min(limit, MAX_PREVIEW_LIMIT))
    wanted_shelf = shelf.strip().casefold()
    pool = [
        e for e in parsed.entries
        if not wanted_shelf or wanted_shelf in {s.casefold() for s in e.shelves}
    ]
    window = pool[offset : offset + limit]

    gate = asyncio.Semaphore(LOOKUP_CONCURRENCY)

    async def one(entry: ReadingListEntry) -> PreviewEntry:
        wanted = isbn_variants(entry.isbn, entry.isbn13)
        async with gate:
            try:
                hits = await _lookup_hits(chain, entry, wanted)
            except Exception as exc:  # noqa: BLE001 -- one bad lookup must not fail the preview
                log.warning("Reading-list lookup failed (%s)", type(exc).__name__)
                return PreviewEntry(entry=entry, status="lookup_failed")
        order = {name: i for i, name in enumerate(chain.config.provider_order)}
        seen: set[tuple[str, str]] = set()
        candidates: list[Candidate] = []
        for hit in hits:
            cand = _candidate(entry, hit, wanted)
            key = (cand.provider_name, cand.provider_uid)
            if not cand.provider_uid or key in seen:
                continue
            seen.add(key)
            candidates.append(cand)
        candidates.sort(
            key=lambda c: (-c.score, order.get(c.provider_name, 99), c.title, c.provider_uid)
        )
        candidates = candidates[:MAX_CANDIDATES]
        status = "unmatched"
        if candidates:
            status = "matched" if candidates[0].score >= MATCH_THRESHOLD else "ambiguous"
        return PreviewEntry(entry=entry, status=status, candidates=candidates)

    results = list(await asyncio.gather(*(one(e) for e in window)))

    # Read-only library check (no writes; the connection only reads).
    with get_conn() as conn:
        for item in results:
            for cand in item.candidates:
                cand.existing_book_id = find_existing_book(conn, cand.provider_uid, cand.asin)
            if item.candidates:
                item.existing_book_id = item.candidates[0].existing_book_id

    is_feed = source_type == "goodreads_rss"
    notes = ["absence_never_removes"]
    if is_feed:
        notes.insert(0, "partial_observation")
    if parsed.truncated:
        notes.append("input_row_cap_reached")
    if offset + limit < len(pool):
        notes.append("preview_window_truncated")
    next_page = None
    if is_feed and page is not None and parsed.total_rows >= FEED_PER_PAGE and page < MAX_FEED_PAGE:
        next_page = page + 1
        notes.append("feed_may_have_more_pages")
    log.info(
        "Reading-list preview source_type=%s rows=%d window=%d matched=%d ambiguous=%d unmatched=%d",
        source_type,
        parsed.total_rows,
        len(results),
        sum(r.status == "matched" for r in results),
        sum(r.status == "ambiguous" for r in results),
        sum(r.status in {"unmatched", "lookup_failed"} for r in results),
    )
    return PreviewResult(
        source_type=source_type,
        entries=results,
        total_entries=len(pool),
        offset=offset,
        limit=limit,
        next_offset=offset + limit if offset + limit < len(pool) else None,
        next_page=next_page,
        observation={
            "kind": "goodreads_rss_page" if is_feed else "csv_upload",
            "page": page,
            "complete": (not is_feed) and not parsed.truncated,
        },
        skipped_rows=parsed.skipped_rows,
        duplicate_rows=parsed.duplicate_rows,
        notes=notes,
    )


# -- selected import ------------------------------------------------------------


@dataclass
class ImportSelection:
    entry_key: str
    provider_name: str = ""
    provider_uid: str = ""
    locale: str = ""


@dataclass
class ImportItemResult:
    entry_key: str
    # created | skipped_existing | skipped_unmatched | skipped_unverifiable
    # | skipped_invalid | error
    status: str
    reason: str = ""
    message: str = ""
    book_id: int | None = None


@dataclass
class ReadingListImportResult:
    created: int = 0
    skipped_existing: int = 0
    skipped: int = 0  # unmatched + unverifiable + invalid
    errors: int = 0
    items: list[ImportItemResult] = field(default_factory=list)


_MESSAGES = {
    "no_selection": "No metadata result was selected for this entry.",
    "invalid_provider": "The selected metadata provider is not configured.",
    "invalid_provider_id": "The selected metadata id is not valid.",
    "invalid_locale": "The selected metadata locale is not supported.",
    "provider_lookup_failed": "The metadata provider could not confirm the selected result.",
    "identity_mismatch": "The metadata provider returned a different book than selected.",
    "already_in_library": "This book is already in the library.",
    "create_failed": "The book could not be created.",
}


def _item(entry_key: str, status: str, reason: str, book_id: int | None = None) -> ImportItemResult:
    return ImportItemResult(entry_key, status, reason, _MESSAGES.get(reason, ""), book_id)


async def _import_one(chain: ProviderChain, sel: ImportSelection) -> ImportItemResult:
    key = sel.entry_key
    if not sel.provider_name and not sel.provider_uid:
        return _item(key, "skipped_unmatched", "no_selection")
    name = sel.provider_name.strip().lower()
    uid = sel.provider_uid.strip()
    if name not in {p.lower() for p in chain.config.provider_order}:
        return _item(key, "skipped_invalid", "invalid_provider")
    if not _PROVIDER_UID_RE.match(uid):
        return _item(key, "skipped_invalid", "invalid_provider_id")
    locale = (sel.locale or chain.config.audible_locale).lower()
    if locale not in AUDIBLE_API_HOSTS:
        return _item(key, "skipped_invalid", "invalid_locale")

    with get_conn() as conn:
        known = find_existing_book(conn, uid)
    if known is not None:
        return _item(key, "skipped_existing", "already_in_library", known)

    chain.config.audible_locale = locale
    try:
        detail = await chain.get_detail(name, uid)
    except Exception as exc:  # noqa: BLE001 -- one bad selection must not abort the batch
        log.warning("Reading-list import: provider detail failed (%s)", type(exc).__name__)
        return _item(key, "skipped_unverifiable", "provider_lookup_failed")
    if detail is None or not detail.title.strip():
        return _item(key, "skipped_unverifiable", "provider_lookup_failed")
    identities = {
        i.casefold() for i in (detail.provider_uid, detail.provider_external_id, detail.asin) if i
    }
    if uid.casefold() not in identities or detail.provider_name.lower() != name:
        return _item(key, "skipped_unverifiable", "identity_mismatch")

    try:
        with get_conn() as conn:
            known = find_existing_book(conn, uid, detail.asin or "")
            if known is not None:
                return _item(key, "skipped_existing", "already_in_library", known)
            book_id = create_book(
                conn,
                BookCreate(
                    title=detail.title.strip(),
                    subtitle=detail.subtitle,
                    description=detail.description,
                    release_date=detail.release_date or None,
                    language=detail.language,
                    publisher=", ".join(detail.publishers),
                    duration_seconds=detail.duration_seconds,
                    cover_url=detail.cover_url,
                    authors=list(detail.authors),
                    narrators=list(detail.narrators),
                    series=detail.series,
                    series_position=detail.series_position or None,
                    provider=name,
                    provider_id=uid,
                    locale=detail.audible_locale or locale,
                    monitored=True,
                ),
            )
    except Exception as exc:  # noqa: BLE001 -- one bad row must not abort the batch
        log.warning("Reading-list import: create failed (%s)", type(exc).__name__)
        return _item(key, "error", "create_failed")
    return ImportItemResult(key, "created", "", "", book_id)


async def import_selections(
    chain: ProviderChain, selections: list[ImportSelection], *, source_type: str
) -> ReadingListImportResult:
    """Create monitored books for explicitly selected, provider-verified results."""
    if len(selections) > MAX_IMPORT_SELECTIONS:
        raise ReadingListError("too_many_selections", "Too many selections in one import.")
    result = ReadingListImportResult()
    seen_keys: set[str] = set()
    for sel in selections:
        if sel.entry_key in seen_keys:
            continue  # same entry submitted twice: first selection wins
        seen_keys.add(sel.entry_key)
        item = await _import_one(chain, sel)
        result.items.append(item)
        if item.status == "created":
            result.created += 1
        elif item.status == "skipped_existing":
            result.skipped_existing += 1
        elif item.status == "error":
            result.errors += 1
        else:
            result.skipped += 1
    log.info(
        "Reading-list import source_type=%s created=%d skipped_existing=%d skipped=%d errors=%d",
        source_type,
        result.created,
        result.skipped_existing,
        result.skipped,
        result.errors,
    )
    return result
