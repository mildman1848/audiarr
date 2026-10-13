"""Outbound webhook dispatch (issue #28): app.connect + the Connect test
endpoint.

Delivery tests monkeypatch ``app.connect._new_client`` -- a small seam
app.connect calls instead of ``httpx.AsyncClient()`` directly -- with a
factory that returns an ``httpx.MockTransport``-backed client (same
MockTransport pattern used throughout tests/test_connections_*.py).
"""

from __future__ import annotations

import copy
import json
import logging

import httpx
import pytest

from app.config import get_settings_path, load_settings, save_settings
from app.connect import (
    dispatch_event,
    sanitize_connect_errors,
    sanitize_last_error,
    send_test_event,
)
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
    # Keys are preserved; the job identifier value is redacted (issue #82).
    assert seen["body"]["data"] == {"title": "Der Vorleser", "nzo_id": "[redacted]"}

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


_UNKNOWN_EVENT = "https://svc-user:svc-pass@evil.example.net/hook?token=tok-9f3a"
_HOSTILE_NAME = "https://ops-user:ops-pass@hooks.internal.example/path/x?key=k-77 my-hook"
_LOG_NEEDLES = (
    "svc-user",
    "svc-pass",
    "tok-9f3a",
    "evil.example.net",
    "ops-user",
    "ops-pass",
    "k-77",
    "hooks.internal.example",
    "my-hook",
    "Vorleser-secret-title",
)


def _assert_no_needles_logged(caplog) -> None:
    assert caplog.records
    for record in caplog.records:
        rendered = " ".join([record.getMessage(), str(record.args), str(record.exc_info)])
        for needle in _LOG_NEEDLES:
            assert needle not in rendered
    for needle in _LOG_NEEDLES:
        assert needle not in caplog.text


async def test_unknown_event_logs_constant_message_only(app_client, monkeypatch, caplog):
    _add_webhook(app_client, name=_HOSTILE_NAME, on_grab=True, on_import=True)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("unregistered event must never be delivered")

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    with caplog.at_level(logging.DEBUG):
        await dispatch_event(_UNKNOWN_EVENT, {"title": "Vorleser-secret-title"})

    _assert_no_needles_logged(caplog)
    messages = [r.getMessage() for r in caplog.records if r.name == "audiarr.connect"]
    assert messages == ["connect: dropped unregistered event"]


async def test_dispatch_logs_omit_webhook_name_and_url_on_success(app_client, monkeypatch, caplog):
    _add_webhook(
        app_client,
        name=_HOSTILE_NAME,
        url="http://hooks.internal.example/path/x?key=k-77",
        on_grab=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    with caplog.at_level(logging.DEBUG):
        await dispatch_event("grab", {"title": "Vorleser-secret-title"})

    _assert_no_needles_logged(caplog)
    messages = [r.getMessage() for r in caplog.records if r.name == "audiarr.connect"]
    assert messages == ["connect: event=grab status=delivered code=200"]


@pytest.mark.parametrize("fail", [False, True])
async def test_httpx_request_log_suppressed_only_during_webhook_post(
    monkeypatch, caplog, fail
):
    from app import connect

    def handler(request: httpx.Request) -> httpx.Response:
        if fail and request.url.host == "hooks.internal.example":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200)

    url = "http://hooks.internal.example/path/x?key=k-77"
    client = _mock_async_client_factory(handler)()
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            await connect._post_once(client, url, {}, {})
            # Context flag is reset after the send, success or failure.
            assert connect._webhook_send_active.get() is False
            # Unrelated traffic still gets a safe INFO line, but never a hostname.
            await client.get("http://unrelated.example/ok")
    finally:
        await client.aclose()

    httpx_messages = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert not any("k-77" in m for m in httpx_messages)
    assert not any("unrelated.example" in m for m in httpx_messages)
    assert any(m == "HTTP Request: GET -> 200" for m in httpx_messages)


async def test_dispatch_logs_omit_webhook_name_and_url_on_failed_delivery(
    app_client, monkeypatch, caplog
):
    _add_webhook(
        app_client,
        name=_HOSTILE_NAME,
        url="http://hooks.internal.example/path/x?key=k-77",
        on_grab=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.url} for {_HOSTILE_NAME}", request=request)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    with caplog.at_level(logging.DEBUG):
        await dispatch_event("grab", {"title": "Vorleser-secret-title"})

    _assert_no_needles_logged(caplog)
    messages = [r.getMessage() for r in caplog.records if r.name == "audiarr.connect"]
    assert messages == ["connect: event=grab status=failed code=None"]


