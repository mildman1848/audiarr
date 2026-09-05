"""Tests for the Audnexus provider against a mock transport."""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.providers.audnexus import AudnexusProvider


def _mock_transport(
    *, books_results: list[dict[str, Any]] | None = None
) -> httpx.MockTransport:
    books_results = (
        books_results
        if books_results is not None
        else [
            {
                "asin": "B08XYZ12345",
                "title": "Das Parfum",
                "subtitle": "",
                "authors": [{"name": "Patrick Süskind"}],
                "narrators": [{"name": "Rolf Boysen"}],
                "isbn": "9783442211012",
                "image": "https://example.invalid/cover.jpg",
            }
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rstrip("/")
        if path == "/health":
            return httpx.Response(200, json={"ok": True})
        if path == "/search/books":
            return httpx.Response(200, json={"results": books_results})
        if path.startswith("/books/"):
            # Detail endpoint returns the first item if it matches.
            if books_results:
                return httpx.Response(200, json=books_results[0])
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


@pytest.fixture
def provider() -> AudnexusProvider:
    # Injected client => provider never touches the network in tests.
    # base_url is required so relative provider URLs resolve for httpx.
    client = httpx.AsyncClient(
        transport=_mock_transport(), base_url="https://api.audnex.us"
    )
    return AudnexusProvider(client=client, region="de")


@pytest.mark.asyncio
async def test_healthcheck_passes(provider: AudnexusProvider) -> None:
    assert await provider.healthcheck() is True


@pytest.mark.asyncio
async def test_search_returns_results(provider: AudnexusProvider) -> None:
    response = await provider.search("Das Parfum")
    assert response.results
    book = response.results[0]
    assert book.provider_name == "audnexus"
    assert book.title == "Das Parfum"
    assert book.asin == "B08XYZ12345"
    assert book.isbn == "9783442211012"
    assert book.locale == "de"
    assert book.cover_url == "https://example.invalid/cover.jpg"


@pytest.mark.asyncio
async def test_search_uses_authors_and_narrators_as_lists(
    provider: AudnexusProvider,
) -> None:
    response = await provider.search("Das Parfum")
    assert response.results
    book = response.results[0]
    assert isinstance(book.authors, list)
    assert book.authors == ["Patrick Süskind"]
    assert isinstance(book.narrators, list)
    assert book.narrators == ["Rolf Boysen"]


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(provider: AudnexusProvider) -> None:
    response = await provider.search("")
    assert not response.results


@pytest.mark.asyncio
async def test_search_no_results_returns_empty() -> None:
    transport = _mock_transport(books_results=[])
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.audnex.us"
    ) as client:
        provider = AudnexusProvider(client=client, region="de")
        response = await provider.search("ThisDoesNotExist12345")
        assert not response.results


@pytest.mark.asyncio
async def test_get_detail_maps_runtime_minutes_to_seconds(
    provider: AudnexusProvider,
) -> None:
    detail = await provider.get_detail("B08XYZ12345")
    assert detail is not None
    assert detail.provider_name == "audnexus"
    assert detail.title == "Das Parfum"
