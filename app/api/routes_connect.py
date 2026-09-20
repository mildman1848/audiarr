"""Connect (outbound webhook) test endpoint.

CRUD for the webhook list itself goes through the generic settings
document (GET/PUT /api/v1/settings, see routes_settings.py) -- this module
only adds the "send a sample event" action used by the Settings -> Connect
page's per-row Test button.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_settings
from app.connect import send_test_event
from app.models.settings import ConnectNotification

log = logging.getLogger("audiarr.api.connect")

router = APIRouter()


class ConnectTestRequest(BaseModel):
    url: str
    header_name: str = ""
    header_value: str = ""


class ConnectTestResponse(BaseModel):
    ok: bool
    message: str
    status_code: int | None = None


def _stored_connect_values(
    connect_id: str, url: str, header_name: str, header_value: str
) -> tuple[str, str, str]:
    """Fill blank url/header from the persisted webhook, if one matches.

    Mirrors _stored_sabnzbd_values/_stored_prowlarr_values in
    routes_connections.py: the browser never renders a saved header_value
    back into the page, so a Test click with a blank header field must
    still use the stored secret rather than sending no header at all.
    """
    stored = next((n for n in load_settings().connect if n.id == connect_id), None)
    if stored is None:
        return url, header_name, header_value
    return (
        url or stored.url,
        header_name or stored.header_name,
        header_value or stored.header_value,
    )


@router.post("/api/v1/connect/test/{connect_id}", response_model=ConnectTestResponse)
async def test_connect_webhook(connect_id: str, request: ConnectTestRequest) -> ConnectTestResponse:
    """Send a sample ``test`` event to one webhook, regardless of its
    on_grab/on_import/on_health_issue toggles.

    Works for unsaved rows too: ``connect_id`` only needs to match a
    persisted entry when the caller wants delivery metadata recorded; url
    and header come from the request body (with a fallback to the stored
    entry for a blank/masked header_value).
    """
    url, header_name, header_value = _stored_connect_values(
        connect_id, request.url.strip(), request.header_name.strip(), request.header_value
    )
    if not url:
        raise HTTPException(400, "No webhook URL configured")

    notif = ConnectNotification(
        id=connect_id, name="test", url=url, header_name=header_name, header_value=header_value
    )
    delivered, status_code, error = await send_test_event(notif)
    log.info("Connect test %r -> delivered=%s code=%s", connect_id, delivered, status_code)
    message = "Test event delivered" if delivered else (error or "Delivery failed")
    return ConnectTestResponse(ok=delivered, message=message, status_code=status_code)
