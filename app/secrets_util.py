"""Helpers for LSIO-style ``FILE__`` secret resolution.

LinuxServer.io images support passing secrets as files (e.g. from Docker
secrets or a mounted read-only file) instead of plain environment
variables. The convention is: if ``FILE__<NAME>`` is set, its value is a
path whose file contents become the value of ``<NAME>``. This module
implements that lookup once so every settings/client module behaves the
same way.

IMPORTANT: never log the resolved secret value. Only log which source was
used (env var vs file) and the variable name.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("audiarr.secrets")


def resolve_secret(name: str, default: str | None = None) -> str | None:
    """Resolve a secret value for env var ``name``.

    Resolution order:
    1. ``FILE__<name>`` — read and strip the file at that path.
    2. ``<name>`` — use the plain environment variable value.
    3. ``default``.

    The returned value is never logged by this function.
    """
    file_var = f"FILE__{name}"
    file_path = os.environ.get(file_var)
    if file_path:
        try:
            value = Path(file_path).read_text(encoding="utf-8").strip()
            log.info("Resolved secret %s from FILE__ path", name)
            return value
        except OSError:
            log.warning(
                "FILE__%s is set but the file could not be read; "
                "falling back to plain env var or default",
                name,
            )

    plain = os.environ.get(name)
    if plain:
        log.info("Resolved secret %s from plain environment variable", name)
        return plain

    return default


def mask(value: str | None) -> str:
    """Return a display-safe placeholder for a secret, for logs/UI only."""
    if not value:
        return "(not set)"
    return "***" + value[-2:] if len(value) > 2 else "***"
