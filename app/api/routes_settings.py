"""Settings read/write endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.config import load_settings, save_settings
from app.models.settings import Settings

log = logging.getLogger("audiarr.api.settings")

router = APIRouter()


@router.get("/api/v1/settings", response_model=Settings)
async def get_settings() -> Settings:
    return load_settings()


@router.put("/api/v1/settings", response_model=Settings)
async def put_settings(settings: Settings) -> Settings:
    save_settings(settings)
    log.info("Settings updated via API")
    return settings
