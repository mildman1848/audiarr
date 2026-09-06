"""Import pipeline orchestrator: scan → match → persist (with dry-run).

This module ties together scanner, matcher, providers, and the library
store. A `run_import` call processes one root folder end-to-end:

  1. scan_folder() collects filesystem candidates
  2. for each candidate: search providers (ASIN first, then fuzzy via
     guessed title/author), score hits, pick the best match
  3. matched candidates become books (via store.upsert_* functions);
     unmatched ones are recorded as import_jobs with status 'failed'
     for later retry/manual matching.

No file is ever moved, renamed, or deleted — the import only records
what exists. File moves/renames are a separate workflow (future issue).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.library import BookCreate
from app.library import create_book as library_create_book
from app.library.matcher import MatchResult, match_candidate_to_hits
from app.library.scanner import BookCandidate, scan_folder
from app.providers.base import BookQuickInfo
from app.providers.chain import ProviderChain

log = logging.getLogger("audiarr.library.importer")


@dataclass
class ImportCandidateResult:
    """Outcome of one candidate in an import run."""

    folder_path: str
    status: str  # "matched" | "unmatched" | "error" | "skipped-duplicate"
    matched_book_id: int | None = None
    matched_asin: str | None = None
    score: float | None = None
    method: str | None = None  # "asin" | "fuzzy"
    detail: str = ""


@dataclass
class ImportSummary:
    root_folder_id: int
    root_path: str
    dry_run: bool
    total_candidates: int = 0
    matched: int = 0
    unmatched: int = 0
    errors: int = 0
    file_count: int = 0
    total_size_bytes: int = 0
    results: list[ImportCandidateResult] = field(default_factory=list)

    @property
    def message(self) -> str:
        return (
            f"Import {'dry-run' if self.dry_run else 'run'} of {self.root_path}: "
            f"{self.matched} matched, {self.unmatched} unmatched, "
            f"{self.errors} error(s)"
        )


async def _search_candidates(
    chain: ProviderChain, candidate: BookCandidate, locale: str
) -> list[BookQuickInfo]:
    """Search providers for one candidate; ASIN first, then title/author."""
    hits: list[BookQuickInfo] = []

    # 1) Exact ASIN lookup (if the folder/file name carries one).
    #    Providers are resolved through the chain's configured order
    #    (overrides first) — same path interactive search uses, so tests
    #    inject stubs exactly the same way.
    for asin in candidate.asin_hints[:1]:
        try:
            detail = None
            for name in chain.config.provider_order:
                provider = chain._resolve_provider(name)
                if provider is None:
                    continue
                detail = await provider.get_detail(asin)
                if detail is not None and detail.title:
                    break
                detail = None  # empty title = miss for this provider; try next
        except Exception:  # noqa: BLE001 — provider errors must not kill the run
            log.warning("ASIN detail lookup failed for %s", asin, exc_info=True)
            detail = None
        if detail is not None:
            hits.append(
                BookQuickInfo(
                    provider_uid=detail.provider_uid,
                    provider_name=detail.provider_name,
                    title=detail.title,
                    subtitle=detail.subtitle,
                    authors=detail.authors,
                    narrators=detail.narrators,
                    series=detail.series,
                    series_position=detail.series_position,
                    cover_url=detail.cover_url,
                    asin=detail.asin or asin,
                    isbn=detail.isbn,
                    locale=locale,
                )
            )

    # 2) Fuzzy search by guessed title/author.
    query = candidate.guessed_title or candidate.folder_name
    try:
        response = await chain.search(
            query, author=candidate.guessed_author or None, locale=locale
        )
        hits.extend(response.results)
    except Exception:  # noqa: BLE001
        log.warning("provider search failed for %r", query, exc_info=True)

    return hits


async def run_import(
    conn: Any,
    chain: ProviderChain,
    root_folder_id: int,
    dry_run: bool = False,
    locale: str = "us",
) -> ImportSummary:
    """Scan, match, and import one root folder.

    ``conn`` is an open sqlite3 connection; ``chain`` the provider chain.
    In dry-run mode nothing is written — the summary reports what would
    happen.
    """
    # Providers must be built for the import locale (e.g. German books
    # need api.audible.de), mirroring how the metadata search route does it.
    chain.config.audible_locale = locale
    row = conn.execute(
        "SELECT path FROM root_folders WHERE id = ?", (root_folder_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"root folder {root_folder_id} not found")
    root_path = row[0]

    summary = ImportSummary(
        root_folder_id=root_folder_id, root_path=root_path, dry_run=dry_run
    )

    candidates = scan_folder(Path(root_path))
    summary.total_candidates = len(candidates)
    summary.file_count = sum(len(c.files) for c in candidates)
    summary.total_size_bytes = sum(c.total_size_bytes for c in candidates)
    log.info(
        "import %s: %d candidate(s), %d file(s), %.2f GiB",
        "(dry-run) " if dry_run else "",
        summary.total_candidates,
        summary.file_count,
        summary.total_size_bytes / (1024**3),
    )

    for candidate in candidates:
        result = await _import_one(conn, chain, candidate, dry_run, locale)
        summary.results.append(result)
        if result.status == "matched":
            summary.matched += 1
        elif result.status == "unmatched":
            summary.unmatched += 1
        elif result.status == "error":
            summary.errors += 1

    if not dry_run:
        _persist_import_jobs(conn, summary)

    log.info(summary.message)
    return summary


async def _import_one(
    conn: Any,
    chain: ProviderChain,
    candidate: BookCandidate,
    dry_run: bool,
    locale: str,
) -> ImportCandidateResult:
    """Process a single candidate: search, match, persist book + files."""
    base = ImportCandidateResult(folder_path=candidate.folder_path, status="unmatched")

    hits = await _search_candidates(chain, candidate, locale)
    match: MatchResult | None = match_candidate_to_hits(candidate, hits)

    if match is None:
        log.debug(
            "no match for %r (title=%r author=%r asin_hints=%s)",
            candidate.folder_name,
            candidate.guessed_title,
            candidate.guessed_author,
            candidate.asin_hints,
        )
        return base

    hit = match.hit
    base.matched_asin = hit.asin
    base.score = round(match.score, 3)
    base.method = match.method

    # Duplicate check: same provider id already imported?
    provider = hit.provider_name.lower()
    existing = conn.execute(
        """SELECT entity_id FROM provider_ids
           WHERE entity_type = 'book' AND provider = ? AND provider_id = ?""",
        (provider, hit.asin),
    ).fetchone()
    if existing:
        base.status = "skipped-duplicate"
        base.matched_book_id = existing[0]
        return base

    if dry_run:
        base.status = "matched"
        base.detail = "would create book + files"
        return base

    # Persist book via library store.
    book_id = _persist_book(conn, hit, locale)
    _persist_files(conn, book_id, candidate, locale)

    base.status = "matched"
    base.matched_book_id = book_id
    return base


def _persist_book(conn: Any, hit: BookQuickInfo, locale: str) -> int:
    """Create book + attribution via the library store (joins handled there)."""
    data = BookCreate(
        title=hit.title,
        subtitle=hit.subtitle or "",
        authors=hit.authors,
        narrators=hit.narrators,
        series=hit.series or "",
        series_position=hit.series_position or None,
        language=locale,
        cover_url=hit.cover_url,
        provider=hit.provider_name.lower(),
        provider_id=hit.asin or "",
        locale=locale,
    )
    return library_create_book(conn, data)


def _persist_files(conn: Any, book_id: int, candidate: BookCandidate, locale: str) -> None:
    """Record the candidate's audio files against the book's edition."""
    cur = conn.execute(
        """INSERT INTO editions (book_id, format, locale) VALUES (?, ?, ?)""",
        (book_id, candidate.dominant_format, locale),
    )
    edition_id = cur.lastrowid
    for f in candidate.files:
        conn.execute(
            """INSERT INTO library_files
               (edition_id, path, size_bytes, format) VALUES (?, ?, ?, ?)""",
            (edition_id, f.path, f.size_bytes, f.format),
        )


def _persist_import_jobs(conn: Any, summary: ImportSummary) -> None:
    """Record per-candidate outcomes as import_jobs rows for auditability."""
    for result in summary.results:
        status = "completed" if result.status == "matched" else "failed"
        if result.status == "skipped-duplicate":
            status = "completed"
        conn.execute(
            """INSERT INTO import_jobs (source_path, status, error)
               VALUES (?, ?, ?)""",
            (
                result.folder_path,
                status,
                None if result.status == "matched" else result.status,
            ),
        )