async def test_unexpected_dispatch_failure_log_omits_name_and_exception_text(
    app_client, monkeypatch, caplog
):
    _add_webhook(
        app_client,
        name=_HOSTILE_NAME,
        url="http://hooks.internal.example/path/x?key=k-77",
        on_grab=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    def boom(*args, **kwargs):
        raise RuntimeError(f"persist failed for {_HOSTILE_NAME} {_UNKNOWN_EVENT}")

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    monkeypatch.setattr("app.connect._record_result", boom)
    with caplog.at_level(logging.DEBUG):
        await dispatch_event("grab", {"title": "Vorleser-secret-title"})

    _assert_no_needles_logged(caplog)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["connect: dispatch of event=grab failed unexpectedly (RuntimeError)"]


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
    # header_value is write-only at the settings API (issue #82): never
    # returned, only the derived flag; the real secret stays persisted.
    assert "header_value" not in saved
    assert saved["header_value_set"] is True
    assert "header_value" not in put_response.json()["connect"][0]
    assert load_settings().connect[0].header_value == "sekret"


# ---------------------------------------------------------------------------
# header_value is write-only at the settings API (issue #82)
# ---------------------------------------------------------------------------

SECRET = "sekret-token-123"


def _put_connect(app_client, entries: list[dict]):
    """GET-merge-PUT the settings document with the given connect list."""
    doc = app_client.get("/api/v1/settings").json()
    doc["connect"] = entries
    return app_client.put("/api/v1/settings", json=doc)


def _entry(**overrides) -> dict:
    base = {
        "id": "wh-1",
        "name": "Discord",
        "type": "webhook",
        "url": "http://hooks.local/discord",
        "enabled": True,
        "header_name": "X-Api-Key",
    }
    base.update(overrides)
    return base


def _seed_secret(app_client, **overrides) -> None:
    resp = _put_connect(app_client, [_entry(header_value=SECRET, **overrides)])
    assert resp.status_code == 200


def test_settings_get_and_put_never_return_connect_header_value(app_client):
    put_resp = _put_connect(app_client, [_entry(header_value=SECRET)])
    assert put_resp.status_code == 200
    get_resp = app_client.get("/api/v1/settings")

    for resp in (put_resp, get_resp):
        assert SECRET not in resp.text
        entry = resp.json()["connect"][0]
        assert "header_value" not in entry
        assert entry["header_value_set"] is True

    # The persisted settings file still stores the real secret.
    assert SECRET in get_settings_path().read_text(encoding="utf-8")
    assert load_settings().connect[0].header_value == SECRET


def test_header_value_set_is_false_without_a_stored_secret(app_client):
    put_resp = _put_connect(app_client, [_entry(header_name="", header_value="")])
    get_resp = app_client.get("/api/v1/settings")

    for resp in (put_resp, get_resp):
        entry = resp.json()["connect"][0]
        assert entry["header_value_set"] is False
        assert "header_value" not in entry


def test_blank_header_value_keeps_stored_secret_for_same_id(app_client):
    _seed_secret(app_client)

    # Rename and reorder behind another entry: id alone decides the match,
    # while url and header_name stay bound to the stored secret.
    resp = _put_connect(
        app_client,
        [
            _entry(id="wh-2", name="Other", header_name="", header_value=""),
            _entry(name="Renamed", header_value=""),
        ],
    )
    assert resp.status_code == 200
    by_id = {c["id"]: c for c in resp.json()["connect"]}
    assert by_id["wh-1"]["header_value_set"] is True
    assert by_id["wh-2"]["header_value_set"] is False

    persisted = {c.id: c for c in load_settings().connect}
    assert persisted["wh-1"].header_value == SECRET
    assert persisted["wh-1"].name == "Renamed"
    assert persisted["wh-2"].header_value == ""


def test_get_roundtrip_put_keeps_stored_secret(app_client):
    """Pages that GET-merge-PUT the whole document (language switch, other
    settings sections) send no header_value at all; the secret must survive."""
    _seed_secret(app_client)

    doc = app_client.get("/api/v1/settings").json()
    assert "header_value" not in doc["connect"][0]
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200

    assert load_settings().connect[0].header_value == SECRET


def test_nonblank_header_value_replaces_stored_secret(app_client):
    _seed_secret(app_client)

    resp = _put_connect(app_client, [_entry(header_value="new-secret")])
    assert resp.status_code == 200
    assert "new-secret" not in resp.text
    assert resp.json()["connect"][0]["header_value_set"] is True
    assert load_settings().connect[0].header_value == "new-secret"


def test_blank_header_name_clears_header_name_and_value(app_client):
    _seed_secret(app_client)

    resp = _put_connect(app_client, [_entry(header_name="", header_value="")])
    assert resp.status_code == 200
    assert resp.json()["connect"][0]["header_value_set"] is False

    saved = load_settings().connect[0]
    assert saved.header_name == ""
    assert saved.header_value == ""
    assert SECRET not in get_settings_path().read_text(encoding="utf-8")


def test_blank_header_name_clears_even_an_incoming_header_value(app_client):
    resp = _put_connect(app_client, [_entry(header_name="  ", header_value="orphan")])
    assert resp.status_code == 200

    saved = load_settings().connect[0]
    assert saved.header_name == ""
    assert saved.header_value == ""


def test_unknown_or_missing_id_never_inherits_a_stored_secret(app_client):
    _seed_secret(app_client)

    # Same name/url/position as the stored entry, but a different id.
    resp = _put_connect(app_client, [_entry(id="not-wh-1", header_value="")])
    assert resp.status_code == 200
    assert resp.json()["connect"][0]["header_value_set"] is False
    assert load_settings().connect[0].header_value == ""

    # A missing (blank) id must not match either, even if the stored entry
    # also has a blank id: nothing is inherited, and the save that would
    # silently erase the legacy secret is rejected instead (see the legacy
    # blank-id tests below).
    settings = load_settings()
    settings.connect = [ConnectNotification(id="", name="Legacy", url=_entry()["url"],
                                            header_name="X-Api-Key", header_value=SECRET)]
    save_settings(settings)
    resp = _put_connect(app_client, [_entry(id="", name="Legacy", header_value="")])
    assert resp.status_code == 422
    assert SECRET not in resp.text
    assert load_settings().connect[0].header_value == SECRET


def test_duplicate_incoming_id_only_first_entry_inherits_secret(app_client):
    _seed_secret(app_client)

    resp = _put_connect(
        app_client,
        [_entry(header_value=""), _entry(name="Clone", header_value="")],
    )
    assert resp.status_code == 200

    first, second = load_settings().connect
    assert first.header_value == SECRET
    assert second.header_value == ""


def test_incoming_header_value_set_flag_cannot_create_a_secret(app_client):
    resp = _put_connect(app_client, [_entry(id="fresh", header_value="", header_value_set=True)])
    assert resp.status_code == 200
    assert resp.json()["connect"][0]["header_value_set"] is False
    assert load_settings().connect[0].header_value == ""


# ---------------------------------------------------------------------------
# Unknown events are dropped (issue #82)
# ---------------------------------------------------------------------------


async def test_unknown_event_is_dropped_before_settings_and_send(app_client, monkeypatch):
    notif_id = _add_webhook(app_client, on_grab=True, on_import=True, on_health_issue=True)
    posts: list[str] = []
    loads: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(str(request.url))
        return httpx.Response(200)

    def counting_load():
        loads.append(1)
        raise AssertionError("settings must not be loaded for an unregistered event")

    real_load_settings = load_settings
    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    monkeypatch.setattr("app.connect.load_settings", counting_load)

    # "test" is only reachable through send_test_event, never dispatch_event.
    for event in ("definitely_unknown", "test", "", "GRAB"):
        await dispatch_event(event, {"title": "x"})
    await dispatch_event(None, {"title": "x"})  # type: ignore[arg-type]

    assert posts == []
    assert loads == []
    monkeypatch.setattr("app.connect.load_settings", real_load_settings)
    stored = next(n for n in load_settings().connect if n.id == notif_id)
    assert stored.last_event == ""
    assert stored.last_status == ""
    assert stored.last_status_code is None
    assert stored.last_error == ""
    assert stored.last_delivered_at == ""


# ---------------------------------------------------------------------------
# Safe legacy payloads (issue #82)
# ---------------------------------------------------------------------------


def _capture_bodies(monkeypatch) -> list[tuple[str, dict]]:
    """Capture (raw serialized body, parsed body) for every POST."""
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raw = request.content.decode("utf-8")
        seen.append((raw, json.loads(raw)))
        return httpx.Response(200)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    return seen


UNSAFE_FRAGMENTS = (
    "user:pw",
    "pw@",
    "apikey=",
    "token=",
    "SECRETTOKEN",
    "nas.lan",
    "sabnzbd.home.lan",
    "qbittorrent.internal",
    "10.0.0.5",
    "192.168.1.20",
    "fe80::1",
    "/mnt/user",
    "/downloads",
    "C:\\Users",
    "C:/Users",
    "SAB-internal",
    "SABnzbd_nzo_abc123",
    "0123456789abcdef0123456789abcdef01234567",
    "Connection refused",
)


async def test_grab_payload_keeps_keys_and_redacts_unsafe_values(app_client, monkeypatch):
    _add_webhook(app_client, on_grab=True)
    seen = _capture_bodies(monkeypatch)
    payload = {
        "title": "Der Vorleser",
        "nzo_id": "SABnzbd_nzo_abc123",
        "category": "audiobooks",
        "indexer_id": 7,
        "download_client": "SAB-internal",
        "protocol": "torrent",
        "torrent_hash": "0123456789abcdef0123456789abcdef01234567",
        "download_url": "http://user:pw@nas.lan:8080/getnzb?apikey=SECRETTOKEN",
        "note": "fetched via https://user:pw@sabnzbd.home.lan/api?token=SECRETTOKEN",
        "extra": {"host": "10.0.0.5", "path": "/mnt/user/audiobooks/x", "api_key": "abc"},
        "list": ["fine title", "192.168.1.20", r"C:\Users\me\books", "C:/Users/me"],
    }
    original = copy.deepcopy(payload)

    await dispatch_event("grab", payload)

    assert payload == original  # caller's dict is never mutated
    raw, body = seen[0]
    assert set(body) == {"event", "app", "timestamp", "data"}
    data = body["data"]
    assert set(data) == set(original)
    assert set(data["extra"]) == {"host", "path", "api_key"}
    assert len(data["list"]) == 4
    assert data["title"] == "Der Vorleser"
    assert data["category"] == "audiobooks"
    assert data["indexer_id"] == 7
    assert data["protocol"] == "torrent"
    assert data["nzo_id"] == "[redacted]"
    assert data["download_client"] == "[redacted]"
    assert data["torrent_hash"] == "[redacted]"
    assert data["download_url"] == "[redacted]"
    assert data["note"] == "[redacted]"
    assert data["extra"] == {"host": "[redacted]", "path": "[redacted]", "api_key": "[redacted]"}
    assert data["list"] == ["fine title", "[redacted]", "[redacted]", "[redacted]"]
    for fragment in UNSAFE_FRAGMENTS:
        assert fragment not in raw, fragment


async def test_import_payload_redacts_source_path_and_keeps_shape(app_client, monkeypatch):
    _add_webhook(app_client, on_import=True)
    seen = _capture_bodies(monkeypatch)

    await dispatch_event(
        "import",
        {
            "book_id": 42,
            "title": "Ein Mann namens Ove",
            "source_path": "/downloads/complete/Ove",
            "status": "matched",
        },
    )

    raw, body = seen[0]
    assert body["data"] == {
        "book_id": 42,
        "title": "Ein Mann namens Ove",
        "source_path": "[redacted]",
        "status": "matched",
    }
    assert "/downloads" not in raw


async def test_ordinary_titles_are_not_over_redacted(app_client, monkeypatch):
    _add_webhook(app_client, on_import=True)
    seen = _capture_bodies(monkeypatch)
    titles = [
        "Harry Potter und der Stein der Weisen",
        "Ein Mann namens Ove",
        "AC/DC: Die Biografie",
        "Sci-Fi / Fantasy",
        "Kapitel 12: Der Anfang",
        "Dr. Jekyll und Mr. Hyde",
        "Fahrenheit 451 (Hörbuch)",
        "Teil 1 von 3 - Radio 12:30 Uhr",
        "Rock & Roll 2.0",
        "Frank Schätzing",
    ]

    for title in titles:
        await dispatch_event("import", {"book_id": 1, "title": title, "status": "matched"})

    assert [body["data"]["title"] for _, body in seen] == titles


@pytest.mark.parametrize(
    ("integration", "message", "expected_integration", "expected_message"),
    [
        ("Audiobookshelf", "Could not reach Audiobookshelf", "Audiobookshelf", "Connection test failed"),
        ("m4b-convertarr", "boom", "m4b-convertarr", "Connection test failed"),
        ("SABnzbd", "Could not reach SABnzbd", "SABnzbd", "Connection test failed"),
        ("sabnzbd", "x", "SABnzbd", "Connection test failed"),
        ("Prowlarr", "Could not reach Prowlarr", "Prowlarr", "Connection test failed"),
        (
            "qBittorrent",
            "Connection refused to qbittorrent.internal:8080",
            "qBittorrent",
            "Connection test failed",
        ),
        ("Some Private NAS", "token=SECRETTOKEN", "unknown", "Connection test failed"),
        ("root-folder", "Root folder '/mnt/user/x' does not exist", "root-folder",
         "Root folder does not exist"),
        ("root-folder", "Root folder '/mnt/user/x' is not writable: [Errno 13] Permission denied",
         "root-folder", "Root folder is not writable"),
        ("root-folder", "Something at /mnt/user/x went wrong", "root-folder",
         "Root folder health issue"),
        # A folder literally named like a phrase must not flip the class.
        ("root-folder", "Root folder '/mnt/user/not writable' does not exist", "root-folder",
         "Root folder does not exist"),
    ],
)
async def test_health_issue_normalizes_integration_and_message(
    app_client, monkeypatch, integration, message, expected_integration, expected_message
):
    _add_webhook(app_client, on_health_issue=True)
    seen = _capture_bodies(monkeypatch)
    payload = {
        "integration": integration,
        "root_folder_id": 3,
        "path": "/mnt/user/x",
        "url": "http://user:pw@nas.lan/x?apikey=SECRETTOKEN",
        "message": message,
    }

    await dispatch_event("health_issue", payload)

    raw, body = seen[0]
    assert body["data"] == {
        "integration": expected_integration,
        "root_folder_id": 3,
        "path": "[redacted]",
        "url": "[redacted]",
        "message": expected_message,
    }
    for fragment in ("user:pw", "SECRETTOKEN", "nas.lan", "/mnt/user", "qbittorrent.internal"):
        assert fragment not in raw


async def test_health_issue_without_optional_keys_adds_none(app_client, monkeypatch):
    _add_webhook(app_client, on_health_issue=True)
    seen = _capture_bodies(monkeypatch)

    await dispatch_event("health_issue", {"integration": "qBittorrent", "message": "login failed"})

    assert seen[0][1]["data"] == {
        "integration": "qBittorrent",
        "message": "Connection test failed",
    }


# ---------------------------------------------------------------------------
# Safe stored / logged delivery errors (issue #82)
# ---------------------------------------------------------------------------

LEAKY_URL = "https://user:pw@intranet.lan/hook?token=SECRETTOKEN"


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError(f"cannot connect to {LEAKY_URL}"),
        httpx.ReadTimeout(f"timed out for {LEAKY_URL}"),
        ValueError(f"unexpected {LEAKY_URL}"),
    ],
)
async def test_transport_exception_text_never_leaks(app_client, monkeypatch, caplog, exc):
    notif_id = _add_webhook(app_client, on_grab=True)

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    with caplog.at_level(logging.DEBUG):
        await dispatch_event("grab", {"title": "x"})

    updated = next(n for n in load_settings().connect if n.id == notif_id)
    assert updated.last_status == "failed"
    assert updated.last_error == type(exc).__name__

    for needle in ("user:pw", "SECRETTOKEN", "intranet.lan"):
        assert needle not in get_settings_path().read_text(encoding="utf-8")
        assert all(needle not in r.getMessage() for r in caplog.records)
        assert all(needle not in str(r.exc_info) for r in caplog.records if r.exc_info)
        assert needle not in caplog.text


