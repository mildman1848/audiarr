"""Client for an existing Audiobookshelf server.

Audiarr does not replace Audiobookshelf's player; it manages metadata and
library organization and can ask Audiobookshelf to rescan a library after
files change. The API key can come from the settings document or, per LSIO
convention, a ``FILE__AUDIOBOOKSHELF_API_KEY`` secret file (see
app/secrets_util.py). The HTTP client is injected for testability.
"""

from __future__ import annotations

import logging

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.audiobookshelf")


class AudiobookshelfClient:
    def __init__(self, base_url: str, api_key: str | None = None, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        # Fall back to the FILE__/env secret if no key was passed explicitly
        # (e.g. loaded straight from settings.json, which may be blank).
        self.api_key = api_key or resolve_secret("AUDIOBOOKSHELF_API_KEY")
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
