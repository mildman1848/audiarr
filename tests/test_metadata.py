"""Metadata search endpoint tests.

Injects a ProviderChain with a mock-transport audible provider via
FastAPI dependency_overrides — no network access, no registry patching.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.api.routes_metadata import build_provider_chain
from app.providers.audible import AudibleProvider, api_host_for
from app.providers.chain import ProviderChain, ProviderChainConfig


def _catalog_envelope(products: list[dict[str, Any]]) -> dict[str, Any]:
    return {"product_filters": [], "products": products, "total_results": len(products)}


def _product(asin: str, title: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "asin": asin,
        "title": title,
        "authors": [{"asin": "B0AUTHOR", "name": "Bernhard Schlink"}],
        "narrators": [{"name": "Hans Korte"}],
        "product_images": {"500": "https://m.media-amazon.com/images/I/test.jpg"},
        "runtime_length_min": 298,
        "release_date": "2010-11-08",
        "language": "de",
    }
    base.update(overrides)
    return base


def _mock_chain(products: list[dict[str, Any]], region: str = "de") -> ProviderChain:
    """Build a chain with a mock-backed audible provider via overrides."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=_catalog_envelope(products))
    )
    client = httpx.AsyncClient(transport=transport, base_url=api_host_for(region))
    audible = AudibleProvider(client=client, region=region)

    return ProviderChain(
        config=ProviderChainConfig(
            provider_order=["audible"],
            audible_locale=region,
        ),
        provider_overrides={"audible": audible},
    )


def test_metadata_search_endpoint_with_mocked_provider(app_client):
    chain = _mock_chain([_product("B004UWRY6M", "Der Vorleser")], region="de")
    app_client.app.dependency_overrides[build_provider_chain] = lambda: chain

    response = app_client.get("/api/v1/metadata/search", params={"query": "Der Vorleser"})
    assert response.status_code == 200
    body = response.json()
    assert body["provider_used"] == "audible"
    assert body["results"][0]["title"] == "Der Vorleser"
    assert body["results"][0]["asin"] == "B004UWRY6M"
    assert body["results"][0]["authors"] == ["Bernhard Schlink"]


def test_metadata_search_endpoint_empty_query(app_client):
    chain = _mock_chain([])
    app_client.app.dependency_overrides[build_provider_chain] = lambda: chain

    response = app_client.get("/api/v1/metadata/search", params={"query": "   "})
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == []
    assert body["provider_used"] is None


def test_metadata_search_endpoint_locale_override(app_client):
    # Chain default is de; the request overrides to us for this call only.
    chain = _mock_chain([_product("B08G9PRS1K", "Project Hail Mary")], region="de")
    app_client.app.dependency_overrides[build_provider_chain] = lambda: chain

    response = app_client.get(
        "/api/v1/metadata/search",
        params={"query": "Project Hail Mary", "locale": "us"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["results"], "expected results with locale override"
    # The override mutates the chain config for this request only.
    assert chain.config.audible_locale == "us"


def test_metadata_search_endpoint_without_override_uses_real_dependency(app_client):
    # No dependency override: endpoint builds the real chain from settings.
    # Use an empty query so no provider is called (would hit the network).
    response = app_client.get("/api/v1/metadata/search", params={"query": ""})
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_metadata_search_endpoint_falls_back_to_audnexus(app_client):
    """audible mock returns nothing -> chain falls through to audnexus,
    which is itself mock-backed via overrides (still no network)."""
    from app.providers.audnexus import AudnexusProvider

    empty_audible_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"product_filters": [], "products": [], "total_results": 0}
        )
    )
    empty_audible = AudibleProvider(
        client=httpx.AsyncClient(
            transport=empty_audible_transport, base_url=api_host_for("de")
        ),
        region="de",
    )

    audnexus_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "asin": "B0FALLBACK",
                        "title": "Fallback Hit",
                        "authors": [{"name": "Jane Doe"}],
                        "narrators": [{"name": "John Roe"}],
                        "image": "https://example.invalid/cover.jpg",
                    }
                ]
            },
        )
    )
    audnexus = AudnexusProvider(
        client=httpx.AsyncClient(
            transport=audnexus_transport, base_url="https://api.audnex.us"
        ),
        region="de",
    )

    chain = ProviderChain(
        config=ProviderChainConfig(
            provider_order=["audible", "audnexus"],
            audible_locale="de",
        ),
        provider_overrides={"audible": empty_audible, "audnexus": audnexus},
    )
    app_client.app.dependency_overrides[build_provider_chain] = lambda: chain

    response = app_client.get("/api/v1/metadata/search", params={"query": "Seltenes Buch"})
    assert response.status_code == 200
    body = response.json()
    assert body["provider_used"] == "audnexus"
    assert body["results"][0]["title"] == "Fallback Hit"
    assert body["results"][0]["provider_name"] == "audnexus"
