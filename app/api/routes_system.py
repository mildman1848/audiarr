"""Health and system status endpoints."""

from __future__ import annotations

import platform

from fastapi import APIRouter

from app import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness probe. Intentionally has no dependency on settings/DB."""
    return {"status": "ok"}


@router.get("/api/v1/system/status")
async def system_status() -> dict:
    """Basic system info, similar in spirit to Radarr/Sonarr's system/status."""
    return {
        "appName": "Audiarr",
        "version": __version__,
        "pythonVersion": platform.python_version(),
        "osName": platform.system(),
    }
