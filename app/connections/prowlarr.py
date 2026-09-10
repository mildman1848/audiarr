"""Client for a Prowlarr indexer manager.

Audiarr does not implement search/grab against Prowlarr yet; this client
only covers the health/status check used by the Settings "Indexers"
section so users can verify their Prowlarr URL and API key.

Prowlarr uses the Servarr v3 API shape: ``GET /api/v1/system/status`` with
the API key in an ``X-Api-Key`` header. The key can also come from a
``FILE__PROWLARR_API_KEY`` secret file per LSIO convention (see
app/secrets_util.py). The HTTP client is injected for testability.
"""

from __future__ import annotations

import logging

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.prowlarr")


class ProwlarrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or resolve_secret("PROWLARR_API_KEY")
        self._client = client

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"X-Api-Key": self.api_key}
        return {}

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=self.base_url, timeout=10.0), True

    async def status(self) -> dict | None:
        """Return the Prowlarr system status dict, or None on failure.

        Success means: HTTP 200 and a parseable JSON object. Prowlarr's
        status payload carries fields like ``version`` and ``appName``;
        we return the whole dict so the caller can surface the version.
        """
        client, owns_client = await self._get_client()
        try:
            response = await client.get("/api/v1/system/status", headers=self._headers())
            if response.status_code != 200:
                log.warning("Prowlarr status check: HTTP %s", response.status_code)
                return None
            try:
                data = response.json()
            except ValueError:
                log.warning("Prowlarr status check: response was not JSON")
                return None
            if not isinstance(data, dict):
                log.warning("Prowlarr status check: unexpected JSON shape")
                return None
            return data
        except httpx.HTTPError as exc:
            log.warning("Prowlarr status check failed: %s", exc)
            return None
        finally:
            if owns_client:
                await client.aclose()
