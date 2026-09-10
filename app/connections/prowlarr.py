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

    @staticmethod
    def _normalize_release(raw: dict) -> dict:
        """Map a raw Prowlarr search result onto Audiarr's release shape."""
        categories: list[str] = []
        for cat in raw.get("categories") or []:
            if isinstance(cat, dict) and cat.get("name"):
                categories.append(str(cat["name"]))
        return {
            "guid": raw.get("guid"),
            "indexer_id": raw.get("indexerId"),
            "indexer": raw.get("indexer"),
            "title": raw.get("title"),
            "size": raw.get("size"),
            "seeders": raw.get("seeders"),
            "leechers": raw.get("leechers"),
            "publish_date": raw.get("publishDate"),
            "download_url": raw.get("downloadUrl"),
            "magnet_url": raw.get("magnetUrl"),
            "protocol": raw.get("protocol"),
            "age": raw.get("age"),
            "categories": categories,
        }

    async def search(self, query: str, limit: int = 50) -> list[dict]:
        """Run an interactive release search, returning normalized dicts.

        Never raises: any transport error, non-200 response, or unexpected
        JSON shape is logged and yields an empty list so the caller can
        surface "no results" instead of a 500.
        """
        client, owns_client = await self._get_client()
        try:
            response = await client.get(
                "/api/v1/search",
                params={"query": query, "type": "search", "limit": limit},
                headers=self._headers(),
            )
            if response.status_code != 200:
                log.warning("Prowlarr search: HTTP %s", response.status_code)
                return []
            try:
                data = response.json()
            except ValueError:
                log.warning("Prowlarr search: response was not JSON")
                return []
            if not isinstance(data, list):
                log.warning("Prowlarr search: unexpected JSON shape")
                return []
            releases = [self._normalize_release(r) for r in data if isinstance(r, dict)]
            log.debug("Prowlarr search for %r -> %d release(s)", query, len(releases))
            return releases
        except httpx.HTTPError as exc:
            log.warning("Prowlarr search failed: %s", exc)
            return []
        finally:
            if owns_client:
                await client.aclose()

    async def download_nzb(self, indexer_id: int, download_url: str) -> bytes | None:
        """Fetch the NZB file for a usenet release via Prowlarr.

        Returns the raw NZB bytes, or None on any failure (transport error,
        non-200, empty body).
        """
        client, owns_client = await self._get_client()
        try:
            response = await client.get(
                f"/api/v1/indexer/{indexer_id}/download",
                params={"link": download_url},
                headers=self._headers(),
            )
            if response.status_code != 200:
                log.warning("Prowlarr NZB download: HTTP %s", response.status_code)
                return None
            content = response.content
            if not content:
                log.warning("Prowlarr NZB download: empty response body")
                return None
            log.debug(
                "Prowlarr NZB download from indexer %s -> %d byte(s)", indexer_id, len(content)
            )
            return content
        except httpx.HTTPError as exc:
            log.warning("Prowlarr NZB download failed: %s", exc)
            return None
        finally:
            if owns_client:
                await client.aclose()

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
