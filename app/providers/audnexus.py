"""Audnexus provider — real async HTTP client with injectable transport.

Audnexus (https://audnex.us) is a community-run free API aggregating
Audible catalog metadata. Key endpoints used here:

- ``GET /search/books?title=<q>&region=<r>`` — search by title
- ``GET /books/<asin>?region=<r>`` — full book details

The HTTP client is injectable so tests run against ``httpx.MockTransport``
instead of the live API (no network dependency in CI).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.providers.base import (
    BaseMetadataProvider,
    BookDetailInfo,
    BookQuickInfo,
    BrowseResponse,
    SearchResponse,
)

log = logging.getLogger("audiarr.providers.audnexus")


class AudnexusProvider(BaseMetadataProvider):
    """Async Audnexus provider.

    If a ``client`` is injected it is used as-is (tests do this with a
    MockTransport). Otherwise a short-lived client is created per call,
    which is fine for the MVP's request volume.
    """

    provider_name: str = "audnexus"
    provider_url: str = "https://api.audnex.us"
    provider_description: str = (
        "Community-run free API aggregating Audible catalog metadata."
    )
    provider_version: str = "0.1.0"
    requires_api_key: bool = False

    def __init__(
        self,
        *,
        base_url: str = "https://api.audnex.us",
        api_key: str = "",
        region: str = "us",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key=api_key, region=region)
        self.base_url = base_url.rstrip("/")
        self.region = region
        self._client = client

    # -- internals ---------------------------------------------------------

    def _make_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

    def _owns_client(self) -> bool:
        return self._client is None

    @staticmethod
    def _extract_items(payload: Any) -> list[dict[str, Any]]:
        """Audnexus returns either a bare list or ``{"results": [...]}``."""
        if isinstance(payload, list):
            return [p for p in payload if isinstance(p, dict)]
        if isinstance(payload, dict):
            raw = payload.get("results", payload.get("books", []))
            if isinstance(raw, list):
                return [p for p in raw if isinstance(p, dict)]
        return []

    def _map_book(self, book: dict[str, Any]) -> BookQuickInfo:
        asin = self._normalize_optional(book.get("asin"))
        return BookQuickInfo(
            provider_uid=asin or self._normalize_string(book.get("name")),
            provider_name=self.provider_name,
            title=self._normalize_string(book.get("title") or book.get("name")),
            subtitle=self._normalize_string(book.get("subtitle")),
            authors=self._normalize_authors(book.get("authors")),
            narrators=self._normalize_narrators(book.get("narrators")),
            asin=asin,
            isbn=self._normalize_optional(book.get("isbn")),
            cover_url=self._normalize_optional(book.get("image")),
            locale=self.region,
        )

    # -- contract ----------------------------------------------------------

    async def healthcheck(self) -> bool:
        client = self._make_client()
        try:
            response = await client.get("/health")
            response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("Audnexus healthcheck failed: %s", exc)
            return False
        finally:
            if self._owns_client():
                await client.aclose()

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        if not query.strip():
            return SearchResponse(results=[], query_used=query)

        params: dict[str, Any] = {"title": query, "region": self.region}
        log.info("Audnexus search for %r region=%s", query, self.region)

        client = self._make_client()
        try:
            response = await client.get("/search/books", params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            log.warning("Audnexus search failed: %s", exc)
            return SearchResponse(results=[], query_used=query)
        finally:
            if self._owns_client():
                await client.aclose()

        results = [self._map_book(b) for b in self._extract_items(payload)]
        log.debug("Audnexus search for %r returned %d hits", query, len(results))
        return SearchResponse(results=results, query_used=query)

    async def browse(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        sort: str = "newest",
        filter: str = "",
    ) -> BrowseResponse:
        # Audnexus has no browse endpoint; browsing falls through to the
        # next provider in the chain by returning empty results.
        return BrowseResponse(results=[], page=page, page_size=page_size)

    async def get_detail(self, external_id: str, **kwargs: Any) -> BookDetailInfo | None:
        region = kwargs.get("region", self.region)
        client = self._make_client()
        try:
            response = await client.get(
                f"/books/{external_id}", params={"region": region}
            )
            response.raise_for_status()
            book = response.json()
        except httpx.HTTPError as exc:
            log.warning("Audnexus detail fetch for %s failed: %s", external_id, exc)
            return None
        finally:
            if self._owns_client():
                await client.aclose()

        if not isinstance(book, dict):
            return None

        return BookDetailInfo(
            provider_uid=self._normalize_optional(book.get("asin")) or external_id,
            provider_name=self.provider_name,
            provider_external_id=external_id,
            title=self._normalize_string(book.get("title") or book.get("name")),
            subtitle=self._normalize_string(book.get("subtitle")),
            description=self._normalize_string(book.get("summary")),
            authors=self._normalize_authors(book.get("authors")),
            narrators=self._normalize_narrators(book.get("narrators")),
            genres=self._normalize_list(book.get("genres")),
            language=self._normalize_string(book.get("language")),
            duration_seconds=int(book.get("runtimeLengthMin", 0) or 0) * 60,
            release_date=self._normalize_string(book.get("releaseDate")),
            cover_url=self._normalize_optional(book.get("image")),
            asin=self._normalize_optional(book.get("asin")),
            isbn=self._normalize_optional(book.get("isbn")),
            publishers=self._normalize_list(book.get("publishers")),
            audible_locale=self._normalize_string(region),
        )
