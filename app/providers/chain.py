"""Ordered provider chain with fallback semantics."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.providers.base import (
    BrowseResponse,
    SearchResponse,
)
from app.providers.registry import ProviderRegistry

log = logging.getLogger("audiarr.providers.chain")


class ProviderChainConfig(BaseModel):
    provider_order: list[str] = Field(default_factory=lambda: ["audible", "audnexus"])
    audible_locale: str = "us"
    audnexus_base_url: str = "https://api.audnex.us"


class ProviderChain:
    """
    Ordered search/sync layer over registered providers.

    Semantics:
    - Providers are tried in ``provider_order``.
    - The first provider that returns any results wins.
    - If no provider returns results, the chain returns an empty result set.
    - Providers that raise on real failures are logged and skipped for this
      request; empty results are treated as "no match", not as an error.
    """

    def __init__(self, *, config: ProviderChainConfig | None = None) -> None:
        self.config = config or ProviderChainConfig()
        self._registry = ProviderRegistry

    def _provider_ctor_kwargs(self, provider_name: str) -> dict[str, Any]:
        """Map chain config to provider constructor kwargs."""
        kwargs: dict[str, Any] = {"region": self.config.audible_locale}
        if provider_name == "audnexus" and self.config.audnexus_base_url:
            kwargs["base_url"] = self.config.audnexus_base_url
        return kwargs

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        if not query.strip():
            return SearchResponse(results=[], query_used=query)

        for provider_name in self.config.provider_order:
            if not self._registry.has_provider(provider_name):
                log.debug("provider %s not registered, skipping", provider_name)
                continue

            try:
                provider = self._registry.by_name(
                    provider_name, **self._provider_ctor_kwargs(provider_name)
                )
                response = await provider.search(query, **kwargs)
                if response.results:
                    log.debug(
                        "provider %s returned %d hits for %r",
                        provider_name,
                        len(response.results),
                        query,
                    )
                    return response
                log.debug(
                    "provider %s returned no results for %r", provider_name, query
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "provider %s search failed for %r: %s",
                    provider_name,
                    query,
                    exc,
                )

        return SearchResponse(results=[], query_used=query)

    async def browse(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        sort: str = "newest",
        filter: str = "",
    ) -> BrowseResponse:
        for provider_name in self.config.provider_order:
            if not self._registry.has_provider(provider_name):
                continue
            try:
                provider = self._registry.by_name(
                    provider_name, **self._provider_ctor_kwargs(provider_name)
                )
                response = await provider.browse(
                    page=page, page_size=page_size, sort=sort, filter=filter
                )
                if response.results:
                    return response
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "provider %s browse failed (page=%s, filter=%r): %s",
                    provider_name,
                    page,
                    filter,
                    exc,
                )

        return BrowseResponse(results=[], page=page, page_size=page_size)

    async def healthcheck(self) -> dict[str, bool]:
        """Run healthchecks of all registered providers concurrently."""
        names = [
            name
            for name in self.config.provider_order
            if self._registry.has_provider(name)
        ]
        if not names:
            return {}

        async def _check(name: str) -> tuple[str, bool]:
            try:
                provider = self._registry.by_name(
                    name, **self._provider_ctor_kwargs(name)
                )
                return name, await provider.healthcheck()
            except Exception:  # noqa: BLE001
                return name, False

        results = await asyncio.gather(*(_check(n) for n in names))
        return dict(results)