async def test_http_status_label_is_preserved(app_client, monkeypatch):
    notif_id = _add_webhook(app_client, on_grab=True)
    monkeypatch.setattr(
        "app.connect._new_client", _mock_async_client_factory(lambda r: httpx.Response(500))
    )

    await dispatch_event("grab", {"title": "x"})

    updated = next(n for n in load_settings().connect if n.id == notif_id)
    assert updated.last_status == "failed"
    assert updated.last_status_code == 500
    assert updated.last_error == "HTTP 500"


async def test_send_test_event_returns_safe_error_label(app_client, monkeypatch):
    notif_id = _add_webhook(app_client)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {LEAKY_URL}", request=request)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    delivered, status_code, error = await send_test_event(
        next(n for n in load_settings().connect if n.id == notif_id)
    )

    assert (delivered, status_code, error) == (False, None, "ConnectError")
    updated = next(n for n in load_settings().connect if n.id == notif_id)
    assert updated.last_status == "failed"
    assert updated.last_error == "ConnectError"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("HTTP 500", "HTTP 500"),
        ("HTTP 404", "HTTP 404"),
        ("HTTP 99999", "delivery_failed"),
        ("HTTP 500 for https://user:pw@h/x", "delivery_failed"),
        ("ConnectError", "ConnectError"),
        ("ReadTimeout", "ReadTimeout"),
        ("OSError", "OSError"),
        ("All connection attempts failed", "delivery_failed"),
        ("https://user:pw@intranet.lan/hook?token=SECRETTOKEN", "delivery_failed"),
        ("intranet.lan", "delivery_failed"),
        ("[Errno -2] Name or service not known", "delivery_failed"),
        ("ConnectError\n", "delivery_failed"),
        ("A" * 200, "delivery_failed"),
    ],
)
def test_sanitize_last_error(value, expected):
    assert sanitize_last_error(value) == expected


