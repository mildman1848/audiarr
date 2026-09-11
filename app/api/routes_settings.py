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
_SETTINGS_RESPONSE_EXCLUDE = {"auth": {"password_hash"}}


@router.get("/api/v1/settings", response_model=Settings, response_model_exclude=_SETTINGS_RESPONSE_EXCLUDE)
async def get_settings() -> Settings:
    return load_settings()


@router.put("/api/v1/settings", response_model=Settings, response_model_exclude=_SETTINGS_RESPONSE_EXCLUDE)
async def put_settings(settings: Settings) -> Settings:
    if settings.auth.password:
        settings.auth.password_hash = hash_password(settings.auth.password)
    else:
        # Empty password on PUT keeps whatever hash is already stored.
        settings.auth.password_hash = load_settings().auth.password_hash
    settings.auth.password = ""

    save_settings(settings)
    log.info("Settings updated via API")
    return settings
