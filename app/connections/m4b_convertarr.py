"""Client for an external m4b-convertarr instance.

Design decision (see docs/design/architecture.md "Conversion strategy"):
Audiarr delegates M4B conversion to the separate m4b-convertarr service
via HTTP instead of embedding a converter. That keeps Audiarr's container
small, lets conversion scale/fail independently, and reuses a
already-hardened LSIO/s6 image rather than duplicating ffmpeg/AtomicParsley
tooling here. Embedding a converter directly into Audiarr remains a
possible future option once the external-first integration has proven
itself; see docs/design/architecture.md for the trade-offs.
"""

from __future__ import annotations

import logging

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.m4b_convertarr")


class M4BConvertarrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        webhook_path: str = "/api/v1/convert",
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.webhook_path = webhook_path
        self.api_key = api_key or resolve_secret("M4B_CONVERTARR_API_KEY")
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
        client, owns_client = await self._get_client()
        try:
            response = await client.get("/health", headers=self._headers())
            return response.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("m4b-convertarr health check failed: %s", exc)
            return False
        finally:
            if owns_client:
                await client.aclose()

    async def submit_conversion(self, source_path: str, target_format: str = "m4b") -> bool:
        """POST a conversion job/webhook to m4b-convertarr. Placeholder payload shape."""
        client, owns_client = await self._get_client()
        try:
            response = await client.post(
                self.webhook_path,
                json={"source_path": source_path, "target_format": target_format},
                headers=self._headers(),
            )
            return response.status_code in (200, 201, 202)
        except httpx.HTTPError as exc:
            log.warning("m4b-convertarr conversion submission failed: %s", exc)
            return False
        finally:
            if owns_client:
                await client.aclose()
