"""Tests for the Audible provider against a mock transport.

Fixtures mirror the real catalog envelope shape:
``{"product_filters": [], "products": [...], "total_results": N}``
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.providers.audible import (
    AudibleProvider,
    api_host_for,
    marketplace_for,
)


def _catalog_envelope(products: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "product_filters": [],
        "products": products,
        "total_results": len(products),
    }


def _product(asin: str, title: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "asin": asin,
        "title": title,
        "authors": [{"asin": "B0AUTHOR", "name": "Bernhard Schlink"}],
        "narrators": [{"name": "Hans Korte"}],
        "product_images": {"500": "https://m.media-amazon.com/images/I/41nmHMvz1VL._SL500_.jpg"},
        "runtime_length_min": 298,
        "release_date": "2010-11-08",
        "language": "de",
        "publisher_name": "Audible Studios",
        "format_type": "unabridged",
    }
    base.update(overrides)
    return base


def _mock_transport(
    *, products: list[dict[str, Any]] | None = None
) -> httpx.MockTransport:
    products = products if products is not None else [
        _product("B004UWRY6M", "Der Vorleser")
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if path == "/1.0/catalog/products":
            return httpx.Response(200, json=_catalog_envelope(products))
        if path.startswith("/1.0/catalog/products/"):
            asin = path.rsplit("/", 1)[-1]
            match = next((p for p in products if p["asin"] == asin), None)
            if match is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"product": match})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def _provider(
    products: list[dict[str, Any]] | None = None, region: str = "de"
) -> AudibleProvider:
    client = httpx.AsyncClient(
        transport=_mock_transport(products=products),
        base_url=api_host_for(region),
    )
    return AudibleProvider(client=client, region=region)


# -- host mapping -----------------------------------------------------------


def test_api_host_mapping_covers_supported_locales() -> None:
    assert api_host_for("us") == "https://api.audible.com"
    assert api_host_for("de") == "https://api.audible.de"
    assert api_host_for("uk") == "https://api.audible.co.uk"


def test_api_host_falls_back_to_us() -> None:
    assert api_host_for("zz") == "https://api.audible.com"


def test_marketplace_mapping_covers_supported_locales() -> None:
    assert marketplace_for("us") == "audible.com"
    assert marketplace_for("de") == "audible.de"


def test_marketplace_falls_back_to_us() -> None:
    assert marketplace_for("zz") == "audible.com"


# -- search -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_real_catalog_shape() -> None:
    provider = _provider(region="de")
    response = await provider.search("Der Vorleser")
    assert response.results
    book = response.results[0]
    assert book.provider_name == "audible"
    assert book.title == "Der Vorleser"
    assert book.asin == "B004UWRY6M"
    assert book.authors == ["Bernhard Schlink"]
    assert book.narrators == ["Hans Korte"]
    assert book.cover_url is not None
    assert book.locale == "de"


@pytest.mark.asyncio
async def test_search_maps_series_with_position() -> None:
    product = _product(
        "B0SERIES01",
        "Harry Potter und der Stein der Weisen",
        series=[{"asin": "B0SERIES", "title": "Harry Potter", "sequence": "1"}],
    )
    provider = _provider(products=[product], region="de")
    response = await provider.search("Harry Potter")
    book = response.results[0]
    assert book.series == "Harry Potter"
    assert book.series_position == 1


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty() -> None:
    provider = _provider(region="us")
    response = await provider.search("   ")
    assert not response.results


@pytest.mark.asyncio
async def test_search_no_results_returns_empty() -> None:
    provider = _provider(products=[], region="de")
    response = await provider.search("ThisDoesNotExist12345")
    assert not response.results


@pytest.mark.asyncio
async def test_search_carries_total_results_metadata() -> None:
    provider = _provider(region="de")
    response = await provider.search("Der Vorleser")
    assert response.provider_metadata.get("total_results") == 1


# -- detail -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_detail_maps_full_product() -> None:
    provider = _provider(region="de")
    detail = await provider.get_detail("B004UWRY6M")
    assert detail is not None
    assert detail.title == "Der Vorleser"
    assert detail.authors == ["Bernhard Schlink"]
    assert detail.duration_seconds == 298 * 60
    assert detail.release_date == "2010-11-08"
    assert detail.language == "de"
    assert detail.publishers == ["Audible Studios"]
    assert detail.audible_locale == "de"


@pytest.mark.asyncio
async def test_get_detail_returns_none_for_unknown_asin() -> None:
    provider = _provider(products=[], region="de")
    detail = await provider.get_detail("B0DOESNOTEXIST")
    assert detail is None


@pytest.mark.asyncio
async def test_get_detail_returns_none_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.audible.de",
    )
    provider = AudibleProvider(client=client, region="de")
    detail = await provider.get_detail("B004UWRY6M")
    assert detail is None


# -- healthcheck ------------------------------------------------------------


@pytest.mark.asyncio
async def test_healthcheck_passes() -> None:
    provider = _provider(region="de")
    assert await provider.healthcheck() is True


@pytest.mark.asyncio
async def test_healthcheck_fails_gracefully() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.audible.de",
    )
    provider = AudibleProvider(client=client, region="de")
    assert await provider.healthcheck() is False
