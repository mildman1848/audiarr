"""Client for a SABnzbd download client.

Audiarr does not implement the full grab/queue workflow yet; this client
only covers the health/version check used by the Settings "Download
Clients" section so users can verify their SABnzbd URL and API key before
the rest of the pipeline lands.

SABnzbd exposes a single ``/api`` endpoint driven by a ``mode`` query
parameter. The version probe is unauthenticated-friendly on some setups
but normally needs the API key, so we always send it when present. The
API key can also come from a ``FILE__SABNZBD_API_KEY`` secret file per
LSIO convention (see app/secrets_util.py). The HTTP client is injected
for testability.
"""

from __future__ import annotations

import logging

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.sabnzbd")


class SABnzbdClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        # Fall back to the FILE__/env secret if no key was passed explicitly
        # (e.g. loaded straight from settings.json, which may be blank).
        self.api_key = api_key or resolve_secret("SABNZBD_API_KEY")
        self._client = client

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=self.base_url, timeout=10.0), True

    async def version(self) -> str | None:
        """Return the SABnzbd version string, or None if it can't be read.

        Success means: HTTP 200, a parseable JSON body, and a
        version-like ``version`` field. Anything else (transport error,
        non-200, HTML login page, missing field) returns None.
        """
        client, owns_client = await self._get_client()
        try:
            params = {"mode": "version", "output": "json"}
            if self.api_key:
                params["apikey"] = self.api_key
            response = await client.get("/api", params=params)
            if response.status_code != 200:
                log.warning("SABnzbd version check: HTTP %s", response.status_code)
                return None
            try:
                data = response.json()
            except ValueError:
                log.warning("SABnzbd version check: response was not JSON")
                return None
            version = data.get("version") if isinstance(data, dict) else None
            if not isinstance(version, str) or not version.strip():
                log.warning("SABnzbd version check: no version field in response")
                return None
            return version.strip()
        except httpx.HTTPError as exc:
            log.warning("SABnzbd version check failed: %s", exc)
            return None
        finally:
            if owns_client:
                await client.aclose()
