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
