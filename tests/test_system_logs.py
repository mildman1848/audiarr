"""In-memory log buffer, redaction, and GET /api/v1/system/logs (issue #73)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app import logging_conf
from app.logging_conf import (
    LOG_BUFFER,
    REDACTED,
    RedactingRingBufferHandler,
    configure_logging,
    redact_secrets,
)

LOGS_URL = "/api/v1/system/logs"


@pytest.fixture()
def isolated_logger():
    """A private logger feeding a fresh handler, independent of root level."""
    logger = logging.getLogger("audiarr.test_system_logs")
    handler = RedactingRingBufferHandler(capacity=3)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, handler
    logger.removeHandler(handler)


@pytest.fixture()
def live_buffer():
    """The shared process buffer, attached to a private logger and emptied."""
    logger = logging.getLogger("audiarr.test_system_logs_live")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(LOG_BUFFER)
    LOG_BUFFER.clear()
    yield logger
    logger.removeHandler(LOG_BUFFER)
    LOG_BUFFER.clear()


def test_buffer_is_bounded_and_newest_first(isolated_logger):
    logger, handler = isolated_logger
    for i in range(5):
        logger.info("message %d", i)

    records = handler.snapshot(10)
    assert [r["message"] for r in records] == ["message 4", "message 3", "message 2"]
    assert [r["message"] for r in handler.snapshot(2)] == ["message 4", "message 3"]
    assert handler.snapshot(0) == []


def test_record_shape_is_stable_plain_dict(isolated_logger):
    logger, handler = isolated_logger
    logger.warning("hello %s", "world")

    (record,) = handler.snapshot(1)
    assert set(record) == {"timestamp", "level", "logger", "message"}
    assert record["level"] == "WARNING"
    assert record["logger"] == "audiarr.test_system_logs"
    assert record["message"] == "hello world"
    # ISO-8601 UTC with millisecond precision.
    assert record["timestamp"].endswith("+00:00")
    assert "T" in record["timestamp"]


def test_snapshot_returns_copies(isolated_logger):
    logger, handler = isolated_logger
    logger.info("original")
    handler.snapshot(1)[0]["message"] = "tampered"
    assert handler.snapshot(1)[0]["message"] == "original"


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("login password=hunter2 for admin", "hunter2"),
        ('payload {"password": "hunter2", "user": "admin"}', "hunter2"),
        ("passphrase: correct horse battery", "correct"),
        ("calling https://x/api?apikey=abc123def&q=1", "abc123def"),
        ("X-Api-Key: abc123def456", "abc123def456"),
        ("webhook_api_key=wh-key-999", "wh-key-999"),
        ("access_token=tok-aaa refresh_token=tok-bbb", "tok-aaa"),
        ("refresh_token='tok-bbb'", "tok-bbb"),
        ("session_id=sess-777", "sess-777"),
        ("client_secret=cs-555", "cs-555"),
        ("Authorization: Bearer abc.def.ghi-123", "abc.def.ghi-123"),
        ("headers {'authorization': 'Basic dXNlcjpwdw=='}", "dXNlcjpwdw"),
        ("sent Bearer abcdefgh12345678 upstream", "abcdefgh12345678"),
        ("Cookie: audiarr_session=sess-777; other=1", "sess-777"),
        ("fetching https://user:s3cretpw@host.example/path", "s3cretpw"),
    ],
)
def test_redact_secrets_removes_values(raw, leaked):
    redacted = redact_secrets(raw)
    assert leaked not in redacted
    assert REDACTED in redacted


def test_redact_secrets_keeps_context():
    assert redact_secrets("login password=hunter2 for admin") == (
        f"login password={REDACTED} for admin"
    )
    assert redact_secrets('{"api_key": "abc", "user": "admin"}') == (
        f'{{"api_key": "{REDACTED}", "user": "admin"}}'
    )
    assert redact_secrets("Authorization: Bearer abc.def.ghi-123") == (
        f"Authorization: Bearer {REDACTED}"
    )
    assert redact_secrets("sync ?token=abc&page=2") == f"sync ?token={REDACTED}&page=2"
    assert redact_secrets("scan finished: 12 books imported") == (
        "scan finished: 12 books imported"
    )


def test_redaction_applies_to_formatted_message_not_args(isolated_logger):
    logger, handler = isolated_logger
    logger.error("connect failed password=%s", "hunter2")

    (record,) = handler.snapshot(1)
    assert "hunter2" not in str(record)
    assert record["message"] == f"connect failed password={REDACTED}"
    assert "args" not in record


def test_redaction_applies_to_exception_text(isolated_logger):
    logger, handler = isolated_logger
    try:
        raise RuntimeError("upstream rejected api_key=abc123def456 token=tok-xyz")
    except RuntimeError:
        logger.exception("sync failed")

    (record,) = handler.snapshot(1)
    assert record["message"].startswith("sync failed\nTraceback")
    assert "RuntimeError" in record["message"]
    assert "abc123def456" not in record["message"]
    assert "tok-xyz" not in record["message"]
    assert f"api_key={REDACTED}" in record["message"]


def test_redaction_applies_to_stack_info(isolated_logger):
    logger, handler = isolated_logger
    logger.warning("trace", stack_info=True)
    (record,) = handler.snapshot(1)
    assert "Stack (most recent call last)" in record["message"]


def test_malformed_log_args_do_not_break_capture(isolated_logger):
    logger, handler = isolated_logger
    # Bypass logging's own misuse guard by emitting a record with bad args.
    record = logging.LogRecord(
        "audiarr.test_system_logs", logging.INFO, __file__, 1, "bad %d", ("x",), None
    )
    handler.emit(record)
    assert handler.snapshot(1)[0]["message"] == "bad %d"


def test_overlong_message_is_truncated(isolated_logger):
    logger, handler = isolated_logger
    logger.info("x" * (logging_conf.MAX_MESSAGE_CHARS + 500))
    message = handler.snapshot(1)[0]["message"]
    assert len(message) <= logging_conf.MAX_MESSAGE_CHARS + len("…[truncated]")
    assert message.endswith("…[truncated]")


def test_configure_logging_is_idempotent_and_keeps_stdout_handlers():
    root = logging.getLogger()
    configure_logging()
    before = list(root.handlers)
    configure_logging("DEBUG")
    configure_logging("INFO")

    assert root.handlers.count(LOG_BUFFER) == 1
    # Repeated calls add nothing; the buffer is the only handler this module adds.
    assert root.handlers == before


def test_endpoint_returns_newest_first_with_stable_shape(app_client, live_buffer):
    for i in range(5):
        live_buffer.warning("endpoint message %d", i)

    resp = app_client.get(LOGS_URL, params={"limit": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"records", "count", "limit", "capacity"}
    assert body["limit"] == 3
    assert body["count"] == 3
    assert body["capacity"] == LOG_BUFFER.capacity
    assert [r["message"] for r in body["records"]] == [
        "endpoint message 4",
        "endpoint message 3",
        "endpoint message 2",
    ]
    for record in body["records"]:
        assert set(record) == {"timestamp", "level", "logger", "message"}
        assert record["logger"] == "audiarr.test_system_logs_live"


def test_endpoint_redacts_secrets_in_response(app_client, live_buffer):
    live_buffer.error("bad login password=hunter2")
    try:
        raise ValueError("token=tok-secret-123")
    except ValueError:
        live_buffer.exception("boom")

    text = app_client.get(LOGS_URL).text
    assert "hunter2" not in text
    assert "tok-secret-123" not in text
    assert REDACTED in text


def test_endpoint_default_limit_and_empty_buffer(app_client, live_buffer):
    LOG_BUFFER.clear()
    body = app_client.get(LOGS_URL).json()
    assert body["limit"] == 100
    assert body["records"] == [] or all(
        r["logger"] != "audiarr.test_system_logs_live" for r in body["records"]
    )


@pytest.mark.parametrize("limit", ["0", "-1", "501", "100000", "abc", ""])
def test_endpoint_rejects_invalid_limit(app_client, limit):
    assert app_client.get(LOGS_URL, params={"limit": limit}).status_code == 422


def test_endpoint_accepts_hard_max_limit(app_client, live_buffer):
    live_buffer.info("one")
    resp = app_client.get(LOGS_URL, params={"limit": 500})
    assert resp.status_code == 200
    assert resp.json()["limit"] == 500


def test_endpoint_ignores_filesystem_style_params(app_client, live_buffer):
    live_buffer.info("safe")
    resp = app_client.get(LOGS_URL, params={"path": "/etc/passwd", "file": "../../x"})
    assert resp.status_code == 200
    assert set(resp.json()) == {"records", "count", "limit", "capacity"}


def test_endpoint_is_read_only(app_client):
    assert app_client.post(LOGS_URL).status_code == 405
    assert app_client.delete(LOGS_URL).status_code == 405


def test_endpoint_requires_auth_when_enabled(app_client, live_buffer):
    settings = app_client.get("/api/v1/settings").json()
    api_key = settings["auth"]["api_key"]
    settings["auth"]["method"] = "forms"
    settings["auth"]["username"] = "admin"
    settings["auth"]["password"] = "s3cret-pw"
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    live_buffer.info("authed message")

    denied = app_client.get(LOGS_URL)
    assert denied.status_code == 401
    assert "authed message" not in denied.text

    via_key = app_client.get(LOGS_URL, headers={"X-Api-Key": api_key})
    assert via_key.status_code == 200

    login = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert login.status_code == 200
    assert app_client.get(LOGS_URL).status_code == 200


@pytest.mark.parametrize(
    ("language", "tab_label", "marker"),
    [
        ("en", "Logs", "current process only"),
        ("de", "Protokoll", "aktuellen Prozesses"),
    ],
)
def test_logs_tab_renders_in_both_languages(app_client, language, tab_label, marker):
    settings = app_client.get("/api/v1/settings").json()
    settings["ui"]["language"] = language
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    page = app_client.get("/system/status")
    assert page.status_code == 200
    assert 'data-system-tab="logs"' in page.text
    assert 'id="system-tab-logs"' in page.text
    assert 'id="system-panel-logs"' in page.text
    assert 'id="system-logs-table"' in page.text
    assert 'id="system-logs-tbody"' in page.text
    assert 'id="system-logs-refresh-btn"' in page.text
    assert f">{tab_label}</button>" in page.text
    assert marker in page.text


def test_system_js_logs_tab_escapes_and_uses_bounded_endpoint():
    js = Path("app/web/static/js/system.js").read_text(encoding="utf-8")
    assert "async function refreshLogs" in js
    assert '"logs"' in js
    assert "/api/v1/system/logs?limit=" in js
    assert "${esc(rec.message)}" in js
    assert "${esc(rec.logger)}" in js


# -- Issue #73 follow-up: JWT / private-key labels, logger-name redaction ---------

# Fabricated, obviously fake values; the tests only assert they never survive.
_FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0LXVzZXIifQ.c2lnbmF0dXJlLXZhbHVl"
_FAKE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIBOgIBAAJBAKfakefakefakefake\n"
    "fakefakefakefakefakefakefake==\n"
    "-----END RSA PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("auth jwt=jwt-opaque-value-1 done", "jwt-opaque-value-1"),
        ("auth jwt: jwt-opaque-value-2 done", "jwt-opaque-value-2"),
        ('body {"jwt": "jwt-opaque-value-3"}', "jwt-opaque-value-3"),
        ("id_jwt=jwt-opaque-value-4", "jwt-opaque-value-4"),
        (f"received {_FAKE_JWT} from client", _FAKE_JWT),
        ("private_key=pk-opaque-value-1", "pk-opaque-value-1"),
        ('{"private-key": "pk-opaque-value-2"}', "pk-opaque-value-2"),
        ("access_key=ak-opaque-value-1", "ak-opaque-value-1"),
        ("signing_key: sk-opaque-value-1", "sk-opaque-value-1"),
        ("credentials=cred-opaque-value-1", "cred-opaque-value-1"),
        ("credential: cred-opaque-value-2", "cred-opaque-value-2"),
        (f"loaded key\n{_FAKE_PEM}\nok", "MIIBOgIBAAJBAKfakefakefakefake"),
        ("truncated -----BEGIN PRIVATE KEY-----\nMIIEvQIBADANfakefake", "MIIEvQIBADANfakefake"),
    ],
)
def test_redact_secrets_covers_jwt_and_private_key_labels(raw, leaked):
    redacted = redact_secrets(raw)
    assert leaked not in redacted
    assert REDACTED in redacted


@pytest.mark.parametrize(
    "token",
    [
        # Short but valid payload (``e30`` is base64url for ``{}``).
        "eyJhbGciOiJIUzI1NiJ9.e30.c2ln",
        # Short payload with an empty signature (token ends in a dot).
        "eyJhbGciOiJub25lIn0.e30.",
        # Fully empty payload and signature.
        "eyJhbGciOiJub25lIn0..",
    ],
)
def test_redact_secrets_covers_short_unlabelled_jwts(token):
    redacted = redact_secrets(f"received {token} from client")
    assert token not in redacted
    assert "e30" not in redacted
    assert "eyJ" not in redacted
    assert REDACTED in redacted
    assert redacted == f"received {REDACTED} from client"


def test_redaction_keeps_ordinary_key_words_and_quotes():
    # A bare "key" is not a secret label.
    for text in (
        "primary key violation on books",
        "cache key=abc computed",
        "monkey: banana",
        "keyboard layout us",
        "jwt validation enabled",
    ):
        assert redact_secrets(text) == text
    assert redact_secrets("jwt='abc12345'") == f"jwt='{REDACTED}'"
    assert redact_secrets('{"jwt": "abc12345", "user": "admin"}') == (
        f'{{"jwt": "{REDACTED}", "user": "admin"}}'
    )
    assert redact_secrets("before\n" + _FAKE_PEM + "\nafter") == f"before\n{REDACTED}\nafter"


def test_logger_name_is_redacted_before_storage(isolated_logger):
    _, handler = isolated_logger
    record = logging.LogRecord(
        "audiarr.client.token=name-secret-value", logging.INFO, __file__, 1, "hello", None, None
    )
    handler.emit(record)

    (stored,) = handler.snapshot(1)
    assert "name-secret-value" not in stored["logger"]
    assert stored["logger"] == f"audiarr.client.token={REDACTED}"
    assert stored["message"] == "hello"


def test_no_raw_secret_in_any_stored_field(isolated_logger):
    _, handler = isolated_logger
    raw_values = ["msg-secret-value-1", "exc-secret-value-1", "name-secret-value-2", _FAKE_JWT]
    try:
        raise RuntimeError(f"upstream said jwt=exc-secret-value-1 and {_FAKE_PEM}")
    except RuntimeError:
        record = logging.LogRecord(
            "audiarr.sync.password=name-secret-value-2",
            logging.ERROR,
            __file__,
            1,
            "failed token=%s",
            ("msg-secret-value-1",),
            __import__("sys").exc_info(),
        )
    handler.emit(record)

    (stored,) = handler.snapshot(1)
    blob = "\n".join(stored.values())
    for raw in raw_values + ["MIIBOgIBAAJBAKfakefakefakefake"]:
        assert raw not in blob
    assert "RuntimeError" in stored["message"]  # useful context survives
    assert stored["message"].startswith(f"failed token={REDACTED}")


def test_endpoint_never_returns_raw_secrets_from_any_field(app_client, live_buffer):
    raw_values = ["api-msg-secret-1", "api-exc-secret-1", _FAKE_JWT, "api-pk-secret-1"]
    live_buffer.error("login jwt=api-msg-secret-1 seen %s", _FAKE_JWT)
    try:
        raise ValueError("private_key=api-pk-secret-1 jwt: api-exc-secret-1")
    except ValueError:
        live_buffer.exception("boom")
    logging.getLogger("audiarr.test_system_logs_live.secret=api-name-secret-1").warning("x")
    child = logging.getLogger("audiarr.test_system_logs_live.token=api-name-secret-1")
    child.warning("child record")

    text = app_client.get(LOGS_URL).text
    for raw in [*raw_values, "api-name-secret-1"]:
        assert raw not in text
    assert REDACTED in text
