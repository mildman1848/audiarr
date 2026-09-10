"""Audiobookshelf integration: scan endpoint + auto-refresh helper/hooks.

The scan endpoint and the ``notify_library_changed`` helper both build an
``AudiobookshelfClient`` internally, so tests swap the class for a factory
that injects an ``httpx.MockTransport`` — the same MockTransport pattern
used in tests/test_connections_audiobookshelf.py.
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from app.connections.audiobookshelf import AudiobookshelfClient, notify_library_changed


def _mock_client_factory(handler):
    """Return a drop-in for AudiobookshelfClient wired to a MockTransport."""
    real_cls = AudiobookshelfClient

    def factory(base_url: str = "", api_key: str | None = None, client=None):
        url = base_url or "http://abs.local"
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)
        return real_cls(base_url=url, api_key=api_key, client=mock)

    return factory


def _configure_abs(
    enabled: bool = True,
    library_id: str = "lib-42",
    url: str = "http://abs.local",
    api_key: str = "secret-token",
) -> None:
    from app.config import load_settings, save_settings

    settings = load_settings()
    settings.connections.audiobookshelf.enabled = enabled
    settings.connections.audiobookshelf.library_id = library_id
    settings.connections.audiobookshelf.url = url
    settings.connections.audiobookshelf.api_key = api_key
    save_settings(settings)


# ---------------------------------------------------------------------------
# Scan endpoint
# ---------------------------------------------------------------------------

def test_scan_endpoint_400_when_disabled(app_client):
    _configure_abs(enabled=False, library_id="lib-42")
    resp = app_client.post("/api/v1/connections/audiobookshelf/scan")
    assert resp.status_code == 400


def test_scan_endpoint_400_when_library_id_empty(app_client):
    _configure_abs(enabled=True, library_id="")
    resp = app_client.post("/api/v1/connections/audiobookshelf/scan")
    assert resp.status_code == 400


def test_scan_endpoint_ok_true_on_200(app_client, monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    monkeypatch.setattr(
        "app.api.routes_connections.AudiobookshelfClient", _mock_client_factory(handler)
    )
    _configure_abs(enabled=True, library_id="lib-42")

    resp = app_client.post("/api/v1/connections/audiobookshelf/scan")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert calls == ["/api/libraries/lib-42/scan"]


# ---------------------------------------------------------------------------
# notify_library_changed helper
# ---------------------------------------------------------------------------

async def test_helper_no_call_when_disabled(app_client, monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    monkeypatch.setattr(
        "app.connections.audiobookshelf.AudiobookshelfClient", _mock_client_factory(handler)
    )
    _configure_abs(enabled=False, library_id="lib-42")

    assert await notify_library_changed() is False
    assert calls == []


async def test_helper_no_call_when_library_id_empty(app_client, monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    monkeypatch.setattr(
        "app.connections.audiobookshelf.AudiobookshelfClient", _mock_client_factory(handler)
    )
    _configure_abs(enabled=True, library_id="")

    assert await notify_library_changed() is False
    assert calls == []


async def test_helper_posts_scan_with_bearer_when_enabled(app_client, monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200)

    monkeypatch.setattr(
        "app.connections.audiobookshelf.AudiobookshelfClient", _mock_client_factory(handler)
    )
    _configure_abs(enabled=True, library_id="lib-42", api_key="secret-token")

    assert await notify_library_changed() is True
    assert seen["path"] == "/api/libraries/lib-42/scan"
    assert seen["auth"] == "Bearer secret-token"


async def test_helper_swallows_httpx_errors(app_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(
        "app.connections.audiobookshelf.AudiobookshelfClient", _mock_client_factory(handler)
    )
    _configure_abs(enabled=True, library_id="lib-42")

    # Must not raise.
    assert await notify_library_changed() is False


# ---------------------------------------------------------------------------
# Webhook completion hook
# ---------------------------------------------------------------------------

def _seed_running_job(title: str = "Der Vorleser") -> tuple[int, int]:
    from app.config import get_db_path
    from app.db import migrate

    migrate()
    conn = sqlite3.connect(get_db_path())
    try:
        cur = conn.execute(
            "INSERT INTO books (title, language) VALUES (?, 'de')", (title,)
        )
        book_id = int(cur.lastrowid or 0)
        cur = conn.execute(
            """INSERT INTO conversion_jobs (book_id, source_path, status, backend)
               VALUES (?, '/tmp/src', 'running', 'm4b-convertarr')""",
            (book_id,),
        )
        job_id = int(cur.lastrowid or 0)
        conn.commit()
        return book_id, job_id
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_webhook_completion_triggers_abs_scan(app_client, tmp_path, monkeypatch):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200)

    monkeypatch.setattr(
        "app.connections.audiobookshelf.AudiobookshelfClient", _mock_client_factory(handler)
    )
    _configure_abs(enabled=True, library_id="lib-42")

    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"\x00" * 128)
    _seed_running_job(title="Der Vorleser")

    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={"converted_path": str(m4b), "title": "Der Vorleser"},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    assert calls == ["/api/libraries/lib-42/scan"]