def _write_stored_errors(errors: dict[str, str]) -> None:
    settings = load_settings()
    settings.connect = [
        ConnectNotification(id=i, name=i, url="http://hooks.local/x", last_status="failed",
                            last_error=err)
        for i, err in errors.items()
    ]
    save_settings(settings)


def test_sanitize_connect_errors_scrubs_stored_values(app_client):
    _write_stored_errors(
        {
            "leaky": f"All connection attempts failed for {LEAKY_URL}",
            "ok-http": "HTTP 503",
            "ok-label": "ConnectTimeout",
            "empty": "",
        }
    )

    assert sanitize_connect_errors() == 1

    by_id = {n.id: n for n in load_settings().connect}
    assert by_id["leaky"].last_error == "delivery_failed"
    assert by_id["ok-http"].last_error == "HTTP 503"
    assert by_id["ok-label"].last_error == "ConnectTimeout"
    assert by_id["empty"].last_error == ""
    assert "SECRETTOKEN" not in get_settings_path().read_text(encoding="utf-8")
    assert sanitize_connect_errors() == 0


async def test_startup_scrubs_stored_errors_before_serving(app_client):
    from app import main as main_module

    _write_stored_errors({"leaky": f"boom {LEAKY_URL}"})

    async with main_module.lifespan(main_module.app):
        # The scrub already ran by the time the app would serve requests.
        assert load_settings().connect[0].last_error == "delivery_failed"
        assert "SECRETTOKEN" not in app_client.get("/api/v1/settings").text


