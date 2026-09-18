"""Wanted/Missing API: monitored books without any library files, plus
cutoff-upgrade tracking for monitored books that already have files.

First slice (missing) surfaces the gap between "the user wants this book"
(monitored=1) and "it's actually on disk" (zero files across all editions).
Second slice (cutoff) surfaces monitored books that DO have files, but whose
best file's inferred quality falls below their quality profile's cutoff
tier -- and lets the user trigger an upgrade search + grab for one, reusing
the same Prowlarr search / evaluate_quality_for_profile / SABnzbd grab path
as the interactive release search (see routes_releases.py).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import load_settings
from app.connections.prowlarr import ProwlarrClient
from app.connections.sabnzbd import SABnzbdClient
from app.db import get_conn, migrate
from app.library.importer import _resolve_quality_profile
from app.models.settings import DownloadClient, Indexer, QualityDefinition, QualityProfile
from app.quality import QualityFit, evaluate_quality_for_profile, infer_quality_from_name

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


class CutoffCandidateOut(BaseModel):
    id: int
    title: str
    authors: list[str]
    current_quality_name: str | None
    current_container: str | None
    current_bitrate_kbps: int | None
    profile_name: str
    cutoff_name: str


class CutoffSearchResponse(BaseModel):
    ok: bool
    found: bool
    message: str
    release_title: str | None = None
    nzo_id: str | None = None
    reason: str = ""


def _ensure_schema() -> None:
    """Defensive migration, mirroring the other stateful routers."""
    migrate()


def _enabled_prowlarr() -> Indexer | None:
    """Return the first enabled Prowlarr indexer from settings, if any."""
    for indexer in load_settings().indexers:
        if indexer.type == "prowlarr" and indexer.enabled:
            return indexer
    return None


def _enabled_sabnzbd() -> DownloadClient | None:
    """Return the first enabled SABnzbd download client from settings, if any."""
    for client in load_settings().download_clients:
        if client.type == "sabnzbd" and client.enabled:
            return client
    return None


def _require_prowlarr() -> Indexer:
    indexer = _enabled_prowlarr()
    if indexer is None:
        raise HTTPException(503, "No enabled Prowlarr indexer configured")
    return indexer


def _require_sabnzbd() -> DownloadClient:
    client = _enabled_sabnzbd()
    if client is None:
        raise HTTPException(503, "No enabled SABnzbd download client configured")
    return client


def _quality_name(definitions: list[QualityDefinition], quality_id: str | None) -> str | None:
    if quality_id is None:
        return None
    return next((d.name for d in definitions if d.id == quality_id), quality_id)


def _best_current_fit(
    paths: list[str], profile: QualityProfile, definitions: list[QualityDefinition]
) -> QualityFit | None:
    """Evaluate every library file of a book and keep its best-fitting tier.

    A book can have several files (multi-part editions); "current quality"
    is the best tier found among them, not the worst -- an upgrade search
    should only trigger when even the best file the user already has falls
    below the profile's cutoff.
    """
    best_fit: QualityFit | None = None
    best_index: int | None = None
    for path in paths:
        inferred = infer_quality_from_name(path)
        fit = evaluate_quality_for_profile(inferred, profile, definitions)
        index = (
            profile.quality_ids.index(fit.matched_quality_id)
            if fit.matched_quality_id in profile.quality_ids
            else None
        )
        if best_fit is None or (index is not None and (best_index is None or index < best_index)):
            best_fit, best_index = fit, index
    return best_fit


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


@router.get("/api/v1/wanted/cutoff", response_model=list[CutoffCandidateOut])
async def get_wanted_cutoff() -> list[CutoffCandidateOut]:
    """Monitored books whose best file is below their quality profile's cutoff."""
    _ensure_schema()
    settings = load_settings()
    definitions = settings.quality_definitions

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT b.id, b.title,
                      (SELECT GROUP_CONCAT(a.name, ', ')
                         FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                        WHERE ba.book_id = b.id ORDER BY ba.position) AS authors
                 FROM books b
                WHERE b.monitored = 1
                  AND EXISTS (
                        SELECT 1 FROM editions e
                          JOIN library_files lf ON lf.edition_id = e.id
                         WHERE e.book_id = b.id)
                ORDER BY b.title""",
        ).fetchall()

        candidates: list[CutoffCandidateOut] = []
        for row in rows:
            profile = _resolve_quality_profile(conn, row["id"], settings)
            if profile is None or not profile.upgrade_allowed:
                continue

            paths = [
                r["path"]
                for r in conn.execute(
                    """SELECT lf.path FROM editions e
                         JOIN library_files lf ON lf.edition_id = e.id
                        WHERE e.book_id = ?""",
                    (row["id"],),
                ).fetchall()
            ]
            fit = _best_current_fit(paths, profile, definitions)
            if fit is None or fit.status != "below_cutoff":
                continue

            log.debug(
                "book %d (%r) below cutoff: matched=%s profile=%r cutoff=%r",
                row["id"],
                row["title"],
                fit.matched_quality_id,
                profile.name,
                profile.cutoff_quality_id,
            )
            candidates.append(
                CutoffCandidateOut(
                    id=row["id"],
                    title=row["title"],
                    authors=row["authors"].split(", ") if row["authors"] else [],
                    current_quality_name=_quality_name(definitions, fit.matched_quality_id),
                    current_container=fit.inferred.container,
                    current_bitrate_kbps=fit.inferred.bitrate_kbps,
                    profile_name=profile.name,
                    cutoff_name=_quality_name(definitions, profile.cutoff_quality_id)
                    or profile.cutoff_quality_id,
                )
            )

    return candidates


@router.post("/api/v1/wanted/cutoff/{book_id}/search", response_model=CutoffSearchResponse)
async def search_cutoff_upgrade(book_id: int) -> CutoffSearchResponse | JSONResponse:
    """Search for a release that meets the book's profile cutoff and grab it.

    Reuses the same Prowlarr search / evaluate_quality_for_profile /
    SABnzbd grab path as /api/v1/releases/search and /api/v1/releases/grab,
    scoped to this book's own resolved quality profile instead of the
    release-search page's default profile. Only grabs when a release
    fits (status "preferred" or "accepted"); otherwise nothing is grabbed
    and a 202 "no fitting release" result is returned.
    """
    _ensure_schema()
    settings = load_settings()

    with get_conn() as conn:
        book = conn.execute("SELECT id, title FROM books WHERE id = ?", (book_id,)).fetchone()
        if book is None:
            raise HTTPException(404, f"book {book_id} not found")
        author_row = conn.execute(
            """SELECT GROUP_CONCAT(a.name, ', ') AS authors
                 FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                WHERE ba.book_id = ? ORDER BY ba.position""",
            (book_id,),
        ).fetchone()
        authors = author_row["authors"].split(", ") if author_row and author_row["authors"] else []
        profile = _resolve_quality_profile(conn, book_id, settings)

    indexer = _require_prowlarr()

    if profile is None:
        log.debug("cutoff search for book %d: no quality profile configured", book_id)
        return JSONResponse(
            status_code=202,
            content=CutoffSearchResponse(
                ok=False,
                found=False,
                message="No fitting release found",
                reason="no quality profile configured for this book",
            ).model_dump(),
        )

    query = f"{book['title']} {' '.join(authors)}".strip()
    client = ProwlarrClient(base_url=indexer.url, api_key=indexer.api_key or None)
    releases = await client.search(query, limit=50)
    log.info("Cutoff search for book %d (%r) -> %d result(s)", book_id, query, len(releases))

    fitting: list[tuple[int, int, dict]] = []
    for release in releases:
        inferred = infer_quality_from_name(release.get("title") or "")
        fit = evaluate_quality_for_profile(inferred, profile, settings.quality_definitions)
        if fit.status not in ("preferred", "accepted"):
            continue
        tier_index = (
            profile.quality_ids.index(fit.matched_quality_id)
            if fit.matched_quality_id in profile.quality_ids
            else len(profile.quality_ids)
        )
        fitting.append((tier_index, -(release.get("seeders") or 0), release))

    if not fitting:
        log.debug(
            "cutoff search for book %d: no fitting release among %d result(s) for profile %r",
            book_id,
            len(releases),
            profile.name,
        )
        return JSONResponse(
            status_code=202,
            content=CutoffSearchResponse(
                ok=False,
                found=False,
                message="No fitting release found",
                reason=f"no release met profile {profile.name!r} cutoff",
            ).model_dump(),
        )

    fitting.sort(key=lambda t: (t[0], t[1]))
    best = fitting[0][2]

    sab = _require_sabnzbd()
    nzb = await client.download_nzb(best["download_url"])
    if nzb is None:
        log.warning("Cutoff search for book %d: NZB fetch from Prowlarr failed", book_id)
        return CutoffSearchResponse(
            ok=False,
            found=True,
            message="Could not fetch the NZB from Prowlarr",
            release_title=best.get("title"),
        )

    sab_client = SABnzbdClient(base_url=sab.base_url(), api_key=sab.api_key or None)
    nzo_id = await sab_client.add_nzb(nzb, best.get("title") or query, sab.category)
    if nzo_id is None:
        log.warning("Cutoff search for book %d: SABnzbd rejected the NZB", book_id)
        return CutoffSearchResponse(
            ok=False,
            found=True,
            message="SABnzbd did not accept the NZB",
            release_title=best.get("title"),
        )

    log.info(
        "Cutoff search for book %d: grabbed %r -> SABnzbd nzo_id %s (category=%s)",
        book_id,
        best.get("title"),
        nzo_id,
        sab.category,
    )
    return CutoffSearchResponse(
        ok=True,
        found=True,
        message=f"Sent to SABnzbd (category {sab.category})",
        release_title=best.get("title"),
        nzo_id=nzo_id,
    )
