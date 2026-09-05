"""Runtime paths and settings persistence.

Follows the LSIO convention: application state lives under /config so it
survives container recreation. For local development (no Docker), the
config directory defaults to ./config relative to the current working
directory, controlled by the AUDIARR_CONFIG_DIR environment variable.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from app.models.settings import Settings

log = logging.getLogger("audiarr.config")


def get_config_dir() -> Path:
    """Return the Audiarr config directory, creating it if needed.

    In the container this resolves to /config/audiarr. Locally it defaults
    to ./config/audiarr so contributors can run the app without Docker.
    """
    base = os.environ.get("AUDIARR_CONFIG_DIR", "./config")
    config_dir = Path(base) / "audiarr"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_settings_path() -> Path:
    return get_config_dir() / "settings.json"


def get_db_path() -> Path:
    return get_config_dir() / "audiarr.db"


def load_settings() -> Settings:
    """Load settings from disk, falling back to defaults on first run."""
    path = get_settings_path()
    if not path.exists():
        log.info("No settings.json found at %s; using defaults", path)
        return Settings()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Settings.model_validate(raw)
    except (OSError, ValueError) as exc:
        log.error("Failed to load settings from %s: %s; using defaults", path, exc)
        return Settings()


def save_settings(settings: Settings) -> None:
    """Persist settings to disk as pretty-printed JSON."""
    path = get_settings_path()
    path.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    log.info("Saved settings to %s", path)
