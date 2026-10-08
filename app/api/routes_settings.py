"""Settings read/write endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.auth import hash_password
from app.config import load_settings, save_settings
from app.models.settings import Settings

log = logging.getLogger("audiarr.api.settings")

router = APIRouter()

# auth.password is already Field(exclude=True) (write-only, never
# persisted). auth.password_hash is a plain, persisted field so it
# survives a settings save (see app/models/settings.py for why it can't
# also use Field(exclude=True)); it is excluded here, at the API-response
# boundary, so GET/PUT never echo it back.
#
# Download-client secrets (api_key, password) are write-only too: they are
# stored, but never returned. A blank value on PUT keeps the stored secret
# for the matching client (see _preserve_download_client_secrets).
_SETTINGS_RESPONSE_EXCLUDE = {
    "auth": {"password_hash"},
    "download_clients": {"__all__": {"api_key", "password"}},
}


def _preserve_download_client_secrets(incoming: Settings, stored: Settings) -> None:
    """Fill blank write-only download-client secrets from the stored entry.

    Entries are matched by ``(type, name)``; an entry with no stored match
    (new, or renamed) keeps whatever was sent. A non-blank value always wins,
    so sending a new secret replaces the stored one.
    """
    previous = {(c.type, c.name): c for c in stored.download_clients}
    for client in incoming.download_clients:
        old = previous.get((client.type, client.name))
        if old is None:
            continue
        if not client.api_key:
            client.api_key = old.api_key
        if not client.password:
            client.password = old.password


@router.get("/api/v1/settings", response_model=Settings, response_model_exclude=_SETTINGS_RESPONSE_EXCLUDE)
async def get_settings() -> Settings:
    return load_settings()


@router.put("/api/v1/settings", response_model=Settings, response_model_exclude=_SETTINGS_RESPONSE_EXCLUDE)
async def put_settings(settings: Settings) -> Settings:
    stored = load_settings()
    if settings.auth.password:
        settings.auth.password_hash = hash_password(settings.auth.password)
    else:
        # Empty password on PUT keeps whatever hash is already stored.
        settings.auth.password_hash = stored.auth.password_hash
    settings.auth.password = ""
    _preserve_download_client_secrets(settings, stored)

    save_settings(settings)
    log.info("Settings updated via API")
    return settings
