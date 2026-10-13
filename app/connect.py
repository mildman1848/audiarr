"""Outbound webhook dispatch for Audiarr events (issue #28).

Best-effort delivery to the webhooks configured in Settings -> Connect
(``Settings.connect``, see app/models/settings.py). A delivery failure must
never break the caller's own workflow (a grab, an import, a connection
health check), so every public function here swallows its own errors and
simply records the outcome on the matching settings entry.

Secrets: ``ConnectNotification.header_value`` is never logged, and is only
ever sent as the value of the single custom header the user configured --
never as part of any other header or the JSON body.

Privacy (issue #82): the legacy payload keeps its envelope and keys, but
unsafe values (paths, URLs, credentials, client/job identifiers, free-form
exception text) are redacted before leaving the process, and only a bounded
safe error label is ever stored or logged for a failed delivery.
"""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import load_settings, save_settings
from app.models.settings import ConnectNotification

log = logging.getLogger("audiarr.connect")

_TIMEOUT_SECONDS = 5.0
_RETRY_DELAY_SECONDS = 0.2

# Maps a dispatched event name to the ConnectNotification flag that must be
# on for a given webhook to receive it.
_EVENT_FLAGS = {
    "grab": "on_grab",
    "import": "on_import",
    "health_issue": "on_health_issue",
}

# ---------------------------------------------------------------------------
# Legacy payload sanitizing
# ---------------------------------------------------------------------------

REDACTED = "[redacted]"
_MAX_DEPTH = 8

# Closed set of integration names a health_issue may carry.
_KNOWN_INTEGRATIONS = (
    "Audiobookshelf",
    "m4b-convertarr",
    "SABnzbd",
    "qBittorrent",
    "Prowlarr",
    "root-folder",
)
_INTEGRATIONS_BY_FOLD = {name.casefold(): name for name in _KNOWN_INTEGRATIONS}
_UNKNOWN_INTEGRATION = "unknown"

# Keys whose value is always redacted (key kept, value replaced). Matching is
# case-insensitive; see _is_sensitive_key for the suffix / credential rules.
_SENSITIVE_KEYS = frozenset(
    {
        "path",
        "source_path",
        "url",
        "download_url",
        "download_client",
        "nzo_id",
        "nzb_id",
        "job_id",
        "download_id",
        "client_id",
        "torrent_id",
        "info_hash",
        "infohash",
        "torrent_hash",
        "hash",
        "magnet",
    }
)
_SENSITIVE_KEY_SUFFIXES = ("_path", "_url", "_uri", "_hash", "_hashes")
# Credential-like key names. Matched per token (split on _ - .) so "authors"
# and "indexer_id" are untouched while "api_key" / "auth_token" are caught.
_CREDENTIAL_TOKENS = frozenset(
    {
        "key",
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
        "pwd",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "cookie",
        "session",
        "bearer",
    }
)

# Fail-closed string guard: any match replaces the WHOLE string.
_URL_RE = re.compile(r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\bwww\.|\bmagnet:\?)")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:(?:key|token|secret|password|passwd|pwd|sig|signature)\s*=|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|\bauthorization\s*:)"
)
_POSIX_PATH_RE = re.compile(
    r"(?:^|(?<=[\s\"'(\[=:,;]))(?:/[^\s/]|~/|\.{1,2}/)"
    r"|\b[\w.-]*[A-Za-z][\w.-]*(?:/[\w.-]+){2,}"
)
_WINDOWS_PATH_RE = re.compile(r"(?:\b[A-Za-z]:[\\/]|\\\\[^\s\\]+\\)")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_HOSTNAME_RE = re.compile(
    r"(?i)(?:\blocalhost\b|"
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:local|lan|internal|intranet|home|corp|private|"
    r"localdomain|docker|svc|arpa|box)\b|"
    r"\b(?=[a-z0-9.-]*[a-z])[a-z0-9-]+(?:\.[a-z0-9-]+)*:\d{2,5}\b)"
)
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_TOKEN_SPLIT_RE = re.compile(r"[\s,;\"'()<>\[\]{}]+")


def _is_sensitive_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    name = key.strip().casefold()
    if name in _SENSITIVE_KEYS or name.endswith(_SENSITIVE_KEY_SUFFIXES):
        return True
    return any(token in _CREDENTIAL_TOKENS for token in re.split(r"[_\-.\s]+", name))


def _contains_ip(text: str) -> bool:
    if _IPV4_RE.search(text):
        return True
    for token in _TOKEN_SPLIT_RE.split(text):
        if ":" not in token or not re.search(r"[0-9a-fA-F]", token):
            continue
        try:
            ipaddress.ip_address(token.strip("."))
        except ValueError:
            continue
        return True
    return False


