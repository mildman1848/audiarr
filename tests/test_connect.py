"""Outbound webhook dispatch (issue #28): app.connect + the Connect test
endpoint.

Delivery tests monkeypatch ``app.connect._new_client`` -- a small seam
app.connect calls instead of ``httpx.AsyncClient()`` directly -- with a
factory that returns an ``httpx.MockTransport``-backed client (same
MockTransport pattern used throughout tests/test_connections_*.py).
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import load_settings, save_settings
from app.connect import dispatch_event, send_test_event
from app.models.settings import ConnectNotification


def _mock_async_client_factory(handler):
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def _add_webhook(app_client, **overrides) -> str:
    settings = load_settings()
    notif = ConnectNotification(
        id=overrides.pop("id", "wh-1"),
        name=overrides.pop("name", "My Webhook"),
        url=overrides.pop("url", "http://hooks.local/audiarr"),
        enabled=overrides.pop("enabled", True),
        **overrides,
    )
    settings.connect.append(notif)
    save_settings(settings)
    return notif.id


# ---------------------------------------------------------------------------
# dispatch_event
# ---------------------------------------------------------------------------


async def test_dispatch_sends_expected_payload_and_header(app_client, monkeypatch):
    notif_id = _add_webhook(
        app_client, header_name="X-Api-Key", header_value="s3cr3t", on_grab=True
    )

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["header"] = request.headers.get("X-Api-Key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))

    await dispatch_event("grab", {"title": "Der Vorleser", "nzo_id": "nzo_1"})

    assert seen["url"] == "http://hooks.local/audiarr"
    assert seen["header"] == "s3cr3t"
    assert seen["body"]["event"] == "grab"
    assert seen["body"]["app"] == "Audiarr"
    assert "timestamp" in seen["body"]
    assert seen["body"]["data"] == {"title": "Der Vorleser", "nzo_id": "nzo_1"}

    updated = next(n for n in load_settings().connect if n.id == notif_id)
    assert updated.last_event == "grab"
    assert updated.last_status == "delivered"
    assert updated.last_status_code == 200
    assert updated.last_error == ""
    assert updated.last_delivered_at


async def test_dispatch_omits_header_when_only_one_field_set(app_client, monkeypatch):
    _add_webhook(app_client, header_name="X-Api-Key", header_value="", on_grab=True)

    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-Api-Key" not in request.headers
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    await dispatch_event("grab", {"title": "x"})


async def test_dispatch_retries_once_on_failure_then_succeeds(app_client, monkeypatch):
    notif_id = _add_webhook(app_client, on_grab=True)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(500)
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    await dispatch_event("grab", {"title": "x"})

    assert len(calls) == 2
    updated = next(n for n in load_settings().connect if n.id == notif_id)
    assert updated.last_status == "delivered"
    assert updated.last_status_code == 200


async def test_dispatch_records_failure_after_exhausting_retry(app_client, monkeypatch):
    notif_id = _add_webhook(app_client, on_grab=True)
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    # Must never raise even though every attempt fails.
    await dispatch_event("grab", {"title": "x"})

    assert len(calls) == 2
    updated = next(n for n in load_settings().connect if n.id == notif_id)
    assert updated.last_status == "failed"
    assert updated.last_status_code is None
    assert updated.last_error


async def test_dispatch_skips_disabled_webhook(app_client, monkeypatch):
    _add_webhook(app_client, enabled=False, on_grab=True)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("disabled webhook must not be called")

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    await dispatch_event("grab", {"title": "x"})


async def test_dispatch_skips_webhook_with_event_flag_off(app_client, monkeypatch):
    _add_webhook(app_client, on_grab=False)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("webhook with on_grab=False must not receive a grab event")

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    await dispatch_event("grab", {"title": "x"})


async def test_dispatch_only_sends_to_webhooks_with_matching_flag(app_client, monkeypatch):
    """on_import must not gate a grab event and vice versa."""
    _add_webhook(app_client, id="wh-grab", on_grab=True, on_import=False)
    _add_webhook(app_client, id="wh-import", on_grab=False, on_import=True)

    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    await dispatch_event("import", {"book_id": 1})

    assert len(called) == 1
    updated = {n.id: n for n in load_settings().connect}
    assert updated["wh-import"].last_status == "delivered"
    assert updated["wh-grab"].last_status == ""


async def test_dispatch_never_logs_header_value(app_client, monkeypatch, caplog):
    _add_webhook(app_client, header_name="X-Api-Key", header_value="top-secret-value")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    with caplog.at_level(logging.DEBUG):
        await dispatch_event("grab", {"title": "x"})

    for record in caplog.records:
        assert "top-secret-value" not in record.getMessage()


# ---------------------------------------------------------------------------
# send_test_event
# ---------------------------------------------------------------------------


async def test_send_test_event_marks_status_tested(app_client, monkeypatch):
    notif_id = _add_webhook(app_client, enabled=False, on_grab=False, on_import=False)

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["event"] == "test"
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    delivered, status_code, error = await send_test_event(
        next(n for n in load_settings().connect if n.id == notif_id)
    )

    assert delivered is True
    assert status_code == 200
    assert error == ""
    updated = next(n for n in load_settings().connect if n.id == notif_id)
    assert updated.last_status == "tested"


# ---------------------------------------------------------------------------
# POST /api/v1/connect/test/{id} endpoint
# ---------------------------------------------------------------------------


def test_connect_test_endpoint_success(app_client, monkeypatch):
    notif_id = _add_webhook(app_client)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))

    resp = app_client.post(
        f"/api/v1/connect/test/{notif_id}",
        json={"url": "http://hooks.local/audiarr", "header_name": "", "header_value": ""},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status_code"] == 200


def test_connect_test_endpoint_failure_reports_status_code(app_client, monkeypatch):
    notif_id = _add_webhook(app_client)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))

    resp = app_client.post(
        f"/api/v1/connect/test/{notif_id}",
        json={"url": "http://hooks.local/audiarr"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["status_code"] == 503


def test_connect_test_endpoint_falls_back_to_stored_header_value(app_client, monkeypatch):
    """A blank header_value in the request (the masked-secret UI pattern)
    must still use the persisted secret, never an empty header."""
    notif_id = _add_webhook(app_client, header_name="X-Api-Key", header_value="stored-secret")

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers.get("X-Api-Key")
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))

    resp = app_client.post(
        f"/api/v1/connect/test/{notif_id}",
        json={"url": "http://hooks.local/audiarr", "header_name": "", "header_value": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert seen["header"] == "stored-secret"


def test_connect_test_endpoint_400_without_url(app_client):
    resp = app_client.post(
        "/api/v1/connect/test/unknown-id",
        json={"url": ""},
    )
    assert resp.status_code == 400


def test_connect_test_endpoint_works_for_unsaved_row(app_client, monkeypatch):
    """A brand-new, not-yet-saved row (client-generated id) can still be
    tested -- the endpoint doesn't require the id to already exist in
    settings.connect."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))

    resp = app_client.post(
        "/api/v1/connect/test/brand-new-client-id",
        json={"url": "http://hooks.local/audiarr"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    # No settings.connect entry existed, so no metadata update happens --
    # confirmed by there simply being nothing to look up.
    assert load_settings().connect == []


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------


def test_connect_notification_defaults(app_client):
    body = app_client.get("/api/v1/settings").json()
    assert body["connect"] == []


def test_connect_notification_persists_new_fields(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["connect"] = [
        {
            "id": "wh-1",
            "name": "Discord",
            "type": "webhook",
            "url": "http://hooks.local/discord",
            "enabled": True,
            "on_grab": True,
            "on_import": False,
            "on_health_issue": True,
            "header_name": "X-Api-Key",
            "header_value": "sekret",
        }
    ]
    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    saved = body["connect"][0]
    assert saved["id"] == "wh-1"
    assert saved["on_grab"] is True
    assert saved["on_import"] is False
    assert saved["on_health_issue"] is True
    assert saved["header_name"] == "X-Api-Key"
    # Note: header_value is NOT excluded from the raw settings API response,
    # matching the existing DownloadClient.api_key/Indexer.api_key precedent
    # in this codebase -- the UI (connect.js) is responsible for never
    # rendering it back into a visible field. See app/connect.py docstring.
    assert saved["header_value"] == "sekret"
