"""API-level tests for the connection test endpoints.

The unreachable-address tests exercise the failure path without mocks or
network access. The SABnzbd/Prowlarr success paths monkeypatch the client
class with an httpx.MockTransport-backed factory (same pattern as
tests/test_audiobookshelf.py).
"""

from __future__ import annotations

import httpx

from app.connections.prowlarr import ProwlarrClient
from app.connections.sabnzbd import SABnzbdClient


def _factory(real_cls, handler):
    """Return a drop-in for a connection client wired to a MockTransport."""

    def make(base_url: str = "", api_key: str | None = None, client=None):
        url = base_url or "http://mock.local"
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)
        return real_cls(base_url=url, api_key=api_key, client=mock)

    return make


def test_audiobookshelf_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/audiobookshelf/test",
        json={"url": "http://127.0.0.1:1", "api_key": "x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_m4b_convertarr_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/m4b-convertarr/test",
        json={"url": "http://127.0.0.1:1"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_sabnzbd_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://127.0.0.1:1", "api_key": "x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_prowlarr_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://127.0.0.1:1", "api_key": "x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_sabnzbd_test_endpoint_ok(app_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.3.2"})

    monkeypatch.setattr(
        "app.api.routes_connections.SABnzbdClient", _factory(SABnzbdClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://sab.local", "api_key": "x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "4.3.2" in body["message"]


def test_prowlarr_test_endpoint_ok(app_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "1.21.0", "appName": "Prowlarr"})

    monkeypatch.setattr(
        "app.api.routes_connections.ProwlarrClient", _factory(ProwlarrClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://prowlarr.local", "api_key": "x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "1.21.0" in body["message"]


def test_sabnzbd_test_endpoint_uses_stored_api_key_when_blank(app_client, monkeypatch):
    settings = app_client.get("/api/v1/settings").json()
    settings["download_clients"] = [
        {
            "name": "SABnzbd",
            "type": "sabnzbd",
            "url": "http://sab.local",
            "api_key": "stored-sab-key",
            "category": "audiobooks",
            "enabled": True,
        }
    ]
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["apikey"] = request.url.params.get("apikey", "")
        return httpx.Response(200, json={"version": "4.3.2"})

    monkeypatch.setattr(
        "app.api.routes_connections.SABnzbdClient", _factory(SABnzbdClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://sab.local"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["apikey"] == "stored-sab-key"


def test_prowlarr_test_endpoint_uses_stored_api_key_when_blank(app_client, monkeypatch):
    settings = app_client.get("/api/v1/settings").json()
    settings["indexers"] = [
        {
            "name": "Prowlarr",
            "type": "prowlarr",
            "url": "http://prowlarr.local",
            "api_key": "stored-prowlarr-key",
            "enabled": True,
        }
    ]
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["x-api-key"] = request.headers.get("X-Api-Key", "")
        return httpx.Response(200, json={"version": "1.21.0", "appName": "Prowlarr"})

    monkeypatch.setattr(
        "app.api.routes_connections.ProwlarrClient", _factory(ProwlarrClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://prowlarr.local"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["x-api-key"] == "stored-prowlarr-key"
