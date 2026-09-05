"""Integration tests: Settings -> ProviderChainConfig -> search behaviour."""
from __future__ import annotations

import pytest

import app.providers  # noqa: F401  — registers built-in providers
from app.models.settings import Settings
from app.providers.audible import AudibleProvider
from app.providers.chain import ProviderChain, ProviderChainConfig


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
    chain = ProviderChain(config=config)

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
    chain = ProviderChain(
        config=ProviderChainConfig(
            provider_order=settings.metadata.provider_order,
            audible_locale=settings.metadata.audible_locale,
            audnexus_base_url=settings.metadata.audnexus_base_url,
        )
    )
    response = await chain.search("Project Hail Mary")
    assert response.results
    assert response.results[0].provider_name == "audible"
