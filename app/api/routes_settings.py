"""Settings read/write endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.auth import hash_password
from app.config import load_settings, save_settings
from app.models.settings import DownloadClient, Settings

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

    Entries are matched by ``(type, name)``. A renamed entry is matched by
    type only when that is unambiguous (exactly one unmatched stored and one
    unmatched incoming client of that type). If a stored secret would be
    silently dropped because the match is ambiguous, the save is rejected
    with a 422 instead. A non-blank value always wins.
    """
    previous = {(c.type, c.name): c for c in stored.download_clients}
    matched: dict[int, DownloadClient] = {}
    for idx, client in enumerate(incoming.download_clients):
        old = previous.get((client.type, client.name))
        if old is not None:
            matched[idx] = old
    used = {id(old) for old in matched.values()}

    for type_ in {c.type for c in incoming.download_clients}:
        new_idx = [
            i for i, c in enumerate(incoming.download_clients)
            if c.type == type_ and i not in matched
        ]
        old_left = [c for c in stored.download_clients if c.type == type_ and id(c) not in used]
        if len(new_idx) == 1 and len(old_left) == 1:
            matched[new_idx[0]] = old_left[0]
            continue
        would_lose = any(
            (not incoming.download_clients[i].api_key and any(c.api_key for c in old_left))
            or (not incoming.download_clients[i].password and any(c.password for c in old_left))
            for i in new_idx
        )
        if would_lose:
            raise HTTPException(
                422,
                f"Ambiguous {type_} download client rename: re-enter its API key / "
                "password or save one client change at a time",
            )

    for idx, old in matched.items():
        client = incoming.download_clients[idx]
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
