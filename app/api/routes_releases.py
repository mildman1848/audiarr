"""Arr-core loop backend: release search, grab, and Activity views.

This wires the classic *arr automation loop:

1. interactive release search via Prowlarr
2. grab a release — pull the NZB through Prowlarr, hand it to SABnzbd
   under the configured category
3. Activity: Queue — the live SABnzbd queue
4. Activity: History — the SABnzbd history

All outbound clients are constructed from the enabled entries in the
persisted settings (``indexers`` / ``download_clients``). Missing config
yields a 503 so the UI can prompt the user to finish setup.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_settings
from app.connections.prowlarr import ProwlarrClient
from app.connections.sabnzbd import SABnzbdClient
from app.models.settings import DownloadClient, Indexer

log = logging.getLogger("audiarr.api.releases")

router = APIRouter()


class ReleaseRow(BaseModel):
    """A normalized Prowlarr release, mirrors ProwlarrClient.search()."""

    guid: str | None = None
    indexer_id: int | None = None
    indexer: str | None = None
    title: str | None = None
    size: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    publish_date: str | None = None
    download_url: str | None = None
    magnet_url: str | None = None
    protocol: str | None = None
    age: int | None = None
    categories: list[str] = []


class ReleaseSearchResponse(BaseModel):
    releases: list[ReleaseRow]
    total_results: int
    indexer: str = "Prowlarr"


class GrabRequest(BaseModel):
    indexer_id: int
    guid: str
    download_url: str
    title: str


class GrabResponse(BaseModel):
    ok: bool
    message: str
    nzo_id: str | None = None


class QueueResponse(BaseModel):
    slots: list[dict]


class HistoryResponse(BaseModel):
    slots: list[dict]


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


@router.get("/api/v1/releases/search", response_model=ReleaseSearchResponse)
async def search_releases(query: str, limit: int = 50) -> ReleaseSearchResponse:
    """Interactive release search against the configured Prowlarr."""
    indexer = _require_prowlarr()
    client = ProwlarrClient(base_url=indexer.url, api_key=indexer.api_key or None)
    releases = await client.search(query, limit=limit)
    log.info("Release search for %r -> %d result(s)", query, len(releases))
    return ReleaseSearchResponse(
        releases=[ReleaseRow(**row) for row in releases],
        total_results=len(releases),
    )


@router.post("/api/v1/releases/grab", response_model=GrabResponse)
async def grab_release(request: GrabRequest) -> GrabResponse:
    """Grab a release: fetch its NZB via Prowlarr and push it to SABnzbd."""
    indexer = _require_prowlarr()
    sab = _require_sabnzbd()

    prowlarr = ProwlarrClient(base_url=indexer.url, api_key=indexer.api_key or None)
    # The search result's download_url is already a self-contained Prowlarr
    # proxy URL, so no indexer_id is needed for the fetch; we keep it in the
    # request for forward-compat and log it.
    log.debug("Grab %r: indexer_id=%s", request.title, request.indexer_id)
    nzb = await prowlarr.download_nzb(request.download_url)
    if nzb is None:
        log.warning("Grab %r: NZB fetch from Prowlarr failed", request.title)
        return GrabResponse(
            ok=False,
            message="Could not fetch the NZB from Prowlarr",
            nzo_id=None,
        )

    sab_client = SABnzbdClient(base_url=sab.base_url(), api_key=sab.api_key or None)
    nzo_id = await sab_client.add_nzb(nzb, request.title, sab.category)
    if nzo_id is None:
        log.warning("Grab %r: SABnzbd rejected the NZB", request.title)
        return GrabResponse(
            ok=False,
            message="SABnzbd did not accept the NZB",
            nzo_id=None,
        )

    log.info(
        "Grab %r -> SABnzbd nzo_id %s (category=%s)", request.title, nzo_id, sab.category
    )
    return GrabResponse(
        ok=True,
        message=f"Sent to SABnzbd (category {sab.category})",
        nzo_id=nzo_id,
    )


@router.get("/api/v1/activity/queue", response_model=QueueResponse)
async def activity_queue() -> QueueResponse:
    """Live SABnzbd download queue."""
    sab = _require_sabnzbd()
    client = SABnzbdClient(base_url=sab.base_url(), api_key=sab.api_key or None)
    return QueueResponse(slots=await client.queue())


@router.get("/api/v1/activity/history", response_model=HistoryResponse)
async def activity_history(limit: int = 50) -> HistoryResponse:
    """Recent SABnzbd download history."""
    sab = _require_sabnzbd()
    client = SABnzbdClient(base_url=sab.base_url(), api_key=sab.api_key or None)
    return HistoryResponse(slots=await client.history(limit=limit))