def _is_unsafe_string(text: str) -> bool:
    return bool(
        _URL_RE.search(text)
        or _CREDENTIAL_RE.search(text)
        or _POSIX_PATH_RE.search(text)
        or _WINDOWS_PATH_RE.search(text)
        or _HOSTNAME_RE.search(text)
        or _LONG_HEX_RE.search(text)
        or _contains_ip(text)
    )


def _sanitize_value(value: Any, depth: int = 0) -> Any:
    """Return a redacted copy of ``value``; the input is never mutated."""
    if depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        return {
            key: (
                (None if item is None else REDACTED)
                if _is_sensitive_key(key)
                else _sanitize_value(item, depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_sanitize_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        return REDACTED if _is_unsafe_string(value) else value
    return value


def _health_message(integration: str, message: Any, path: Any) -> str:
    """Safe static replacement for a health_issue message (never echoes input)."""
    if integration != "root-folder":
        return "Connection test failed"
    text = message if isinstance(message, str) else ""
    # Drop the folder path first so a folder literally named "not writable"
    # cannot flip the classification.
    if isinstance(path, str) and path:
        text = text.replace(path, "")
    text = text.casefold()
    if "does not exist" in text:
        return "Root folder does not exist"
    if "not writable" in text:
        return "Root folder is not writable"
    return "Root folder health issue"


def _sanitize_payload(event: str, payload: Any) -> Any:
    """Legacy payload with the same envelope/keys but unsafe values redacted."""
    clean = _sanitize_value(payload)
    if event == "health_issue" and isinstance(payload, dict) and isinstance(clean, dict):
        raw = payload.get("integration")
        integration = (
            _INTEGRATIONS_BY_FOLD.get(raw.strip().casefold(), _UNKNOWN_INTEGRATION)
            if isinstance(raw, str)
            else _UNKNOWN_INTEGRATION
        )
        if "integration" in clean:
            clean["integration"] = integration
        if "message" in clean:
            clean["message"] = _health_message(integration, payload.get("message"), payload.get("path"))
    return clean


# ---------------------------------------------------------------------------
# Safe stored delivery errors
# ---------------------------------------------------------------------------

_FALLBACK_ERROR = "delivery_failed"
_HTTP_ERROR_RE = re.compile(r"HTTP [1-5][0-9]{2}")
# CamelCase exception-class label, e.g. ConnectError / ReadTimeout / OSError.
_CLASS_LABEL_RE = re.compile(r"[A-Z][a-z0-9]*(?:[A-Z][A-Za-z0-9]*)+")
_MAX_LABEL_LENGTH = 64


def sanitize_last_error(value: Any) -> str:
    """Reduce a delivery error to a bounded safe form.

    Accepts only ``HTTP <status>`` or a simple exception-class label;
    anything else (free text, URLs, hostnames, ...) maps to
    ``delivery_failed``. An empty value stays empty.
    """
    if not isinstance(value, str) or not value:
        return ""
    if _HTTP_ERROR_RE.fullmatch(value):
        return value
    if len(value) <= _MAX_LABEL_LENGTH and _CLASS_LABEL_RE.fullmatch(value):
        return value
    return _FALLBACK_ERROR


def sanitize_connect_errors() -> int:
    """Scrub unsafe ``last_error`` values already stored in settings.

    Returns the number of entries changed. Raises on load/save failure; the
    startup caller logs only a constant message (the error text itself could
    carry a stored URL).
    """
    settings = load_settings()
    changed = 0
    for entry in settings.connect:
        clean = sanitize_last_error(entry.last_error)
        if clean != entry.last_error:
            entry.last_error = clean
            changed += 1
    if changed:
        save_settings(settings)
    return changed


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _new_client() -> httpx.AsyncClient:
    """Build the default outbound client. A separate, patchable seam so
    tests can swap in an httpx.MockTransport-backed client without
    monkeypatching the shared httpx module."""
    return httpx.AsyncClient()


_webhook_send_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "audiarr_connect_webhook_send_active", default=False
)


class _SuppressHttpxRequestLog(logging.Filter):
    """Drop httpx's ``HTTP Request:`` INFO record while a webhook POST runs.

    httpx logs the full request URL (userinfo, query token) itself, outside
    our control. The ContextVar scopes the suppression to the task sending a
    Connect webhook, so httpx logging for unrelated requests is untouched.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not _webhook_send_active.get():
            return True
        return not str(record.msg).startswith("HTTP Request:")


# Logger-level filters only see records logged directly on "httpx", which is
# where httpx emits its request line. addFilter is idempotent per instance.
logging.getLogger("httpx").addFilter(_SuppressHttpxRequestLog())


async def _post_once(
    client: httpx.AsyncClient, url: str, body: dict, headers: dict
) -> tuple[bool, int | None, str]:
    """Single POST attempt. Returns (delivered, status_code, error).

    ``error`` is only ever ``HTTP <status>`` or the exception class name:
    ``str(exc)`` can carry the webhook URL (userinfo, query token) and must
    never reach storage, logs or the API.
    """
    token = _webhook_send_active.set(True)
    try:
        response = await client.post(url, json=body, headers=headers, timeout=_TIMEOUT_SECONDS)
        if 200 <= response.status_code < 300:
            return True, response.status_code, ""
        return False, response.status_code, sanitize_last_error(f"HTTP {response.status_code}")
    except Exception as exc:  # noqa: BLE001 -- never let transport text escape
        return False, None, sanitize_last_error(type(exc).__name__)
    finally:
        _webhook_send_active.reset(token)


async def _send(
    notif: ConnectNotification,
    event: str,
    data: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> tuple[bool, int | None, str]:
    """POST one event to one webhook, retrying once on failure.

    Never raises: transport errors are folded into the (delivered, ...)
    result just like a non-2xx response.
    """
    body = {
        "event": event,
        "app": "Audiarr",
        "timestamp": datetime.now(UTC).isoformat(),
        "data": data,
    }
    headers = {}
    if notif.header_name and notif.header_value:
        headers[notif.header_name] = notif.header_value

    owns_client = client is None
    http_client = client or _new_client()
    try:
        delivered, status_code, error = await _post_once(http_client, notif.url, body, headers)
        if not delivered:
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
            delivered, status_code, error = await _post_once(http_client, notif.url, body, headers)
        return delivered, status_code, error
    finally:
        if owns_client:
            await http_client.aclose()


def _record_result(
    notif_id: str, event: str, status: str, status_code: int | None, error: str
) -> None:
    """Persist delivery metadata on the matching Settings.connect entry.

    A no-op if ``notif_id`` doesn't match any stored entry (e.g. a test
    fired against an unsaved, not-yet-persisted webhook row).
    """
    if not notif_id:
        return
    settings = load_settings()
    for entry in settings.connect:
        if entry.id == notif_id:
            entry.last_event = event
            entry.last_status = status
            entry.last_status_code = status_code
            entry.last_error = sanitize_last_error(error)
            entry.last_delivered_at = _utc_now()
            save_settings(settings)
            return


async def dispatch_event(event: str, payload: dict[str, Any]) -> None:
    """Best-effort: send ``event`` to every enabled webhook that wants it.

    Events missing from ``_EVENT_FLAGS`` are dropped before settings are
    even loaded. The payload is sanitized once (see _sanitize_payload) and
    the caller's dict is never mutated. Looked up fresh from settings on
    every call (no caching) so a recent Connect edit takes effect
    immediately. Never raises -- callers on the grab/import/health-check
    paths must keep working even if every webhook is unreachable or
    misconfigured. Failures log only the exception class: its text may
    carry a webhook URL.
    """
    flag = _EVENT_FLAGS.get(event) if isinstance(event, str) else None
    if flag is None:
        # Constant message: an unknown event name is caller-controlled.
        log.warning("connect: dropped unregistered event")
        return
    try:
        settings = load_settings()
    except Exception as exc:  # noqa: BLE001 -- must never break the caller
        log.warning(
            "connect: failed to load settings for event %r (%s)", event, type(exc).__name__
        )
        return

    targets = [n for n in settings.connect if n.enabled and n.url and getattr(n, flag, False)]
    if not targets:
        return
    try:
        data = _sanitize_payload(event, payload)
    except Exception as exc:  # noqa: BLE001 -- fail closed: send nothing
        log.warning("connect: payload sanitizing failed (%s)", type(exc).__name__)
        return
    for notif in targets:
        try:
            delivered, status_code, error = await _send(notif, event, data)
            status = "delivered" if delivered else "failed"
            log.info("connect: event=%s status=%s code=%s", event, status, status_code)
            _record_result(notif.id, event, status, status_code, error)
        except Exception as exc:  # noqa: BLE001 -- one bad webhook must not skip the rest
            log.warning(
                "connect: dispatch of event=%s failed unexpectedly (%s)",
                event,
                type(exc).__name__,
            )


async def send_test_event(notif: ConnectNotification) -> tuple[bool, int | None, str]:
    """Send a sample ``test`` event to exactly one webhook, ignoring its
    enabled/event flags, and record the outcome as ``last_status="tested"``.
    """
    delivered, status_code, error = await _send(
        notif, "test", {"message": "This is a test event from Audiarr."}
    )
    _record_result(notif.id, "test", "tested" if delivered else "failed", status_code, error)
    return delivered, status_code, error
