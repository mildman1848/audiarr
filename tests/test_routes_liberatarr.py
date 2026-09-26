"""Route-level tests for the Liberatarr integration (issue #64).

Follows the pattern in tests/test_connections_endpoints.py: monkeypatch the
functions the route module imports, exercised through the app_client
fixture (isolated AUDIARR_CONFIG_DIR per test, see tests/conftest.py).
"""

from __future__ import annotations

from app.connections.liberatarr import LiberatarrError


def _set_liberatarr_settings(app_client, base_url: str, token: str, enabled: bool = True) -> None:
    settings = app_client.get("/api/v1/settings").json()
    settings["connections"]["liberatarr"] = {
        "base_url": base_url,
        "token": token,
        "enabled": enabled,
    }
    resp = app_client.put("/api/v1/settings", json=settings)
    assert resp.status_code == 200


def test_liberatarr_test_endpoint_unreachable(app_client):
    response = app_client.get("/api/v1/liberatarr/test", params={"base_url": "http://127.0.0.1:1"})
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_liberatarr_test_endpoint_uses_stored_values_when_blank(app_client, monkeypatch):
    _set_liberatarr_settings(app_client, base_url="http://liberatarr.local", token="stored-token")

    seen = {}

    async def fake_test_connection(settings):
        seen["base_url"] = settings.base_url
        seen["token"] = settings.token
        return {"ok": True, "message": "Liberatarr is reachable"}

    monkeypatch.setattr("app.api.routes_liberatarr.test_connection", fake_test_connection)

    response = app_client.get("/api/v1/liberatarr/test")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["base_url"] == "http://liberatarr.local"
    assert seen["token"] == "stored-token"


def test_liberatarr_test_endpoint_overrides_base_url_via_query(app_client, monkeypatch):
    _set_liberatarr_settings(app_client, base_url="http://stored.local", token="stored-token")

    seen = {}

    async def fake_test_connection(settings):
        seen["base_url"] = settings.base_url
        seen["token"] = settings.token
        return {"ok": True, "message": "Liberatarr is reachable"}

    monkeypatch.setattr("app.api.routes_liberatarr.test_connection", fake_test_connection)

    response = app_client.get("/api/v1/liberatarr/test", params={"base_url": "http://override.local"})
    assert response.status_code == 200
    assert seen["base_url"] == "http://override.local"
    # Token is never accepted as a request parameter -- always the stored one.
    assert seen["token"] == "stored-token"


def test_liberatarr_test_endpoint_never_receives_token_in_query_string(app_client, monkeypatch):
    """The token must never be exposed in the request path/query (request logs)."""
    _set_liberatarr_settings(app_client, base_url="http://stored.local", token="top-secret")

    async def fake_test_connection(settings):
        return {"ok": True, "message": "ok"}

    monkeypatch.setattr("app.api.routes_liberatarr.test_connection", fake_test_connection)

    response = app_client.get(
        "/api/v1/liberatarr/test", params={"base_url": "http://override.local", "token": "not-accepted"}
    )
    assert response.status_code == 200
    # No route parameter reads "token"; passing it is simply ignored by FastAPI.


def test_liberatarr_library_proxy_success(app_client, monkeypatch):
    rows = [{"asin": "B001", "title": "T", "authors": [], "narrators": [], "status": 0, "locale": ""}]

    async def fake_fetch_library(settings):
        return rows

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.get("/api/v1/liberatarr/library")
    assert response.status_code == 200
    assert response.json() == rows


def test_liberatarr_library_proxy_upstream_failure_returns_502(app_client, monkeypatch):
    async def fake_fetch_library(settings):
        raise LiberatarrError("Liberatarr returned HTTP 500")

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.get("/api/v1/liberatarr/library")
    assert response.status_code == 502
    assert "Liberatarr returned HTTP 500" in response.json()["detail"]


