"""Integration tests: Settings -> ProviderChainConfig -> search behaviour."""
from __future__ import annotations

import pytest

import app.providers  # noqa: F401  — registers built-in providers
from app.models.settings import Settings
from app.providers.audible import AudibleProvider
from app.providers.base import BaseMetadataProvider, BookQuickInfo, SearchResponse
from app.providers.chain import ProviderChain, ProviderChainConfig


class _StubProvider(BaseMetadataProvider):
    """Offline provider used to test chain ordering without live API calls."""

    def __init__(self, provider_name: str, *, results: list[BookQuickInfo] | None = None):
        super().__init__(region="de")
        self.provider_name = provider_name
        self._results = results or []

    async def search(self, query: str, **kwargs) -> SearchResponse:
        return SearchResponse(results=self._results, query_used=query)


def test_settings_defaults_map_to_chain_defaults() -> None:
    settings = Settings()
    config = ProviderChainConfig(
        provider_order=settings.metadata.provider_order,
        audible_locale=settings.metadata.audible_locale,
        audnexus_base_url=settings.metadata.audnexus_base_url,
    )
    chain = ProviderChain(config=config)
    assert chain.config.provider_order == ["audible", "audnexus"]
    assert chain.config.audible_locale == "us"
    assert chain.config.audnexus_base_url == "https://api.audnex.us"


@pytest.mark.asyncio
async def test_chain_search_uses_settings_order() -> None:
    settings = Settings()
    settings.metadata.provider_order = ["audible", "audnexus"]
    settings.metadata.audible_locale = "de"

    config = ProviderChainConfig(
        provider_order=settings.metadata.provider_order,
        audible_locale=settings.metadata.audible_locale,
        audnexus_base_url=settings.metadata.audnexus_base_url,
    )
    audible_hit = BookQuickInfo(
        provider_uid="audible:der-vorleser",
        provider_name="audible",
        title="Der Vorleser",
        locale="de",
    )
    chain = ProviderChain(
        config=config,
        provider_overrides={
            "audible": _StubProvider("audible", results=[audible_hit]),
            "audnexus": _StubProvider("audnexus"),
        },
    )

    response = await chain.search("Der Vorleser")
    assert response.results
    assert response.results[0].provider_name == "audible"
    assert response.results[0].locale == "de"


@pytest.mark.asyncio
async def test_chain_passes_locale_to_provider_instances() -> None:
    config = ProviderChainConfig(provider_order=["audible"], audible_locale="de")
    chain = ProviderChain(config=config)

    provider = chain._registry.by_name("audible", region=config.audible_locale)
    assert isinstance(provider, AudibleProvider)
    assert provider.region == "de"


@pytest.mark.asyncio
async def test_chain_maps_audnexus_base_url_from_settings() -> None:
    settings = Settings()
    settings.metadata.audnexus_base_url = "http://audnexus.local:8080"

    config = ProviderChainConfig(
        provider_order=["audnexus"],
        audible_locale=settings.metadata.audible_locale,
        audnexus_base_url=settings.metadata.audnexus_base_url,
    )
    chain = ProviderChain(config=config)

    kwargs = chain._provider_ctor_kwargs("audnexus")
    assert kwargs["base_url"] == "http://audnexus.local:8080"


@pytest.mark.asyncio
async def test_full_fallback_settings_order_with_mocked_audnexus() -> None:
    """Settings order [audible, audnexus] with audible first must never
    reach the network: the audible stub answers before audnexus runs."""
    settings = Settings()
    audible_hit = BookQuickInfo(
        provider_uid="audible:project-hail-mary",
        provider_name="audible",
        title="Project Hail Mary",
        locale=settings.metadata.audible_locale,
    )
    chain = ProviderChain(
        config=ProviderChainConfig(
            provider_order=settings.metadata.provider_order,
            audible_locale=settings.metadata.audible_locale,
            audnexus_base_url=settings.metadata.audnexus_base_url,
        ),
        provider_overrides={
            "audible": _StubProvider("audible", results=[audible_hit]),
            "audnexus": _StubProvider("audnexus"),
        },
    )
    response = await chain.search("Project Hail Mary")
    assert response.results
    assert response.results[0].provider_name == "audible"
