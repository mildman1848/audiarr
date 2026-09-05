"""Audible provider — real catalog API client, no login required.

Audible's public catalog endpoints work without authentication (the same
approach used by community tools and Kodi addons):

- ``GET /1.0/catalog/products?keywords=<q>&num_results=N&response_groups=...``
- ``GET /1.0/catalog/products/<asin>?response_groups=...``

Region/marketplace is selected via the API host (api.audible.com,
api.audible.de, ...). The default region is ``us``; ``de`` is fully
supported.

The HTTP client is injectable so tests run against ``httpx.MockTransport``
instead of the live API.
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

log = logging.getLogger("audiarr.providers.audible")

# Catalog hosts per locale. Keys mirror AudibleLocale in app.models.settings.
AUDIBLE_API_HOSTS: dict[str, str] = {
    "us": "https://api.audible.com",
    "uk": "https://api.audible.co.uk",
    "de": "https://api.audible.de",
    "fr": "https://api.audible.fr",
    "ca": "https://api.audible.ca",
    "au": "https://api.audible.com.au",
    "it": "https://api.audible.it",
    "es": "https://api.audible.es",
    "jp": "https://api.audible.co.jp",
    "in": "https://api.audible.in",
}

# Storefront domains (for building web links), kept for UI use.
AUDIBLE_MARKETPLACES: dict[str, str] = {
    "us": "audible.com",
    "uk": "audible.co.uk",
    "de": "audible.de",
    "fr": "audible.fr",
    "ca": "audible.ca",
    "au": "audible.com.au",
    "it": "audible.it",
    "es": "audible.es",
    "jp": "audible.co.jp",
    "in": "audible.in",
}

DEFAULT_PAGE_SIZE = 20

# response_groups that map to our BookQuickInfo/BookDetailInfo fields.
CATALOG_RESPONSE_GROUPS = (
    "contributors,product_attrs,product_extended_attrs,media,series"
)

USER_AGENT = "Audiarr/0.1.0 (https://github.com/mildman1848/audiarr)"


def api_host_for(locale: str) -> str:
    """Map a locale code to the Audible catalog API host."""
    return AUDIBLE_API_HOSTS.get(locale.lower(), AUDIBLE_API_HOSTS["us"])


def marketplace_for(locale: str) -> str:
    """Map a locale code to the Audible storefront domain."""
    return AUDIBLE_MARKETPLACES.get(locale.lower(), AUDIBLE_MARKETPLACES["us"])


class AudibleProvider(BaseMetadataProvider):
    """Audible catalog provider using the public, login-free API."""

    provider_name: str = "audible"
    provider_url: str = "https://api.audible.com"
    provider_description: str = (
        "Audible catalog metadata via the public catalog API (no login)."
    )
    provider_version: str = "0.2.0"
    requires_api_key: bool = False

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        region: str = "us",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(api_key=api_key, region=region)
        self.region = region
        self.base_url = (base_url or api_host_for(region)).rstrip("/")
        self._client = client

    # -- internals ---------------------------------------------------------

    def _make_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

    def _owns_client(self) -> bool:
        return self._client is None

    def _cover_from(self, product: dict[str, Any]) -> str | None:
        images = product.get("product_images") or {}
        # Prefer larger variants; Audible keys are like "500", "1024".
        for key in sorted(images, key=lambda k: int(k) if str(k).isdigit() else 0, reverse=True):
            return self._normalize_optional(images[key])
        return None

    def _series_from(self, product: dict[str, Any]) -> tuple[str, int]:
        series_list = product.get("series") or []
        if not series_list:
            return "", 0
        first = series_list[0] or {}
        title = self._normalize_string(first.get("title"))
        sequence_raw = first.get("sequence")
        try:
            sequence = int(float(str(sequence_raw))) if sequence_raw else 0
        except (TypeError, ValueError):
            sequence = 0
        return title, sequence

    def _map_product(self, product: dict[str, Any]) -> BookQuickInfo:
        asin = self._normalize_optional(product.get("asin"))
        series, sequence = self._series_from(product)
        return BookQuickInfo(
            provider_uid=asin or "",
            provider_name=self.provider_name,
            title=self._normalize_string(product.get("title")),
            subtitle=self._normalize_string(product.get("subtitle")),
            authors=self._normalize_named_entries(product.get("authors")),
            narrators=self._normalize_named_entries(product.get("narrators")),
            series=series,
            series_position=sequence,
            cover_url=self._cover_from(product),
            asin=asin,
            locale=self.region,
        )

    def _map_product_detail(self, product: dict[str, Any]) -> BookDetailInfo:
        asin = self._normalize_optional(product.get("asin")) or ""
        series, sequence = self._series_from(product)
        runtime_min = product.get("runtime_length_min") or 0
        try:
            duration_seconds = int(runtime_min) * 60
        except (TypeError, ValueError):
            duration_seconds = 0
        return BookDetailInfo(
            provider_uid=asin,
            provider_name=self.provider_name,
            provider_external_id=asin,
            title=self._normalize_string(product.get("title")),
            subtitle=self._normalize_string(product.get("subtitle")),
            description=self._normalize_string(
                product.get("publisher_summary") or product.get("merchandising_summary")
            ),
            authors=self._normalize_named_entries(product.get("authors")),
            narrators=self._normalize_named_entries(product.get("narrators")),
            series=series,
            series_position=sequence,
            language=self._normalize_string(product.get("language")),
            duration_seconds=duration_seconds,
            release_date=self._normalize_string(product.get("release_date")),
            cover_url=self._cover_from(product),
            asin=asin or None,
            publishers=(
                [self._normalize_string(product.get("publisher_name"))]
                if product.get("publisher_name")
                else []
            ),
            content_type=self._normalize_string(product.get("format_type")),
            audible_locale=self.region,
        )

    # -- contract ----------------------------------------------------------

    async def healthcheck(self) -> bool:
        client = self._make_client()
        try:
            response = await client.get(
                "/1.0/catalog/products",
                params={"num_results": 1, "response_groups": "contributors"},
            )
            response.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug("Audible healthcheck failed: %s", exc)
            return False
        finally:
            if self._owns_client():
                await client.aclose()

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        """Search by free-text keywords and/or author name.

        ``author`` (kwarg) uses the catalog API's ``author=`` parameter;
        when both are given, Audible ANDs them (title keywords + author).
        """
        if not query.strip() and not (kwargs.get("author") or "").strip():
            return SearchResponse(results=[], query_used=query)

        page_size = int(kwargs.get("page_size", DEFAULT_PAGE_SIZE))
        params: dict[str, Any] = {
            "num_results": page_size,
            "response_groups": CATALOG_RESPONSE_GROUPS,
        }
        if query.strip():
            params["keywords"] = query.strip()
        author = (kwargs.get("author") or "").strip()
        if author:
            params["author"] = author
        log.info(
            "Audible search for %r author=%r region=%s", query, author, self.region
        )

        client = self._make_client()
        try:
            response = await client.get("/1.0/catalog/products", params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            log.warning("Audible search failed: %s", exc)
            return SearchResponse(results=[], query_used=query)
        finally:
            if self._owns_client():
                await client.aclose()

        products = payload.get("products", []) if isinstance(payload, dict) else []
        results = [self._map_product(p) for p in products if isinstance(p, dict)]
        total = payload.get("total_results", len(results)) if isinstance(payload, dict) else 0
        log.debug("Audible search for %r returned %d hits", query, len(results))
        return SearchResponse(
            results=results,
            query_used=query,
            provider_metadata={"total_results": total},
        )

    async def browse(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        sort: str = "newest",
        filter: str = "",
    ) -> BrowseResponse:
        # Browse is not part of the MVP; search covers interactive lookup.
        return BrowseResponse(results=[], page=page, page_size=page_size)

    async def get_detail(self, external_id: str, **kwargs: Any) -> BookDetailInfo | None:
        client = self._make_client()
        try:
            response = await client.get(
                f"/1.0/catalog/products/{external_id}",
                params={"response_groups": CATALOG_RESPONSE_GROUPS + ",product_desc"},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            log.warning("Audible detail fetch for %s failed: %s", external_id, exc)
            return None
        finally:
            if self._owns_client():
                await client.aclose()

        product = payload.get("product") if isinstance(payload, dict) else None
        if not isinstance(product, dict):
            return None
        return self._map_product_detail(product)