async def test_startup_scrub_failure_logs_constant_message_only(app_client, monkeypatch, caplog):
    from app import main as main_module

    def broken():
        raise RuntimeError(f"cannot scrub {LEAKY_URL}")

    monkeypatch.setattr("app.main.sanitize_connect_errors", broken)
    with caplog.at_level(logging.DEBUG):
        async with main_module.lifespan(main_module.app):
            pass

    assert any("could not sanitize stored delivery errors" in r.getMessage()
               for r in caplog.records)
    for needle in ("SECRETTOKEN", "user:pw", "intranet.lan", "cannot scrub"):
        assert needle not in caplog.text


def test_settings_put_ignores_client_supplied_delivery_metadata_for_new_ids(app_client):
    resp = _put_connect(
        app_client,
        [
            _entry(id="a", last_error=f"boom {LEAKY_URL}", last_event="grab", last_status="failed",
                   last_status_code=502, last_delivered_at="forged"),
            _entry(id="b", last_error="HTTP 502", last_event="import", last_status="delivered",
                   last_status_code=200, last_delivered_at="forged"),
            _entry(id="c", last_error="ReadTimeout", last_event="test", last_status="failed",
                   last_status_code=500, last_delivered_at="forged"),
        ],
    )
    assert resp.status_code == 200
    assert "SECRETTOKEN" not in resp.text

    entries = app_client.get("/api/v1/settings").json()["connect"]
    assert all(
        (entry["last_event"], entry["last_status"], entry["last_status_code"],
         entry["last_error"], entry["last_delivered_at"])
        == ("", "", None, "", "")
        for entry in entries
    )
    assert "SECRETTOKEN" not in get_settings_path().read_text(encoding="utf-8")


def test_settings_put_preserves_server_delivery_metadata_by_stable_id(app_client):
    _add_webhook(app_client, id="stable", name="Server entry", on_grab=True)
    settings = load_settings()
    stored = settings.connect[0]
    stored.last_event = "grab"
    stored.last_status = "delivered"
    stored.last_status_code = 204
    stored.last_error = "HTTP 204"
    stored.last_delivered_at = "2026-10-12T10:11:12+00:00"
    save_settings(settings)

    doc = app_client.get("/api/v1/settings").json()
    doc["connect"] = [
        _entry(id="stable", name="Renamed", last_event="forged", last_status="failed",
               last_status_code=599, last_error=f"forged {LEAKY_URL}",
               last_delivered_at="forged")
    ]
    resp = app_client.put("/api/v1/settings", json=doc)

    assert resp.status_code == 200
    saved = load_settings().connect[0]
    assert (saved.last_event, saved.last_status, saved.last_status_code,
            saved.last_error, saved.last_delivered_at) == (
        "grab", "delivered", 204, "HTTP 204", "2026-10-12T10:11:12+00:00"
    )
    assert "forged" not in resp.text and "SECRETTOKEN" not in resp.text


def test_settings_put_does_not_inherit_metadata_for_ambiguous_or_blank_ids(app_client):
    settings = load_settings()
    settings.connect = [
        ConnectNotification(id="duplicate", name="One", url="http://hooks.local/one",
                            last_event="grab", last_status="delivered", last_status_code=200,
                            last_error="HTTP 200", last_delivered_at="old"),
        ConnectNotification(id="duplicate", name="Two", url="http://hooks.local/two",
                            last_event="import", last_status="failed", last_status_code=500,
                            last_error="HTTP 500", last_delivered_at="older"),
        ConnectNotification(id="", name="Legacy", url="http://hooks.local/legacy",
                            last_event="grab", last_status="delivered", last_status_code=200,
                            last_error="HTTP 200", last_delivered_at="old"),
    ]
    save_settings(settings)

    doc = app_client.get("/api/v1/settings").json()
    for entry in doc["connect"]:
        entry.update(last_event="forged", last_status="failed", last_status_code=599,
                     last_error="forged", last_delivered_at="forged")
    resp = app_client.put("/api/v1/settings", json=doc)

    assert resp.status_code == 200
    assert all(
        (entry.last_event, entry.last_status, entry.last_status_code,
         entry.last_error, entry.last_delivered_at) == ("", "", None, "", "")
        for entry in load_settings().connect
    )


