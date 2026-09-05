"""Audible provider — deterministic stub with marketplace mapping.

This is deliberately a stub for MVP: it demonstrates the provider
shape, supports Audible marketplace/locale mapping, and returns
deterministic mock results so tests and the UI have something to
render without network access or an Audible account.

A real implementation will wrap Audible's public search pages
(audible.com / audible.de search API) — see ROADMAP.md.
"""
from __future__ import annotations

import logging
from typing import Any

from app.providers.base import (
    BaseMetadataProvider,
    BookQuickInfo,
    BrowseResponse,
    SearchResponse,
)

log = logging.getLogger("audiarr.providers.audible")

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


def marketplace_for(locale: str) -> str:
    """Map a locale code to the Audible marketplace domain."""
    return AUDIBLE_MARKETPLACES.get(locale.lower(), AUDIBLE_MARKETPLACES["us"])


class AudibleProvider(BaseMetadataProvider):
    """Stub Audible provider: no network calls, no credentials required."""

    provider_name: str = "audible"
    provider_url: str = "https://www.audible.com"
    provider_description: str = (
        "Audible catalog metadata (stub for MVP; real client planned)."
    )
    provider_version: str = "0.1.0"
    requires_api_key: bool = False

    def __init__(
        self,
        *,
        base_url: str = "",
        api_key: str = "",
        region: str = "us",
    ) -> None:
        super().__init__(api_key=api_key, region=region)
        self.base_url = base_url or marketplace_for(region)

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        marketplace = marketplace_for(self.region)
        log.info(
            "Audible stub search for %r on marketplace %s (region=%s)",
            query,
            marketplace,
            self.region,
        )
        if not query.strip():
            return SearchResponse(results=[], query_used=query)

        return SearchResponse(
            results=[
                BookQuickInfo(
                    provider_uid=f"stub-asin-{abs(hash(query.lower())) % 100000}",
                    provider_name=self.provider_name,
                    title=query.strip(),
                    authors=["Unknown Author"],
                    narrators=["Unknown Narrator"],
                    asin=f"STUB{abs(hash(query.lower())) % 100000:05d}",
                    locale=self.region,
                )
            ],
            query_used=query,
        )

    async def browse(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        sort: str = "newest",
        filter: str = "",
    ) -> BrowseResponse:
        return BrowseResponse(results=[], page=page, page_size=page_size)
