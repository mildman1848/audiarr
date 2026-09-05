"""Metadata provider and chain tests. No real network access is used:
Audible is a pure stub, Audnexus is exercised via httpx.MockTransport.
"""

from __future__ import annotations

import httpx
import pytest

from app.metadata.audible import AudibleProvider
from app.metadata.audnexus import AudnexusProvider
from app.metadata.chain import MetadataProviderChain


@pytest.mark.asyncio
async def test_audible_stub_returns_result_for_query():
    provider = AudibleProvider()
    results = await provider.search("Dune", locale="de")
    assert len(results) == 1
    assert results[0].source == "audible"
    assert results[0].title == "Dune"


@pytest.mark.asyncio
async def test_audible_stub_empty_query_returns_nothing():
    provider = AudibleProvider()
    results = await provider.search("   ", locale="us")
    assert results == []


@pytest.mark.asyncio
async def test_audnexus_uses_mocked_http_client():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books"
        return httpx.Response(
            200,
            json=[
                {
                    "asin": "B000TEST",
                    "title": "Mocked Book",
                    "authors": [{"name": "Jane Doe"}],
                    "narrators": [{"name": "John Roe"}],
                }
            ],
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.audnex.us")
    provider = AudnexusProvider(client=mock_client)

    results = await provider.search("Mocked Book", locale="de")
    await mock_client.aclose()

    assert len(results) == 1
    assert results[0].title == "Mocked Book"
    assert results[0].authors == ["Jane Doe"]


@pytest.mark.asyncio
async def test_chain_falls_back_when_first_provider_empty():
    class EmptyProvider:
        name = "empty"

        async def search(self, query, locale):
            return []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = [
            {"asin": "X", "title": "Fallback Hit", "authors": [], "narrators": []}
        ]
        return httpx.Response(200, json=payload)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.audnex.us")

    chain = MetadataProviderChain(
        providers={"empty": EmptyProvider(), "audnexus": AudnexusProvider(client=mock_client)},
        order=["empty", "audnexus"],
    )
    results, provider_used = await chain.search("anything", "de")
    await mock_client.aclose()

    assert provider_used == "audnexus"
    assert results[0].title == "Fallback Hit"


@pytest.mark.asyncio
async def test_metadata_search_endpoint(app_client):
    response = app_client.post("/api/v1/metadata/search", json={"query": "Dune"})
    assert response.status_code == 200
    body = response.json()
    assert body["provider_used"] == "audible"
    assert body["results"][0]["title"] == "Dune"
