from __future__ import annotations

import httpx
import pytest

from app.connections.prowlarr import ProwlarrClient


@pytest.mark.asyncio
async def test_status_ok_on_200_with_api_key_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/system/status"
        assert request.headers.get("x-api-key") == "prowlarr-key"
        return httpx.Response(200, json={"version": "1.21.0.4649", "appName": "Prowlarr"})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://prowlarr.local"
    )
    client = ProwlarrClient(
        base_url="http://prowlarr.local", api_key="prowlarr-key", client=mock_client
    )

    status = await client.status()
    assert status is not None
    assert status["version"] == "1.21.0.4649"
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_status_omits_header_when_no_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "x-api-key" not in request.headers
        return httpx.Response(200, json={"version": "1.0.0"})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://prowlarr.local"
    )
    client = ProwlarrClient(base_url="http://prowlarr.local", client=mock_client)

    assert await client.status() == {"version": "1.0.0"}
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_status_none_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://prowlarr.local"
    )
    client = ProwlarrClient(base_url="http://prowlarr.local", api_key="bad", client=mock_client)

    assert await client.status() is None
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_status_none_on_non_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://prowlarr.local"
    )
    client = ProwlarrClient(base_url="http://prowlarr.local", client=mock_client)

    assert await client.status() is None
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_status_none_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://prowlarr.local"
    )
    client = ProwlarrClient(base_url="http://prowlarr.local", client=mock_client)

    assert await client.status() is None
    await mock_client.aclose()


def test_api_key_via_file_env(tmp_path, monkeypatch):
    secret_file = tmp_path / "prowlarr_key"
    secret_file.write_text("from-file-secret\n", encoding="utf-8")
    monkeypatch.setenv("FILE__PROWLARR_API_KEY", str(secret_file))

    client = ProwlarrClient(base_url="http://prowlarr.local")
    assert client.api_key == "from-file-secret"
