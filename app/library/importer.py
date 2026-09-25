"""Import pipeline orchestrator: scan → match → persist (with dry-run).

This module ties together scanner, matcher, providers, and the library
store. A `run_import` call processes one root folder end-to-end:

  1. scan_folder() collects filesystem candidates
  2. for each candidate: search providers (ASIN first, then fuzzy via
     guessed title/author), score hits, pick the best match
  3. a matched candidate's files are placed on disk per the root folder's
     configured import_strategy (move/copy/hardlink, see #30 and
     app.library.import_strategy) and the book is persisted with the
     files' actual final paths; unmatched candidates are recorded as
     import_jobs with status 'failed' for later retry/manual matching.

Dry-run never touches the filesystem or the DB -- see the `if dry_run`
guard in `_import_one`, which returns before any strategy/persist call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import load_settings
from app.connect import dispatch_event
from app.library import BookCreate
from app.library import create_book as library_create_book
from app.library.import_strategy import ImportStrategyError, PlacedFile, place_candidate_files
from app.library.matcher import MatchResult, match_candidate_to_hits
from app.library.scanner import BookCandidate, scan_folder
from app.models.settings import QualityProfile, Settings
from app.providers.base import BookQuickInfo
from app.providers.chain import ProviderChain
from app.quality import should_convert_candidate

log = logging.getLogger("audiarr.library.importer")


class ImportMatchError(RuntimeError):
    """Raised by import_single_folder when no provider resolves the ASIN."""


@dataclass
class ImportCandidateResult:
    """Outcome of one candidate in an import run."""

    folder_path: str
    status: str  # "matched" | "unmatched" | "error" | "skipped-duplicate" | "ignored"
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
    ignored: int = 0
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
        "SELECT path, import_strategy FROM root_folders WHERE id = ?", (root_folder_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"root folder {root_folder_id} not found")
    root_path = row["path"]
    strategy = row["import_strategy"]

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

    # Folders explicitly ignored via the Import Problems workflow are
    # skipped entirely (no provider calls) and never count as unmatched.
    ignored_paths = {
        row[0] for row in conn.execute("SELECT path FROM import_ignores").fetchall()
    }

    for candidate in candidates:
        if candidate.folder_path in ignored_paths:
            summary.results.append(
                ImportCandidateResult(folder_path=candidate.folder_path, status="ignored")
            )
            summary.ignored += 1
            continue

        result = await _import_one(conn, chain, candidate, dry_run, locale, root_path, strategy)
        summary.results.append(result)
        if result.status == "matched":
            summary.matched += 1
        elif result.status == "unmatched":
            summary.unmatched += 1
        elif result.status == "error":
            summary.errors += 1

    if not dry_run:
        _persist_import_jobs(conn, summary)
        # Ask Audiobookshelf to rescan so it picks up the new files. This
        # is best-effort and never raises (see notify_library_changed).
        from app.connections.audiobookshelf import notify_library_changed

        await notify_library_changed()

    log.info(summary.message)
    return summary


async def _import_one(
    conn: Any,
    chain: ProviderChain,
    candidate: BookCandidate,
    dry_run: bool,
    locale: str,
    root_path: str,
    strategy: str,
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

    try:
        placed_files = _place_files(candidate, root_path, strategy)
    except ImportStrategyError as exc:
        log.error(
            "import strategy %r failed for %r: %s", strategy, candidate.folder_path, exc
        )
        base.status = "error"
        base.detail = str(exc)
        return base

    # Persist book via library store, with the files' actual final paths.
    book_id = _persist_book(conn, hit, locale)
    _persist_files(conn, book_id, placed_files, candidate.dominant_format, locale)

    # Offer the candidate to the conversion backend if the quality profile
    # wants it (no-op when conversion is disabled; the backend owns file
    # movement). Uses the files' final folder, not the original scan
    # location, since a "move" strategy may have relocated them.
    final_folder = _final_folder(placed_files, candidate.folder_path)
    await _maybe_enqueue_conversion(conn, book_id, candidate, source_path=final_folder)

    base.status = "matched"
    base.matched_book_id = book_id
    await dispatch_event(
        "import",
        {"book_id": book_id, "title": hit.title, "source_path": candidate.folder_path, "status": "matched"},
    )
    return base


def _final_folder(placed_files: list[PlacedFile], fallback: str) -> str:
    """Return the folder that now holds a candidate's placed files.

    All of a candidate's files share one target folder (see
    place_candidate_files), so any placed file's parent directory works;
    falls back to the original scan location for an empty list (a
    candidate with zero files, which never reaches this path in practice).
    """
    if not placed_files:
        return fallback
    return str(Path(placed_files[0].final_path).parent)


def _place_files(candidate: BookCandidate, root_path: str, strategy: str) -> list[PlacedFile]:
    """Resolve + apply the strategy for every file of one candidate.

    Every scanned file is a direct child of the candidate's own folder (see
    scan_folder), so its source path is simply candidate.folder_path joined
    with the file's basename.
    """
    files = [
        (
            str(Path(candidate.folder_path) / Path(f.path).name),
            Path(f.path).name,
            f.size_bytes,
            f.format,
        )
        for f in candidate.files
    ]
    return place_candidate_files(files, root_path, candidate.folder_name, strategy)


async def import_single_folder(
    conn: Any,
    chain: ProviderChain,
    folder_path: str,
    asin: str,
    locale: str,
) -> ImportCandidateResult:
    """Manually match one folder to a provider ASIN (Import Problems workflow).

    Re-derives the BookCandidate by rescanning the folder's parent directory
    (reusing the scanner's normalization/ASIN-hint/file-listing logic instead
    of trusting client-supplied metadata), resolves ``asin`` via the provider
    chain, and persists exactly like a normal import match.

    Unlike the batch ``run_import`` path, provider errors are NOT swallowed
    here: this is a single, interactive, user-triggered action, so callers
    (the API route) surface failures immediately instead of recording a
    silent "unmatched" result.
    """
    folder = Path(folder_path)
    candidates = scan_folder(folder.parent)
    candidate = next((c for c in candidates if c.folder_path == str(folder)), None)
    if candidate is None:
        raise ValueError(f"folder no longer exists or has no audio files: {folder_path}")

    # Providers must be built for the requested locale FIRST — a DE ASIN
    # returns an empty-title detail from the US endpoint (see
    # _search_candidates); an empty title is treated as a miss below.
    chain.config.audible_locale = locale

    detail = None
    for name in chain.config.provider_order:
        provider = chain._resolve_provider(name)
        if provider is None:
            continue
        detail = await provider.get_detail(asin)
        if detail is not None and detail.title:
            break
        detail = None  # empty title = miss for this provider; try next

    if detail is None:
        raise ImportMatchError(f"no provider resolved ASIN {asin!r} for locale {locale!r}")

    hit = BookQuickInfo(
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

    result = ImportCandidateResult(
        folder_path=candidate.folder_path,
        status="unmatched",
        matched_asin=hit.asin,
        method="asin",
    )

    # Duplicate check: same provider id already imported?
    provider_name = hit.provider_name.lower()
    existing = conn.execute(
        """SELECT entity_id FROM provider_ids
           WHERE entity_type = 'book' AND provider = ? AND provider_id = ?""",
        (provider_name, hit.asin),
    ).fetchone()
    if existing:
        result.status = "skipped-duplicate"
        result.matched_book_id = existing[0]
        _record_import_job(conn, result)
        return result

    # If a configured root folder governs this folder's location, place the
    # files per its import_strategy; otherwise (e.g. a folder matched from a
    # download-client directory that isn't itself a configured root folder)
    # fall back to recording the scanner's paths as-is, unchanged.
    root_row = _find_root_folder_for_path(conn, candidate.folder_path)
    if root_row is not None:
        try:
            placed_files = _place_files(candidate, root_row["path"], root_row["import_strategy"])
        except ImportStrategyError as exc:
            log.error(
                "import strategy %r failed for %r: %s",
                root_row["import_strategy"], candidate.folder_path, exc,
            )
            result.status = "error"
            result.detail = str(exc)
            _record_import_job(conn, result)
            return result
        final_folder = _final_folder(placed_files, candidate.folder_path)
    else:
        placed_files = _unplaced_files(candidate)
        final_folder = candidate.folder_path

    book_id = _persist_book(conn, hit, locale)
    _persist_files(conn, book_id, placed_files, candidate.dominant_format, locale)

    await _maybe_enqueue_conversion(conn, book_id, candidate, source_path=final_folder)

    result.status = "matched"
    result.matched_book_id = book_id
    _record_import_job(conn, result)
    await dispatch_event(
        "import",
        {
            "book_id": book_id,
            "title": hit.title,
            "source_path": candidate.folder_path,
            "status": "matched",
        },
    )
    return result


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


def _persist_files(
    conn: Any, book_id: int, placed_files: list[PlacedFile], dominant_format: str, locale: str
) -> None:
    """Record a candidate's files against the book's edition.

    ``placed_files`` carries each file's actual final filesystem path (see
    _place_files/_unplaced_files) so library_files.path is always openable
    later, never a stale scanner-relative fragment.
    """
    cur = conn.execute(
        """INSERT INTO editions (book_id, format, locale) VALUES (?, ?, ?)""",
        (book_id, dominant_format, locale),
    )
    edition_id = cur.lastrowid
    for pf in placed_files:
        conn.execute(
            """INSERT INTO library_files
               (edition_id, path, size_bytes, format) VALUES (?, ?, ?, ?)""",
            (edition_id, pf.final_path, pf.size_bytes, pf.format),
        )


def _find_root_folder_for_path(conn: Any, folder_path: str) -> Any:
    """Return the root_folders row whose configured path contains ``folder_path``.

    Same containment rule as app.sab_auto_import.SabAutoImportScheduler.
    _find_root_folder (kept local rather than shared to avoid coupling this
    module to the SAB poller).
    """
    rows = conn.execute("SELECT id, path, import_strategy FROM root_folders").fetchall()
    for row in rows:
        root = row["path"].rstrip("/")
        if folder_path == root or folder_path.startswith(root + "/"):
            return row
    return None


def _unplaced_files(candidate: BookCandidate) -> list[PlacedFile]:
    """PlacedFile list for a candidate with no governing root folder.

    Nothing is moved/copied/hardlinked -- files are recorded at their
    current scanner-relative path exactly like before #30, since there is
    no configured root folder to resolve a strategy or a safe target
    against.
    """
    return [
        PlacedFile(f.path, f.path, f.size_bytes, f.format, "unchanged")
        for f in candidate.files
    ]


def _resolve_quality_profile(
    conn: Any, book_id: int, settings: Settings
) -> QualityProfile | None:
    """Resolve the quality profile that applies to a book.

    Books store an optional ``quality_profile`` name (see migration #008 /
    issue #20); empty or a name that no longer matches a configured profile
    falls back to the first configured profile (the settings-wide default).
    """
    if not settings.quality_profiles:
        return None
    row = conn.execute(
        "SELECT quality_profile FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    name = row["quality_profile"] if row else ""
    if name:
        for profile in settings.quality_profiles:
            if profile.name == name:
                return profile
    return settings.quality_profiles[0]


async def _maybe_enqueue_conversion(
    conn: Any, book_id: int, candidate: BookCandidate, source_path: str | None = None
) -> None:
    """Offer a candidate to the conversion backend if the quality profile wants it.

    Profile-aware replacement for the old hardcoded "mp3/m4a always enqueue"
    check (see app/quality.py::should_convert_candidate and #17): the book's
    own quality profile decides whether this candidate's format is worth
    converting toward, given its target quality tier, falling back to the
    first configured profile when the book has none set (see #20). Never
    blocks the import itself -- only the conversion enqueue decision.

    ``source_path`` defaults to the candidate's original scan location;
    callers pass the files' actual final folder when an import strategy
    (#30) may have relocated them, so the conversion backend looks in the
    right place.
    """
    settings = load_settings()
    profile = _resolve_quality_profile(conn, book_id, settings)
    if profile is None or not should_convert_candidate(
        candidate.dominant_format, profile, settings.quality_definitions
    ):
        return
    try:
        from app.conversion.worker import enqueue_for_book

        await enqueue_for_book(conn, book_id, source_path or candidate.folder_path, output_path="")
    except Exception:  # noqa: BLE001 — conversion must never break imports
        log.warning("auto-enqueue conversion failed for book %d", book_id, exc_info=True)


def _job_outcome(result: ImportCandidateResult) -> tuple[str, str | None]:
    """Map a candidate result to the (status, error) pair stored on import_jobs."""
    status = "completed" if result.status == "matched" else "failed"
    if result.status == "skipped-duplicate":
        status = "completed"
    error = None if result.status == "matched" else result.status
    return status, error


def _record_import_job(conn: Any, result: ImportCandidateResult) -> None:
    """Insert one import_jobs row for a single manually-processed result."""
    status, error = _job_outcome(result)
    conn.execute(
        """INSERT INTO import_jobs (source_path, status, error) VALUES (?, ?, ?)""",
        (result.folder_path, status, error),
    )


def _persist_import_jobs(conn: Any, summary: ImportSummary) -> None:
    """Record per-candidate outcomes as import_jobs rows for auditability."""
    for result in summary.results:
        status, error = _job_outcome(result)
        conn.execute(
            """INSERT INTO import_jobs (source_path, status, error)
               VALUES (?, ?, ?)""",
            (
                result.folder_path,
                status,
                error,
            ),
        )