# ---------------------------------------------------------------------------
# A stored header_value is bound to its destination (url + header_name)
# ---------------------------------------------------------------------------

ATTACKER_URL = "http://attacker.example/collect"
_STORED_URL = "http://hooks.local/discord"


def _capture_requests(monkeypatch, status: int = 200) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status)

    monkeypatch.setattr("app.connect._new_client", _mock_async_client_factory(handler))
    return seen


def _post_test(app_client, notif_id: str = "wh-1", **body):
    payload = {"url": _STORED_URL, "header_name": "X-Api-Key", "header_value": ""}
    payload.update(body)
    return app_client.post(f"/api/v1/connect/test/{notif_id}", json=payload)


def _assert_secret_untouched(app_client) -> None:
    stored = load_settings().connect[0]
    assert (stored.url, stored.header_name, stored.header_value) == (
        _STORED_URL,
        "X-Api-Key",
        SECRET,
    )


def _assert_no_secret_anywhere(seen: list[httpx.Request], resp: httpx.Response) -> None:
    assert SECRET not in resp.text
    for request in seen:
        assert SECRET not in json.dumps(dict(request.headers))
        assert SECRET not in request.content.decode()
        assert SECRET not in str(request.url)


def test_test_endpoint_same_target_blank_value_still_uses_stored_secret(app_client, monkeypatch):
    _seed_secret(app_client)
    seen = _capture_requests(monkeypatch)

    resp = _post_test(app_client)

    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert [str(r.url) for r in seen] == [_STORED_URL]
    assert seen[0].headers["X-Api-Key"] == SECRET
    assert SECRET not in resp.text
    _assert_secret_untouched(app_client)


def test_test_endpoint_blank_url_and_name_fall_back_to_stored_target_and_secret(
    app_client, monkeypatch
):
    _seed_secret(app_client)
    seen = _capture_requests(monkeypatch)

    resp = _post_test(app_client, url="  ", header_name="")

    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert [str(r.url) for r in seen] == [_STORED_URL]
    assert seen[0].headers["X-Api-Key"] == SECRET
    _assert_secret_untouched(app_client)


@pytest.mark.parametrize(
    "url",
    [
        ATTACKER_URL,
        "http://hooks.local/discord/other",
        "https://hooks.local/discord",
        "http://hooks.local:8080/discord",
        "http://hooks.local/DISCORD",
    ],
)
def test_test_endpoint_changed_url_never_receives_the_stored_secret(app_client, monkeypatch, url):
    _seed_secret(app_client)
    seen = _capture_requests(monkeypatch)

    resp = _post_test(app_client, url=url)

    assert resp.status_code == 422
    assert seen == []
    _assert_no_secret_anywhere(seen, resp)
    assert "attacker.example" not in resp.text
    _assert_secret_untouched(app_client)


@pytest.mark.parametrize("header_name", ["X-Other", "x-api-key", "Authorization"])
def test_test_endpoint_changed_header_name_never_receives_the_stored_secret(
    app_client, monkeypatch, header_name
):
    _seed_secret(app_client)
    seen = _capture_requests(monkeypatch)

    resp = _post_test(app_client, header_name=header_name)

    assert resp.status_code == 422
    assert seen == []
    _assert_no_secret_anywhere(seen, resp)
    _assert_secret_untouched(app_client)


def test_test_endpoint_blank_url_with_changed_header_name_does_not_send_secret(
    app_client, monkeypatch
):
    """A blank url falls back to the stored one, but a different header name
    still must not pick up the stored value."""
    _seed_secret(app_client)
    seen = _capture_requests(monkeypatch)

    resp = _post_test(app_client, url="", header_name="X-Other")

    assert resp.status_code == 422
    assert seen == []
    _assert_secret_untouched(app_client)


def test_test_endpoint_changed_url_without_header_name_is_sent_without_any_secret(
    app_client, monkeypatch
):
    """Testing a new url with no header at all stays possible; the stored
    header name/secret are not dragged along to the new destination."""
    _seed_secret(app_client)
    seen = _capture_requests(monkeypatch)

    resp = _post_test(app_client, url=ATTACKER_URL, header_name="")

    assert resp.status_code == 200
    assert [str(r.url) for r in seen] == [ATTACKER_URL]
    assert "X-Api-Key" not in seen[0].headers
    _assert_no_secret_anywhere(seen, resp)
    _assert_secret_untouched(app_client)


def test_test_endpoint_explicit_value_works_for_a_changed_target(app_client, monkeypatch):
    _seed_secret(app_client)
    seen = _capture_requests(monkeypatch)

    resp = _post_test(
        app_client, url=ATTACKER_URL, header_name="X-Other", header_value="typed-secret"
    )

    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert [str(r.url) for r in seen] == [ATTACKER_URL]
    assert seen[0].headers["X-Other"] == "typed-secret"
    assert "X-Api-Key" not in seen[0].headers
    _assert_no_secret_anywhere(seen, resp)
    _assert_secret_untouched(app_client)


def test_test_endpoint_stored_entry_without_secret_allows_changed_target(app_client, monkeypatch):
    _add_webhook(app_client, id="plain", header_name="", header_value="")
    seen = _capture_requests(monkeypatch)

    resp = _post_test(app_client, notif_id="plain", url=ATTACKER_URL, header_name="X-Other")

    assert resp.status_code == 200
    assert [str(r.url) for r in seen] == [ATTACKER_URL]
    assert "X-Other" not in seen[0].headers


