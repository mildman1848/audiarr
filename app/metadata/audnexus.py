"""Audnexus metadata provider (fallback).

Audnexus (https://api.audnex.us) is a community-run, freely usable API
that aggregates Audible catalog metadata by ASIN/region, without requiring
Audible credentials. Audiarr uses it as the fallback provider when the
primary Audible provider stub returns nothing.

The HTTP client is injected so tests can use ``httpx.MockTransport``
instead of a real network call (see tests/test_metadata.py).
"""

from __future__ import annotations

import logging

import httpx

from app.metadata.base import BookMetadata, MetadataProvider

log = logging.getLogger("audiarr.metadata.audnexus")


class AudnexusProvider(MetadataProvider):
    """Fallback provider backed by the Audnexus book search endpoint."""

    name = "audnexus"

    def __init__(self, base_url: str = "https://api.audnex.us", client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        # If no client is supplied, one is created lazily per-call so the
        # provider works standalone; tests pass a mocked client instead.
        self._client = client

    async def search(self, query: str, locale: str) -> list[BookMetadata]:
        if not query.strip():
            return []

        params = {"title": query, "region": locale}
        log.info("Audnexus search for %r region=%s", query, locale)

        client = self._client
        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

        try:
            response = await client.get("/books", params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            log.warning("Audnexus search failed: %s", exc)
            return []
        finally:
            if owns_client:
                await client.aclose()

        results: list[BookMetadata] = []
        for item in payload if isinstance(payload, list) else payload.get("results", []):
            results.append(
                BookMetadata(
                    source=self.name,
                    source_id=item.get("asin", ""),
                    title=item.get("title", "Unknown title"),
                    authors=[a.get("name", "") for a in item.get("authors", [])],
                    narrators=[n.get("name", "") for n in item.get("narrators", [])],
                    asin=item.get("asin"),
                    publisher=item.get("publisherName"),
                    release_date=item.get("releaseDate"),
                    description=item.get("summary"),
                    cover_url=item.get("image"),
                    language=item.get("language", locale),
                )
            )
        return results
