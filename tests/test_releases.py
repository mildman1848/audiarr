"""Tests for the Arr-core loop backend.

Client-level tests use an injected ``httpx.MockTransport`` (same pattern as
tests/test_connections_prowlarr.py). Endpoint tests monkeypatch the
module-level client symbols in ``app.api.routes_releases`` with a
MockTransport-backed factory (same pattern as
tests/test_connections_endpoints.py).
"""

from __future__ import annotations

import httpx
import pytest

from app.connections.prowlarr import ProwlarrClient
from app.connections.sabnzbd import SABnzbdClient

# ---------------------------------------------------------------------------
# ProwlarrClient.search / download_nzb
# ---------------------------------------------------------------------------

_RAW_RELEASE = {
    "guid": "abc-123",
    "indexerId": 4,
    "indexer": "SomeUsenetIndexer",
    "title": "Some Audiobook Unabridged",
    "size": 734003200,
    "seeders": None,
    "leechers": None,
    "publishDate": "2026-09-01T12:00:00Z",
    "downloadUrl": "https://indexer.example/download/abc-123.nzb",
    "magnetUrl": None,
    "protocol": "usenet",
    "age": 9,
    "categories": [{"id": 3030, "name": "Audio/Audiobook"}, {"id": 3000}],
}


@pytest.mark.asyncio
async def test_search_normalizes_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/search"
        assert request.url.params["query"] == "vorleser"
        assert request.url.params["type"] == "search"
        assert request.url.params["limit"] == "25"
        assert request.headers.get("x-api-key") == "p-key"
        return httpx.Response(200, json=[_RAW_RELEASE])

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://p.local")
    client = ProwlarrClient(base_url="http://p.local", api_key="p-key", client=mock)

    rows = await client.search("vorleser", limit=25)
    await mock.aclose()

    assert len(rows) == 1
    row = rows[0]
    assert row == {
        "guid": "abc-123",
        "indexer_id": 4,
        "indexer": "SomeUsenetIndexer",
        "title": "Some Audiobook Unabridged",
        "size": 734003200,
        "seeders": None,
        "leechers": None,
        "publish_date": "2026-09-01T12:00:00Z",
        "download_url": "https://indexer.example/download/abc-123.nzb",
        "magnet_url": None,
        "protocol": "usenet",
        "age": 9,
        "categories": ["Audio/Audiobook"],
    }


@pytest.mark.asyncio
async def test_search_returns_empty_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://p.local")
    client = ProwlarrClient(base_url="http://p.local", api_key="p-key", client=mock)

    assert await client.search("x") == []
    await mock.aclose()


@pytest.mark.asyncio
async def test_search_returns_empty_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://p.local")
    client = ProwlarrClient(base_url="http://p.local", client=mock)

    assert await client.search("x") == []
    await mock.aclose()


_PROXY_URL = "http://p.local/1/download?apikey=p-key&link=Zm9vYmFy&file=book.nzb"


@pytest.mark.asyncio
async def test_download_nzb_returns_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/1/download")
        return httpx.Response(200, content=b"<nzb>payload</nzb>")

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://p.local")
    client = ProwlarrClient(base_url="http://p.local", api_key="p-key", client=mock)

    assert await client.download_nzb(_PROXY_URL) == b"<nzb>payload</nzb>"
    await mock.aclose()


@pytest.mark.asyncio
async def test_download_nzb_none_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://p.local")
    client = ProwlarrClient(base_url="http://p.local", client=mock)

    assert await client.download_nzb(_PROXY_URL) is None
    await mock.aclose()


@pytest.mark.asyncio
async def test_download_nzb_none_on_magnet_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"magnet:?xt=urn:btih:deadbeef&dn=book")

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://p.local")
    client = ProwlarrClient(base_url="http://p.local", client=mock)

    assert await client.download_nzb(_PROXY_URL) is None
    await mock.aclose()