def test_test_endpoint_unknown_id_never_inherits_a_stored_secret(app_client, monkeypatch):
    _seed_secret(app_client)
    seen = _capture_requests(monkeypatch)

    # Same url/name as the stored entry, different id: nothing to inherit.
    resp = _post_test(app_client, notif_id="not-wh-1")
    assert resp.status_code == 200
    # ... and a blank url cannot fall back to another entry's url either.
    blank = _post_test(app_client, notif_id="not-wh-1", url="")
    assert blank.status_code == 400

    assert [str(r.url) for r in seen] == [_STORED_URL]
    assert "X-Api-Key" not in seen[0].headers
    _assert_no_secret_anywhere(seen, resp)
    _assert_secret_untouched(app_client)


def test_test_endpoint_duplicate_stored_id_is_ambiguous_and_inherits_nothing(
    app_client, monkeypatch
):
    settings = load_settings()
    settings.connect = [
        ConnectNotification(id="dup", name="A", url="http://hooks.local/a",
                            header_name="X-Api-Key", header_value=SECRET),
        ConnectNotification(id="dup", name="B", url="http://hooks.local/b",
                            header_name="X-Api-Key", header_value="other-secret"),
    ]
    save_settings(settings)
    seen = _capture_requests(monkeypatch)

    same_target = _post_test(app_client, notif_id="dup", url="http://hooks.local/a")
    blank_url = _post_test(app_client, notif_id="dup", url="")

    assert same_target.status_code == 200
    assert blank_url.status_code == 400
    assert [str(r.url) for r in seen] == ["http://hooks.local/a"]
    for request in seen:
        assert "X-Api-Key" not in request.headers
    for resp in (same_target, blank_url):
        assert SECRET not in resp.text and "other-secret" not in resp.text


def test_put_same_target_blank_value_keeps_stored_secret(app_client):
    _seed_secret(app_client)

    # Name/enabled flags may change freely; the destination is untouched.
    resp = _put_connect(app_client, [_entry(name="Renamed", enabled=False, header_value="")])

    assert resp.status_code == 200
    assert resp.json()["connect"][0]["header_value_set"] is True
    saved = load_settings().connect[0]
    assert (saved.name, saved.url, saved.header_name, saved.header_value) == (
        "Renamed",
        _STORED_URL,
        "X-Api-Key",
        SECRET,
    )


def test_put_same_target_with_surrounding_whitespace_keeps_stored_secret(app_client):
    _seed_secret(app_client)

    resp = _put_connect(app_client, [_entry(url=f"  {_STORED_URL} ", header_value="")])

    assert resp.status_code == 200
    assert load_settings().connect[0].header_value == SECRET


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"url": ATTACKER_URL}, id="url"),
        pytest.param({"url": "http://hooks.local/DISCORD"}, id="url-case"),
        pytest.param({"url": ""}, id="url-blanked"),
        pytest.param({"header_name": "X-Other"}, id="header-name"),
        pytest.param({"header_name": "x-api-key"}, id="header-name-case"),
        pytest.param({"url": ATTACKER_URL, "header_name": "X-Other"}, id="both"),
    ],
)
def test_put_changed_destination_with_blank_secret_is_rejected_without_mutation(
    app_client, changes
):
    _seed_secret(app_client)
    before = get_settings_path().read_bytes()

    resp = _put_connect(
        app_client,
        [
            _entry(**changes, header_value=""),
            # An unrelated, otherwise valid entry in the same request must not
            # be partially persisted either.
            _entry(id="wh-2", name="Second", header_name="", header_value=""),
        ],
    )

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and "re-enter" in detail
    assert SECRET not in resp.text
    assert "attacker.example" not in resp.text
    assert get_settings_path().read_bytes() == before
    _assert_secret_untouched(app_client)


def test_put_changed_destination_with_explicit_secret_is_saved(app_client):
    _seed_secret(app_client)

    resp = _put_connect(
        app_client,
        [_entry(url=ATTACKER_URL, header_name="X-Other", header_value="typed-secret")],
    )

    assert resp.status_code == 200
    assert "typed-secret" not in resp.text
    assert resp.json()["connect"][0]["header_value_set"] is True
    saved = load_settings().connect[0]
    assert (saved.url, saved.header_name, saved.header_value) == (
        ATTACKER_URL,
        "X-Other",
        "typed-secret",
    )
    assert SECRET not in get_settings_path().read_text(encoding="utf-8")


def test_put_blank_header_name_clears_the_secret_even_when_the_url_changes(app_client):
    _seed_secret(app_client)

    resp = _put_connect(app_client, [_entry(url=ATTACKER_URL, header_name="", header_value="")])

    assert resp.status_code == 200
    saved = load_settings().connect[0]
    assert (saved.url, saved.header_name, saved.header_value) == (ATTACKER_URL, "", "")
    assert SECRET not in get_settings_path().read_text(encoding="utf-8")


def test_put_changed_destination_without_a_stored_secret_is_not_blocked(app_client):
    resp = _put_connect(app_client, [_entry(header_name="X-Api-Key", header_value="")])
    assert resp.status_code == 200
    assert load_settings().connect[0].header_value == ""

    resp = _put_connect(
        app_client, [_entry(url=ATTACKER_URL, header_name="X-Other", header_value="")]
    )
    assert resp.status_code == 200
    saved = load_settings().connect[0]
    assert (saved.url, saved.header_name, saved.header_value) == (ATTACKER_URL, "X-Other", "")


def test_put_unknown_id_with_changed_destination_inherits_nothing(app_client):
    _seed_secret(app_client)

    resp = _put_connect(app_client, [_entry(id="fresh", url=ATTACKER_URL, header_value="")])

    assert resp.status_code == 200
    assert resp.json()["connect"][0]["header_value_set"] is False
    assert load_settings().connect[0].header_value == ""


