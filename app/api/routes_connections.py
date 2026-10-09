"""Connection test endpoints for outbound integrations."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_settings
from app.connect import dispatch_event
from app.connections.audiobookshelf import AudiobookshelfClient
from app.connections.m4b_convertarr import M4BConvertarrClient
from app.connections.prowlarr import ProwlarrClient
from app.connections.qbittorrent import QBittorrentClient, QBittorrentError
from app.connections.sabnzbd import SABnzbdClient

log = logging.getLogger("audiarr.api.connections")

router = APIRouter()


class ConnectionTestRequest(BaseModel):
    url: str
    api_key: str | None = None


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str


@router.post("/api/v1/connections/audiobookshelf/test", response_model=ConnectionTestResponse)
async def test_audiobookshelf(request: ConnectionTestRequest) -> ConnectionTestResponse:
    client = AudiobookshelfClient(base_url=request.url, api_key=request.api_key)
    ok = await client.health()
    log.info("Audiobookshelf connection test to %s -> %s", request.url, ok)
    message = "Audiobookshelf reachable" if ok else "Could not reach Audiobookshelf"
    if not ok:
        await dispatch_event(
            "health_issue",
            {"integration": "Audiobookshelf", "url": request.url, "message": message},
        )
    return ConnectionTestResponse(ok=ok, message=message)


@router.post("/api/v1/connections/audiobookshelf/scan", response_model=ConnectionTestResponse)
async def scan_audiobookshelf() -> ConnectionTestResponse:
    """Trigger a rescan of the configured Audiobookshelf library.

    Uses the persisted connection settings (url, api_key, library_id).
    The scan call is fire-and-confirm: a 200/202 from Audiobookshelf
    counts as success.
    """
    conn = load_settings().connections.audiobookshelf
    if not conn.enabled:
        raise HTTPException(400, "Audiobookshelf connection is disabled")
    if not conn.library_id:
        raise HTTPException(400, "No Audiobookshelf library_id configured")

    client = AudiobookshelfClient(base_url=conn.url, api_key=conn.api_key or None)
    ok = await client.scan_library(conn.library_id)
    log.info("Audiobookshelf scan trigger for library %s -> %s", conn.library_id, ok)
    return ConnectionTestResponse(
        ok=ok,
        message=(
            "Audiobookshelf library scan triggered"
            if ok
            else "Audiobookshelf did not accept the scan request"
        ),
    )


@router.post("/api/v1/connections/m4b-convertarr/test", response_model=ConnectionTestResponse)
async def test_m4b_convertarr(request: ConnectionTestRequest) -> ConnectionTestResponse:
    client = M4BConvertarrClient(base_url=request.url, api_key=request.api_key)
    ok = await client.health()
    log.info("m4b-convertarr connection test to %s -> %s", request.url, ok)
    message = "m4b-convertarr reachable" if ok else "Could not reach m4b-convertarr"
    if not ok:
        await dispatch_event(
            "health_issue",
            {"integration": "m4b-convertarr", "url": request.url, "message": message},
        )
    return ConnectionTestResponse(ok=ok, message=message)


def _stored_sabnzbd_values(url: str, api_key: str | None) -> tuple[str, str | None]:
    """Fill missing SABnzbd test values from persisted settings.

    The browser intentionally does not render saved API keys back into the
    password field. Without this fallback, a saved connection would fail its
    next Test click unless the user pasted the key again.
    """
    if url and api_key:
        return url, api_key

    clients = [c for c in load_settings().download_clients if c.type == "sabnzbd"]
    if url:
        # Never hand a stored key to a URL that is not the configured client.
        match = next((c for c in clients if c.base_url().rstrip("/") == url.rstrip("/")), None)
    else:
        match = clients[0] if clients else None
    if match is None:
        return url, api_key
    return url or match.base_url(), api_key or match.api_key or None


def _stored_prowlarr_values(url: str, api_key: str | None) -> tuple[str, str | None]:
    """Fill missing Prowlarr test values from persisted settings."""
    if url and api_key:
        return url, api_key

    indexers = [i for i in load_settings().indexers if i.type == "prowlarr"]
    if url:
        # Never hand a stored key to a URL that is not the configured indexer.
        match = next((i for i in indexers if i.url.rstrip("/") == url.rstrip("/")), None)
    else:
        match = indexers[0] if indexers else None
    if match is None:
        return url, api_key
    return url or match.url.rstrip("/"), api_key or match.api_key or None


@router.post("/api/v1/connections/sabnzbd/test", response_model=ConnectionTestResponse)
async def test_sabnzbd(request: ConnectionTestRequest) -> ConnectionTestResponse:
    """Probe a SABnzbd download client via its ``mode=version`` API call."""
    url, api_key = _stored_sabnzbd_values(request.url, request.api_key)
    client = SABnzbdClient(base_url=url, api_key=api_key)
    version = await client.version()
    ok = version is not None
    log.info("SABnzbd connection test to %s -> %s", url, ok)
    message = f"SABnzbd {version}" if ok else "Could not reach SABnzbd"
    if not ok:
        await dispatch_event(
            "health_issue", {"integration": "SABnzbd", "url": url, "message": message}
        )
    return ConnectionTestResponse(ok=ok, message=message)


class QBittorrentTestRequest(BaseModel):
    """qBittorrent test input: an API key, or legacy username/password."""

    url: str
    api_key: str | None = None
    username: str | None = None
    password: str | None = None


def _stored_qbittorrent_values(
    request: QBittorrentTestRequest,
) -> tuple[str | None, str | None, str | None]:
    """Fill blank qBittorrent test secrets from the stored client at the SAME URL.

    Unlike the SABnzbd fallback, a stored secret is only ever reused for the
    URL it was saved with, so a test can't be pointed at another host to
    collect it. An explicit API key in the request means API-key mode: stored
    legacy credentials are not mixed in.
    """
    api_key, username, password = request.api_key, request.username, request.password
    if api_key:
        return api_key, None, None
    wanted = request.url.rstrip("/")
    stored = next(
        (
            c
            for c in load_settings().download_clients
            if c.type == "qbittorrent" and c.base_url() == wanted
        ),
        None,
    )
    if stored is None:
        return None, username, password
    if not username and not password:
        return stored.api_key or None, stored.username or None, stored.password or None
    if username and not password and username == stored.username:
        return None, username, stored.password or None
    return None, username, password


@router.post("/api/v1/connections/qbittorrent/test", response_model=ConnectionTestResponse)
async def test_qbittorrent(request: QBittorrentTestRequest) -> ConnectionTestResponse:
    """Probe a qBittorrent client via ``/api/v2/app/version`` (never logs secrets)."""
    api_key, username, password = _stored_qbittorrent_values(request)
    url = request.url.rstrip("/")
    client = QBittorrentClient(
        base_url=url, api_key=api_key, username=username, password=password
    )
    try:
        version = await client.version()
    except QBittorrentError as exc:
        log.info("qBittorrent connection test -> failed (%s)", exc.reason)
        message = str(exc)
        await dispatch_event(
            "health_issue", {"integration": "qBittorrent", "message": message}
        )
        return ConnectionTestResponse(ok=False, message=message)
    log.info("qBittorrent connection test -> ok")
    return ConnectionTestResponse(ok=True, message=f"qBittorrent {version}")


@router.post("/api/v1/connections/prowlarr/test", response_model=ConnectionTestResponse)
async def test_prowlarr(request: ConnectionTestRequest) -> ConnectionTestResponse:
    """Probe a Prowlarr indexer manager via ``/api/v1/system/status``."""
    url, api_key = _stored_prowlarr_values(request.url, request.api_key)
    client = ProwlarrClient(base_url=url, api_key=api_key)
    status = await client.status()
    ok = status is not None
    version = (status or {}).get("version")
    log.info("Prowlarr connection test to %s -> %s", url, ok)
    message = f"Prowlarr {version}".strip() if ok else "Could not reach Prowlarr"
    if not ok:
        await dispatch_event(
            "health_issue", {"integration": "Prowlarr", "url": url, "message": message}
        )
    return ConnectionTestResponse(ok=ok, message=message)
