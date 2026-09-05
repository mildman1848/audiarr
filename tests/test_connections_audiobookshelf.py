from __future__ import annotations

import httpx
import pytest

from app.connections.audiobookshelf import AudiobookshelfClient


@pytest.mark.asyncio
async def test_health_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/healthcheck"
        return httpx.Response(200)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://abs.local")
    client = AudiobookshelfClient(base_url="http://abs.local", api_key="test-key", client=mock_client)

    assert await client.health() is True
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_health_false_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://abs.local")
    client = AudiobookshelfClient(base_url="http://abs.local", client=mock_client)

    assert await client.health() is False
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_scan_library_posts_expected_path():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(202)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://abs.local")
    client = AudiobookshelfClient(base_url="http://abs.local", client=mock_client)

    assert await client.scan_library("lib-1") is True
    assert calls == ["/api/libraries/lib-1/scan"]
    await mock_client.aclose()


def test_api_key_via_file_env(tmp_path, monkeypatch):
    secret_file = tmp_path / "abs_key"
    secret_file.write_text("from-file-secret\n", encoding="utf-8")
    monkeypatch.setenv("FILE__AUDIOBOOKSHELF_API_KEY", str(secret_file))

    client = AudiobookshelfClient(base_url="http://abs.local")
    assert client.api_key == "from-file-secret"