def test_put_duplicate_incoming_id_clone_with_changed_destination_inherits_nothing(app_client):
    _seed_secret(app_client)

    resp = _put_connect(
        app_client,
        [_entry(header_value=""), _entry(name="Clone", url=ATTACKER_URL, header_value="")],
    )

    assert resp.status_code == 200
    first, clone = load_settings().connect
    assert first.header_value == SECRET and first.url == _STORED_URL
    assert (clone.url, clone.header_value) == (ATTACKER_URL, "")


def test_put_duplicate_stored_id_is_ambiguous_and_inherits_nothing(app_client):
    settings = load_settings()
    settings.connect = [
        ConnectNotification(id="dup", name="A", url=_STORED_URL,
                            header_name="X-Api-Key", header_value=SECRET),
        ConnectNotification(id="dup", name="B", url=_STORED_URL,
                            header_name="X-Api-Key", header_value="other-secret"),
    ]
    save_settings(settings)

    resp = _put_connect(app_client, [_entry(id="dup", header_value="")])

    assert resp.status_code == 200
    assert load_settings().connect[0].header_value == ""


# ---------------------------------------------------------------------------
# Legacy blank/duplicate-id secrets are never silently erased on save
# ---------------------------------------------------------------------------

_FRESH_UI_ID = "0b0c0f4e-7a6d-4a53-9a39-1f2b3c4d5e6f"  # what connect.js assigns


def _seed_legacy(*entries: ConnectNotification) -> None:
    settings = load_settings()
    settings.connect = list(entries)
    save_settings(settings)


def _legacy_entry(**overrides) -> ConnectNotification:
    base = {"id": "", "name": "Legacy", "url": _STORED_URL, "enabled": True,
            "header_name": "X-Api-Key", "header_value": SECRET}
    base.update(overrides)
    return ConnectNotification(**base)


@pytest.mark.parametrize("incoming_id", [_FRESH_UI_ID, ""], ids=["ui-fresh-id", "blank-id"])
def test_put_legacy_blank_id_secret_with_blank_value_is_rejected_without_mutation(
    app_client, incoming_id
):
    _seed_legacy(_legacy_entry())
    before = get_settings_path().read_bytes()

    # GET -> UI assigns a fresh id to the blank-id entry -> PUT with no value.
    doc = app_client.get("/api/v1/settings").json()
    assert doc["connect"][0]["id"] == ""
    assert doc["connect"][0]["header_value_set"] is True
    doc["connect"][0].update(id=incoming_id, header_value="")
    doc["connect"].append(_entry(id="wh-2", name="Second", header_name="", header_value=""))
    resp = app_client.put("/api/v1/settings", json=doc)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and "re-enter" in detail
    assert SECRET not in resp.text and "hooks.local" not in resp.text
    assert get_settings_path().read_bytes() == before
    stored = load_settings().connect
    assert [(c.id, c.url, c.header_name, c.header_value) for c in stored] == [
        ("", _STORED_URL, "X-Api-Key", SECRET)
    ]


def test_put_legacy_blank_id_secret_with_new_value_is_saved(app_client):
    _seed_legacy(_legacy_entry())

    resp = _put_connect(app_client, [_entry(id=_FRESH_UI_ID, header_value="new-secret")])

    assert resp.status_code == 200
    assert "new-secret" not in resp.text and SECRET not in resp.text
    assert resp.json()["connect"][0]["header_value_set"] is True
    saved = load_settings().connect[0]
    assert (saved.id, saved.header_value) == (_FRESH_UI_ID, "new-secret")


def test_put_legacy_blank_id_secret_removed_entry_is_allowed(app_client):
    _seed_legacy(_legacy_entry())

    resp = _put_connect(app_client, [])

    assert resp.status_code == 200
    assert load_settings().connect == []


def test_put_legacy_blank_id_secret_with_cleared_header_is_allowed(app_client):
    _seed_legacy(_legacy_entry())

    resp = _put_connect(app_client, [_entry(id=_FRESH_UI_ID, header_name="", header_value="")])

    assert resp.status_code == 200
    assert resp.json()["connect"][0]["header_value_set"] is False
    saved = load_settings().connect[0]
    assert (saved.header_name, saved.header_value) == ("", "")


def test_put_legacy_secret_does_not_flag_a_properly_inheriting_entry_with_same_target(
    app_client,
):
    # The legacy entry is dropped; wh-1 shares its url/header but inherits its
    # own secret by id, so nothing is silently lost for the entry that remains.
    _seed_legacy(
        _legacy_entry(),
        _legacy_entry(id="wh-1", name="Current", header_value="current-secret"),
    )

    resp = _put_connect(app_client, [_entry(header_value="")])

    assert resp.status_code == 200
    saved = load_settings().connect
    assert [(c.id, c.header_value) for c in saved] == [("wh-1", "current-secret")]


def test_put_duplicate_stored_id_with_distinct_targets_is_rejected_without_mutation(
    app_client,
):
    other_url = "http://hooks.local/other"
    _seed_legacy(
        _legacy_entry(id="dup", name="A", header_value=SECRET),
        _legacy_entry(id="dup", name="B", url=other_url, header_value="other-secret"),
    )
    before = get_settings_path().read_bytes()

    resp = _put_connect(app_client, [_entry(id="dup", url=other_url, header_value="")])

    assert resp.status_code == 422
    assert "other-secret" not in resp.text and "hooks.local" not in resp.text
    assert get_settings_path().read_bytes() == before
    assert [c.header_value for c in load_settings().connect] == [SECRET, "other-secret"]