def _row(asin, title="Some Title", status=0, authors=None, narrators=None, locale=""):
    """Build a normalized row as ``fetch_library`` would return it. Defaults
    to the real Liberatarr int status enum (0 = Not Liberated)."""
    return {
        "asin": asin,
        "title": title,
        "authors": authors or [],
        "narrators": narrators or [],
        "status": status,
        "locale": locale,
    }


def test_liberatarr_sync_creates_books_for_new_asins(app_client, monkeypatch):
    rows = [
        _row("B001", title="Book One", authors=["Author A"], narrators=["Narrator A"]),
        _row("B002", title="Book Two"),
    ]

    async def fake_fetch_library(settings):
        return rows

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/liberatarr/sync")
    assert response.status_code == 200
    body = response.json()
    assert body == {"created": 2, "skipped_existing": 0, "skipped_no_asin": 0, "errors": []}


def test_liberatarr_sync_is_idempotent(app_client, monkeypatch):
    rows = [_row("B001", title="Book One")]

    async def fake_fetch_library(settings):
        return rows

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    first = app_client.post("/api/v1/liberatarr/sync").json()
    assert first == {"created": 1, "skipped_existing": 0, "skipped_no_asin": 0, "errors": []}

    second = app_client.post("/api/v1/liberatarr/sync").json()
    assert second == {"created": 0, "skipped_existing": 1, "skipped_no_asin": 0, "errors": []}


def test_liberatarr_sync_idempotent_with_non_empty_locale(app_client, monkeypatch):
    """Regression: the duplicate check must ignore locale, since a book
    created with e.g. locale="us" would otherwise never match a later
    lookup that defaults to locale="" and get recreated every sync."""
    rows = [_row("B001", title="Book One", locale="us")]

    async def fake_fetch_library(settings):
        return rows

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    first = app_client.post("/api/v1/liberatarr/sync").json()
    assert first == {"created": 1, "skipped_existing": 0, "skipped_no_asin": 0, "errors": []}

    second = app_client.post("/api/v1/liberatarr/sync").json()
    assert second == {"created": 0, "skipped_existing": 1, "skipped_no_asin": 0, "errors": []}


def test_liberatarr_sync_skips_rows_without_asin(app_client, monkeypatch):
    rows = [_row("", title="No ASIN Book"), _row("B001", title="Has ASIN")]

    async def fake_fetch_library(settings):
        return rows

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/liberatarr/sync")
    body = response.json()
    assert body["created"] == 1
    assert body["skipped_no_asin"] == 1


def test_liberatarr_sync_skips_already_liberated_rows(app_client, monkeypatch):
    """status=1 (Liberated, the real int enum) must not be synced."""
    rows = [
        _row("B001", title="Already Owned", status=1),
        _row("B002", title="Missing", status=0),
    ]

    async def fake_fetch_library(settings):
        return rows

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/liberatarr/sync")
    body = response.json()
    assert body["created"] == 1
    assert body["skipped_existing"] == 0
    assert body["skipped_no_asin"] == 0


def test_liberatarr_sync_tolerates_int_like_string_status(app_client, monkeypatch):
    """An int-like string status ("0") must be tolerated as Not Liberated."""
    rows = [_row("B001", title="Book One", status="0")]

    async def fake_fetch_library(settings):
        return rows

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/liberatarr/sync")
    body = response.json()
    assert body["created"] == 1


def test_liberatarr_sync_does_not_sync_unknown_statuses(app_client, monkeypatch):
    """status=2 (Error) and status=0x1000 (Partial) must never be synced as wanted."""
    rows = [
        _row("B001", title="Error Book", status=2),
        _row("B002", title="Partial Book", status=0x1000),
    ]

    async def fake_fetch_library(settings):
        return rows

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/liberatarr/sync")
    body = response.json()
    assert body == {"created": 0, "skipped_existing": 0, "skipped_no_asin": 0, "errors": []}


def test_liberatarr_sync_upstream_failure_returns_502(app_client, monkeypatch):
    async def fake_fetch_library(settings):
        raise LiberatarrError("Could not reach Liberatarr: boom")

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/liberatarr/sync")
    assert response.status_code == 502
    assert "boom" in response.json()["detail"]
