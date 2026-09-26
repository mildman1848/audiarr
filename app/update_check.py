"""Display-only update check against the GitHub releases API (issue #33).

Audiarr never auto-updates. This module fetches the latest GitHub release
for ``settings.updates.repository``, compares it against the running
version, and persists the result into settings (via
app.config.load_settings/save_settings) so the System/Status page and
Settings -> General can show it without re-querying GitHub on every page
load. The HTTP client is injected for testability, matching the pattern
used by app/connections/prowlarr.py.

Privacy: when ``settings.updates.check_enabled`` is False, no HTTP client
is ever constructed and no request is made.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx

from app import __version__
from app.config import load_settings, save_settings

log = logging.getLogger("audiarr.update_check")

_VERSION_RE = re.compile(r"^(\d+(?:\.\d+)*)")


def _parse_version(raw: str) -> tuple[int, ...] | None:
    """Parse a leading dotted-numeric version, e.g. 'v1.2.3' or '0.5.2-beta'.

    Returns None (never raises) if no numeric dotted version is found at
    the start of the string.
    """
    if not raw:
        return None
    text = raw.strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    match = _VERSION_RE.match(text)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def compare_versions(current: str, latest: str) -> int:
    """Compare two version strings: -1 current<latest, 0 equal/unknown, 1 current>latest.

    Unparseable input on either side is treated as "unknown" (0) rather
    than raising or claiming an update is available.
    """
    current_parts = _parse_version(current)
    latest_parts = _parse_version(latest)
    if current_parts is None or latest_parts is None:
        return 0
    length = max(len(current_parts), len(latest_parts))
    current_padded = current_parts + (0,) * (length - len(current_parts))
    latest_padded = latest_parts + (0,) * (length - len(latest_parts))
    if current_padded < latest_padded:
        return -1
    if current_padded > latest_padded:
        return 1
    return 0


def _normalize_tag(tag: str) -> str:
    """Strip a leading 'v'/'V' from a release tag when followed by a digit."""
    text = tag.strip()
    if len(text) > 1 and text[0] in ("v", "V") and text[1].isdigit():
        return text[1:]
    return text


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


async def check_for_updates(force: bool = False, client: httpx.AsyncClient | None = None) -> dict:
    """Run (or skip) a display-only update check and persist the result.

    ``force`` is accepted for API stability with a future scheduler but
    this slice has no caching/throttling to bypass -- every call performs
    a live check unless disabled. Disabled always short-circuits before
    any client is created, regardless of ``force``.
    """
    del force  # no throttling in this slice; see docstring
    settings = load_settings()

    if not settings.updates.check_enabled:
        return {
            "enabled": False,
            "current_version": __version__,
            "latest_version": settings.updates.latest_version,
            "latest_url": settings.updates.latest_url,
            "latest_name": settings.updates.latest_name,
            "update_available": settings.updates.update_available,
            "checked_at": settings.updates.last_checked_at,
            "error": "",
        }

    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=10.0)

    latest_version = settings.updates.latest_version
    latest_url = settings.updates.latest_url
    latest_name = settings.updates.latest_name
    update_available = settings.updates.update_available
    error = ""

    try:
        response = await http_client.get(
            settings.updates.releases_url,
            headers={
                "User-Agent": f"audiarr-update-check/{__version__} "
                f"(+https://github.com/{settings.updates.repository})",
                "Accept": "application/vnd.github+json",
            },
        )
        if response.status_code != 200:
            error = f"HTTP {response.status_code}"
            log.info("Update check: unexpected status %s", response.status_code)
        else:
            try:
                data = response.json()
            except ValueError:
                data = None
            if not isinstance(data, dict):
                error = "unexpected response shape"
                log.warning("Update check: response was not a JSON object")
            elif data.get("draft"):
                log.debug("Update check: latest release is a draft, ignoring")
            elif data.get("prerelease"):
                log.debug("Update check: latest release is a prerelease, ignoring")
            else:
                tag = str(data.get("tag_name") or "")
                latest_version = _normalize_tag(tag)
                latest_url = str(data.get("html_url") or "")
                latest_name = str(data.get("name") or tag)
                update_available = compare_versions(__version__, latest_version) < 0
    except httpx.HTTPError as exc:
        log.warning("Update check failed: %s", exc)
        error = str(exc)
    finally:
        if owns_client:
            await http_client.aclose()

    checked_at = _now()
    settings.updates.last_checked_at = checked_at
    settings.updates.latest_version = latest_version
    settings.updates.latest_url = latest_url
    settings.updates.latest_name = latest_name
    settings.updates.update_available = update_available
    settings.updates.last_error = error
    save_settings(settings)

    return {
        "enabled": True,
        "current_version": __version__,
        "latest_version": latest_version,
        "latest_url": latest_url,
        "latest_name": latest_name,
        "update_available": update_available,
        "checked_at": checked_at,
        "error": error,
    }
