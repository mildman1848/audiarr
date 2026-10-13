"""Client for an existing Audiobookshelf server.

Audiarr does not replace Audiobookshelf's player; it manages metadata and
library organization and can ask Audiobookshelf to rescan a library after
files change. The API key can come from the settings document or, per LSIO
convention, a ``FILE__AUDIOBOOKSHELF_API_KEY`` secret file (see
app/secrets_util.py), but only when ``api_key`` is ``None`` and ``base_url``
matches the non-secret ``AUDIOBOOKSHELF_URL`` (see
``resolve_audiobookshelf_api_key``): an explicit empty string means "send no
key" (used for connection tests against a destination that is not the
configured server). The HTTP client is injected for testability.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.audiobookshelf")

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Raw C0/C1 control characters, checked before parsing because URL parsers
# silently drop CR/LF/TAB.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_URL_FORBIDDEN_CHARS = re.compile(r"[\s\\]")


def normalize_audiobookshelf_base_url(url: str | None) -> tuple[str, str, int, str] | None:
    """``(scheme, host, effective port, base path)`` of a Audiobookshelf base URL.

    Strict on purpose, since the result decides where a deployment secret may
    be sent: only ``http``/``https`` with a host, no userinfo, query, fragment,
    control characters, whitespace or backslashes, and a valid port. Scheme and
    host are case-folded, a missing port becomes the scheme default, and one
    trailing slash on the path is ignored; nothing else is normalized. Returns
    None for anything unset or malformed.
    """
    if not url:
        return None
    if _CONTROL_CHARS.search(url) or _URL_FORBIDDEN_CHARS.search(url) or "?" in url or "#" in url:
        return None
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            return None
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if port is None:
        port = _DEFAULT_PORTS[scheme]
    elif port == 0:
        return None
    path = parts.path.removesuffix("/")
    if path.endswith("/"):
        return None
    return scheme, host.lower(), port, path


def is_trusted_audiobookshelf_url(base_url: str | None) -> bool:
    """True when ``base_url`` is exactly the trusted ``AUDIOBOOKSHELF_URL`` destination.

    ``AUDIOBOOKSHELF_URL`` is a non-secret deployment setting. If it is unset or
    malformed nothing is trusted, so the environment key is never used.
    """
    trusted = normalize_audiobookshelf_base_url(os.environ.get("AUDIOBOOKSHELF_URL"))
    if trusted is None:
        return False
    return trusted == normalize_audiobookshelf_base_url(base_url)


def resolve_audiobookshelf_api_key(base_url: str | None) -> str:
    """The deployment API key (env/``FILE__``) for requests to ``base_url``.

    Returned only when ``base_url`` matches the trusted ``AUDIOBOOKSHELF_URL``, so a
    settings change can not re-aim the deployment secret. Otherwise "".
    """
    if not is_trusted_audiobookshelf_url(base_url):
        log.debug("Audiobookshelf environment API key not used: URL is not the trusted AUDIOBOOKSHELF_URL")
        return ""
    return resolve_secret("AUDIOBOOKSHELF_API_KEY") or ""


class AudiobookshelfClient:
    def __init__(self, base_url: str, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        # None falls back to the FILE__/env secret (e.g. loaded straight from
        # settings.json, which may be blank), but only for the destination
        # named by AUDIOBOOKSHELF_URL. "" is an explicit "no key" so a caller
        # can keep the deployment secret away from a URL it does not control.
        self.api_key = resolve_audiobookshelf_api_key(base_url) if api_key is None else api_key
        self._client = client

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=self.base_url, timeout=10.0), True

    async def health(self) -> bool:
        """Return True if the Audiobookshelf server responds healthy."""
        client, owns_client = await self._get_client()
        try:
            response = await client.get("/healthcheck", headers=self._headers())
            return response.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("Audiobookshelf health check failed: %s", exc)
            return False
        finally:
            if owns_client:
                await client.aclose()

    async def scan_library(self, library_id: str) -> bool:
        """Trigger a library scan. Placeholder for the real Audiobookshelf API shape."""
        client, owns_client = await self._get_client()
        try:
            response = await client.post(f"/api/libraries/{library_id}/scan", headers=self._headers())
            return response.status_code in (200, 202)
        except httpx.HTTPError as exc:
            log.warning("Audiobookshelf library scan failed: %s", exc)
            return False
        finally:
            if owns_client:
                await client.aclose()


async def notify_library_changed() -> bool:
    """Ask the configured Audiobookshelf server to rescan its library.

    Called after a successful import run and after a conversion webhook
    completes so Audiobookshelf picks up new/changed files without the
    user hitting "Scan" manually.

    Best-effort by design: returns early when the connection is disabled
    or no ``library_id`` is configured, and swallows every error (logging
    it). An auto-refresh failure must NEVER break the import pipeline or a
    webhook completion. Returns True only when a scan was accepted.
    """
    from app.config import load_settings

    conn = load_settings().connections.audiobookshelf
    if not conn.enabled:
        log.debug("Audiobookshelf auto-refresh skipped: connection disabled")
        return False
    if not conn.library_id:
        log.debug("Audiobookshelf auto-refresh skipped: no library_id configured")
        return False

    try:
        client = AudiobookshelfClient(base_url=conn.url, api_key=conn.api_key or None)
        ok = await client.scan_library(conn.library_id)
        log.info(
            "Audiobookshelf auto-refresh: scan of library %s -> %s",
            conn.library_id,
            "accepted" if ok else "rejected",
        )
        return ok
    except Exception:  # noqa: BLE001 — auto-refresh must never break the caller
        log.warning("Audiobookshelf auto-refresh failed", exc_info=True)
        return False
