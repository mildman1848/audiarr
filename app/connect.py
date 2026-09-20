"""Outbound webhook dispatch for Audiarr events (issue #28).

Best-effort delivery to the webhooks configured in Settings -> Connect
(``Settings.connect``, see app/models/settings.py). A delivery failure must
never break the caller's own workflow (a grab, an import, a connection
health check), so every public function here swallows its own errors and
simply records the outcome on the matching settings entry.

Secrets: ``ConnectNotification.header_value`` is never logged, and is only
ever sent as the value of the single custom header the user configured --
never as part of any other header or the JSON body.
"""

from __future__ import annotations

import asyncio
import logging
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


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _new_client() -> httpx.AsyncClient:
    """Build the default outbound client. A separate, patchable seam so
    tests can swap in an httpx.MockTransport-backed client without
    monkeypatching the shared httpx module."""
    return httpx.AsyncClient()


async def _post_once(
    client: httpx.AsyncClient, url: str, body: dict, headers: dict
) -> tuple[bool, int | None, str]:
    """Single POST attempt. Returns (delivered, status_code, error)."""
    try:
        response = await client.post(url, json=body, headers=headers, timeout=_TIMEOUT_SECONDS)
        if 200 <= response.status_code < 300:
            return True, response.status_code, ""
        return False, response.status_code, f"HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        return False, None, str(exc)


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
            entry.last_error = error
            entry.last_delivered_at = _utc_now()
            save_settings(settings)
            return


async def dispatch_event(event: str, payload: dict[str, Any]) -> None:
    """Best-effort: send ``event`` to every enabled webhook that wants it.

    Looked up fresh from settings on every call (no caching) so a recent
    Connect edit takes effect immediately. Never raises -- callers on the
    grab/import/health-check paths must keep working even if every webhook
    is unreachable or misconfigured.
    """
    flag = _EVENT_FLAGS.get(event)
    try:
        settings = load_settings()
    except Exception:  # noqa: BLE001 -- must never break the caller
        log.warning("connect: failed to load settings for event %r", event, exc_info=True)
        return

    targets = [
        n
        for n in settings.connect
        if n.enabled and n.url and (flag is None or getattr(n, flag, False))
    ]
    for notif in targets:
        try:
            delivered, status_code, error = await _send(notif, event, payload)
            status = "delivered" if delivered else "failed"
            log.info(
                "connect: event=%s webhook=%r status=%s code=%s",
                event,
                notif.name,
                status,
                status_code,
            )
            _record_result(notif.id, event, status, status_code, error)
        except Exception:  # noqa: BLE001 -- one bad webhook must not skip the rest
            log.warning("connect: dispatch to %r failed unexpectedly", notif.name, exc_info=True)


async def send_test_event(notif: ConnectNotification) -> tuple[bool, int | None, str]:
    """Send a sample ``test`` event to exactly one webhook, ignoring its
    enabled/event flags, and record the outcome as ``last_status="tested"``.
    """
    delivered, status_code, error = await _send(
        notif, "test", {"message": "This is a test event from Audiarr."}
    )
    _record_result(notif.id, "test", "tested" if delivered else "failed", status_code, error)
    return delivered, status_code, error
