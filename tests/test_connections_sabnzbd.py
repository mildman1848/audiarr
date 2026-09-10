from __future__ import annotations

import httpx
import pytest

from app.connections.sabnzbd import SABnzbdClient


@pytest.mark.asyncio
async def test_version_ok_on_200_with_version_field():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api"
        assert request.url.params["mode"] == "version"
        assert request.url.params["apikey"] == "sab-key"
        return httpx.Response(200, json={"version": "4.3.2"})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", api_key="sab-key", client=mock_client)

    assert await client.version() == "4.3.2"
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_version_none_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "API Key Incorrect"})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", api_key="bad", client=mock_client)

    assert await client.version() is None
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_version_none_on_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>login</html>")

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.version() is None
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_version_none_on_missing_version_field():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": True})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.version() is None
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_version_none_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.version() is None
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_version_omits_apikey_when_not_set():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "apikey" not in request.url.params
        return httpx.Response(200, json={"version": "4.0.0"})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.version() == "4.0.0"
    await mock_client.aclose()


def test_api_key_via_file_env(tmp_path, monkeypatch):
    secret_file = tmp_path / "sab_key"
    secret_file.write_text("from-file-secret\n", encoding="utf-8")
    monkeypatch.setenv("FILE__SABNZBD_API_KEY", str(secret_file))

    client = SABnzbdClient(base_url="http://sab.local")
    assert client.api_key == "from-file-secret"