# ---------------------------------------------------------------------------
# SABnzbdClient.add_nzb / queue / history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_nzb_returns_nzo_id():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api"
        assert request.url.params["mode"] == "addfile"
        assert request.url.params["apikey"] == "sab-key"
        assert request.url.params["cat"] == "audiobooks"
        body = request.content
        seen["has_nzbfile"] = b'name="nzbfile"' in body
        seen["has_filename"] = b"Der Vorleser.nzb" in body
        seen["has_payload"] = b"<nzb>payload</nzb>" in body
        return httpx.Response(200, json={"status": True, "nzo_ids": ["SABnzbd_nzo_abc"]})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sab.local")
    client = SABnzbdClient(base_url="http://sab.local", api_key="sab-key", client=mock)

    nzo_id = await client.add_nzb(b"<nzb>payload</nzb>", "Der Vorleser", "audiobooks")
    await mock.aclose()

    assert nzo_id == "SABnzbd_nzo_abc"
    assert seen == {"has_nzbfile": True, "has_filename": True, "has_payload": True}


@pytest.mark.asyncio
async def test_add_nzb_none_when_status_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": False, "error": "nope"})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sab.local")
    client = SABnzbdClient(base_url="http://sab.local", api_key="sab-key", client=mock)

    assert await client.add_nzb(b"x", "T", "audiobooks") is None
    await mock.aclose()


@pytest.mark.asyncio
async def test_add_nzb_none_when_no_nzo_ids():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": True, "nzo_ids": []})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sab.local")
    client = SABnzbdClient(base_url="http://sab.local", client=mock)

    assert await client.add_nzb(b"x", "T", "audiobooks") is None
    await mock.aclose()


@pytest.mark.asyncio
async def test_queue_normalization():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "queue"
        return httpx.Response(
            200,
            json={
                "queue": {
                    "status": "Downloading",
                    "slots": [
                        {
                            "nzo_id": "nzo_1",
                            "filename": "Book One",
                            "status": "Downloading",
                            "size": "700 MB",
                            "sizeleft": "120 MB",
                            "timeleft": "0:03:20",
                            "percentage": "82",
                            "category": "audiobooks",
                            "index": 0,
                        }
                    ],
                }
            },
        )

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sab.local")
    client = SABnzbdClient(base_url="http://sab.local", api_key="sab-key", client=mock)

    slots = await client.queue()
    await mock.aclose()

    assert slots == [
        {
            "nzo_id": "nzo_1",
            "filename": "Book One",
            "status": "Downloading",
            "size": "700 MB",
            "size_left": "120 MB",
            "time_left": "0:03:20",
            "progress_percent": 82.0,
            "category": "audiobooks",
        }
    ]


@pytest.mark.asyncio
async def test_history_normalization():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "history"
        assert request.url.params["limit"] == "10"
        return httpx.Response(
            200,
            json={
                "history": {
                    "total": 1,
                    "slots": [
                        {
                            "nzo_id": "nzo_9",
                            "name": "Finished Book",
                            "status": "Completed",
                            "size": "512 MB",
                            "category": "audiobooks",
                            "completed": 1725100000,
                            "nzb_name": "finished.nzb",
                        }
                    ],
                }
            },
        )

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://sab.local")
    client = SABnzbdClient(base_url="http://sab.local", api_key="sab-key", client=mock)

    slots = await client.history(limit=10)
    await mock.aclose()

    assert slots == [
        {
            "nzo_id": "nzo_9",
            "name": "Finished Book",
            "status": "Completed",
            "size": "512 MB",
            "category": "audiobooks",
            "completed_at": 1725100000,
        }
    ]


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------


def _factory(real_cls, handler):
    """Drop-in replacement for a connection client, wired to a MockTransport."""

    def make(base_url: str = "", api_key: str | None = None, client=None):
        url = base_url or "http://mock.local"
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)
        return real_cls(base_url=url, api_key=api_key, client=mock)

    return make


def _configure(app_client, *, prowlarr: bool = True, sabnzbd: bool = True) -> None:
    from app.config import load_settings, save_settings
    from app.models.settings import DownloadClient, Indexer

    settings = load_settings()
    settings.indexers = (
        [
            Indexer(
                name="Prowlarr",
                type="prowlarr",
                url="http://prowlarr.local",
                api_key="p-key",
                enabled=True,
            )
        ]
        if prowlarr
        else []
    )
    settings.download_clients = (
        [
            DownloadClient(
                name="SABnzbd",
                type="sabnzbd",
                url="http://sab.local",
                api_key="sab-key",
                category="audiobooks",
                enabled=True,
            )
        ]
        if sabnzbd
        else []
    )
    save_settings(settings)


