"""Tests for the Audible stub provider."""
from __future__ import annotations

import pytest

from app.providers.audible import AudibleProvider, marketplace_for


def test_marketplace_mapping_covers_supported_locales() -> None:
    assert marketplace_for("us") == "audible.com"
    assert marketplace_for("de") == "audible.de"
    assert marketplace_for("uk") == "audible.co.uk"
    assert marketplace_for("fr") == "audible.fr"


def test_marketplace_mapping_falls_back_to_us() -> None:
    assert marketplace_for("zz") == "audible.com"
    assert marketplace_for("") == "audible.com"


@pytest.mark.asyncio
async def test_search_returns_stub_result() -> None:
    provider = AudibleProvider(region="de")
    response = await provider.search("Der Vorleser")
    assert response.results
    first = response.results[0]
    assert first.provider_name == "audible"
    assert first.title == "Der Vorleser"
    assert first.locale == "de"
    assert first.asin and first.asin.startswith("STUB")


@pytest.mark.asyncio
async def test_search_is_deterministic_per_query() -> None:
    provider = AudibleProvider(region="us")
    first = await provider.search("Dune")
    second = await provider.search("Dune")
    assert first.results[0].asin == second.results[0].asin


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty() -> None:
    provider = AudibleProvider(region="us")
    response = await provider.search("   ")
    assert not response.results


@pytest.mark.asyncio
async def test_browse_returns_empty_by_design() -> None:
    provider = AudibleProvider(region="us")
    response = await provider.browse()
    assert response.results == []
