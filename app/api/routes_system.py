"""Health and system status endpoints."""

from __future__ import annotations

import platform

from fastapi import APIRouter

from app import __version__
from app.config import load_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness probe. Intentionally has no dependency on settings/DB."""
    return {"status": "ok"}


@router.get("/api/v1/system/status")
async def system_status() -> dict:
    """Basic system info, similar in spirit to Radarr/Sonarr's system/status.

    ``updates``/``backup``/``logging`` are derived from the settings
    document (see app/models/settings.py) so the System/Status page can show
    the configured maintenance state without a dedicated endpoint.
    """
    settings = load_settings()
    return {
        "appName": "Audiarr",
        "version": __version__,
        "pythonVersion": platform.python_version(),
        "osName": platform.system(),
        "updates": {
            "branch": settings.updates.branch,
            "automatic": settings.updates.automatic,
        },
        "backup": {
            "folder": settings.backup.folder,
            "intervalHours": settings.backup.interval_hours,
            "retentionCopies": settings.backup.retention_copies,
        },
        "logging": {
            "level": settings.logging.level,
            "retentionDays": settings.logging.retention_days,
        },
    }