def test_search_endpoint_returns_rows(app_client, monkeypatch):
    _configure(app_client)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_RAW_RELEASE])

    monkeypatch.setattr(
        "app.api.routes_releases.ProwlarrClient", _factory(ProwlarrClient, handler)
    )

    resp = app_client.get("/api/v1/releases/search", params={"query": "vorleser", "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_results"] == 1
    assert body["indexer"] == "Prowlarr"
    assert body["releases"][0]["guid"] == "abc-123"
    assert body["releases"][0]["categories"] == ["Audio/Audiobook"]


def test_search_endpoint_503_without_prowlarr(app_client):
    _configure(app_client, prowlarr=False)
    resp = app_client.get("/api/v1/releases/search", params={"query": "x"})
    assert resp.status_code == 503


def test_grab_endpoint_success(app_client, monkeypatch):
    _configure(app_client)

    def prowlarr_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith("/1/download")
        return httpx.Response(200, content=b"<nzb>payload</nzb>")

    def sab_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "addfile"
        assert request.url.params["cat"] == "audiobooks"
        assert b"<nzb>payload</nzb>" in request.content
        return httpx.Response(200, json={"status": True, "nzo_ids": ["nzo_grabbed"]})

    monkeypatch.setattr(
        "app.api.routes_releases.ProwlarrClient", _factory(ProwlarrClient, prowlarr_handler)
    )
    monkeypatch.setattr(
        "app.api.routes_releases.SABnzbdClient", _factory(SABnzbdClient, sab_handler)
    )

    resp = app_client.post(
        "/api/v1/releases/grab",
        json={
            "indexer_id": 4,
            "guid": "abc-123",
            "download_url": "http://prowlarr.local/1/download?apikey=p-key&link=Zm9v&file=d.nzb",
            "title": "Der Vorleser",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["nzo_id"] == "nzo_grabbed"


def test_grab_endpoint_failure_when_nzb_fetch_returns_none(app_client, monkeypatch):
    _configure(app_client)

    def prowlarr_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    def sab_handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("SABnzbd must not be called when the NZB fetch fails")

    monkeypatch.setattr(
        "app.api.routes_releases.ProwlarrClient", _factory(ProwlarrClient, prowlarr_handler)
    )
    monkeypatch.setattr(
        "app.api.routes_releases.SABnzbdClient", _factory(SABnzbdClient, sab_handler)
    )

    resp = app_client.post(
        "/api/v1/releases/grab",
        json={
            "indexer_id": 4,
            "guid": "abc-123",
            "download_url": "http://prowlarr.local/1/download?apikey=p-key&link=Zm9v&file=d.nzb",
            "title": "Der Vorleser",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["nzo_id"] is None
    assert "Prowlarr" in body["message"]


def test_queue_endpoint(app_client, monkeypatch):
    _configure(app_client)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "queue": {
                    "slots": [
                        {
                            "nzo_id": "nzo_1",
                            "filename": "Book One",
                            "status": "Downloading",
                            "size": "700 MB",
                            "sizeleft": "120 MB",
                            "timeleft": "0:03:20",
                            "percentage": "82",
                            "category": "audiobooks",
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(
        "app.api.routes_releases.SABnzbdClient", _factory(SABnzbdClient, handler)
    )

    resp = app_client.get("/api/v1/activity/queue")
    assert resp.status_code == 200
    slots = resp.json()["slots"]
    assert slots[0]["nzo_id"] == "nzo_1"
    assert slots[0]["size_left"] == "120 MB"
    assert slots[0]["progress_percent"] == 82.0


def test_history_endpoint(app_client, monkeypatch):
    _configure(app_client)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "50"
        return httpx.Response(
            200,
            json={
                "history": {
                    "slots": [
                        {
                            "nzo_id": "nzo_9",
                            "name": "Finished Book",
                            "status": "Completed",
                            "size": "512 MB",
                            "category": "audiobooks",
                            "completed": 1725100000,
                        }
                    ]
                }
            },
        )

    monkeypatch.setattr(
        "app.api.routes_releases.SABnzbdClient", _factory(SABnzbdClient, handler)
    )

    resp = app_client.get("/api/v1/activity/history")
    assert resp.status_code == 200
    slots = resp.json()["slots"]
    assert slots[0]["name"] == "Finished Book"
    assert slots[0]["completed_at"] == 1725100000


def test_queue_endpoint_503_without_sabnzbd(app_client):
    _configure(app_client, sabnzbd=False)
    resp = app_client.get("/api/v1/activity/queue")
    assert resp.status_code == 503
