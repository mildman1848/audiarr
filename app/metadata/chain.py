"""Provider chain: try providers in configured order, first hit wins."""

from __future__ import annotations

import logging

from app.metadata.audible import AudibleProvider
from app.metadata.audnexus import AudnexusProvider
from app.metadata.base import BookMetadata, MetadataProvider

log = logging.getLogger("audiarr.metadata.chain")


def build_default_providers() -> dict[str, MetadataProvider]:
    """Return the known providers keyed by name, for chain construction."""
    return {
        "audible": AudibleProvider(),
        "audnexus": AudnexusProvider(),
    }


class MetadataProviderChain:
    """Runs providers in order until one returns a non-empty result."""

    def __init__(self, providers: dict[str, MetadataProvider], order: list[str]):
        self.providers = providers
        self.order = order

    async def search(self, query: str, locale: str) -> tuple[list[BookMetadata], str | None]:
        """Search using the configured provider order.

        Returns (results, provider_name_used). provider_name_used is None
        if every provider failed or returned nothing.
        """
        for name in self.order:
            provider = self.providers.get(name)
            if provider is None:
                log.warning("Configured provider %r is not registered; skipping", name)
                continue
            try:
                results = await provider.search(query, locale)
            except Exception:  # noqa: BLE001 - a provider failing must not break the chain
                log.exception("Provider %r raised while searching %r", name, query)
                continue
            if results:
                log.info("Provider %r returned %d result(s) for %r", name, len(results), query)
                return results, name
            log.info("Provider %r returned no results for %r; trying next", name, query)
        return [], None
