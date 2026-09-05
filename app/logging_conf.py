"""Logging setup.

Uses plain stdlib logging so container logs are readable via `docker logs`
without needing a log aggregator. Level is controlled by settings (or the
AUDIARR_LOG_LEVEL env var before settings.json exists, e.g. during very
first boot).
"""

from __future__ import annotations

import logging
import os


def configure_logging(level: str | None = None) -> None:
    resolved = (level or os.environ.get("AUDIARR_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, resolved, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    # Never log secret values; provider/connection modules only log that a
    # secret was resolved and from which source (see app/secrets_util.py).
