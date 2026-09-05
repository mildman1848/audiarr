from __future__ import annotations

import httpx
import pytest

from app.connections.m4b_convertarr import M4BConvertarrClient


@pytest.mark.asyncio
async def test_health_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://m4b.local")
    client = M4BConvertarrClient(base_url="http://m4b.local", client=mock_client)

    assert await client.health() is True
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_submit_conversion_posts_webhook_path():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.content))
        return httpx.Response(202)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://m4b.local")
    client = M4BConvertarrClient(base_url="http://m4b.local", client=mock_client)

    ok = await client.submit_conversion(source_path="/data/inbox/book.mp3")
    assert ok is True
    assert calls[0][0] == "/api/v1/convert"
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_health_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://m4b.local")
    client = M4BConvertarrClient(base_url="http://m4b.local", client=mock_client)

    assert await client.health() is False
    await mock_client.aclose()
