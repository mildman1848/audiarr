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


# ---------------------------------------------------------------------------
# Deployment secret (M4B_CONVERTARR_API_KEY / FILE__M4B_CONVERTARR_API_KEY) is bound to the
# non-secret M4B_CONVERTARR_URL.
# ---------------------------------------------------------------------------

_TRUSTED = "http://m4b.local:8080"


@pytest.fixture(params=["env", "file"])
def deployment_key(request, monkeypatch, tmp_path):
    monkeypatch.delenv("M4B_CONVERTARR_API_KEY", raising=False)
    monkeypatch.delenv("FILE__M4B_CONVERTARR_API_KEY", raising=False)
    monkeypatch.delenv("M4B_CONVERTARR_URL", raising=False)
    if request.param == "env":
        monkeypatch.setenv("M4B_CONVERTARR_API_KEY", "dummy-env-key")
    else:
        secret_file = tmp_path / "key"
        secret_file.write_text("dummy-env-key\n", encoding="utf-8")
        monkeypatch.setenv("FILE__M4B_CONVERTARR_API_KEY", str(secret_file))
    monkeypatch.setenv("M4B_CONVERTARR_URL", _TRUSTED)
    return request.param


async def _auth_header_sent(client_args: dict) -> str:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200)

    base_url = client_args["base_url"]
    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)
    client = M4BConvertarrClient(client=mock_client, **client_args)
    try:
        assert await client.health() is True
    finally:
        await mock_client.aclose()
    return seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://m4b.local:8080",
        "http://m4b.local:8080/",
        "HTTP://M4B.LOCAL:8080",
    ],
)
async def test_env_and_file_key_sent_to_exact_trusted_destination(deployment_key, base_url):
    assert await _auth_header_sent({"base_url": base_url}) == "Bearer dummy-env-key"


@pytest.mark.asyncio
async def test_default_port_is_normalized(deployment_key, monkeypatch):
    monkeypatch.setenv("M4B_CONVERTARR_URL", "https://m4b.local")
    assert await _auth_header_sent({"base_url": "https://m4b.local:443/"}) == "Bearer dummy-env-key"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "base_url",
    [
        "http://attacker.example:8080",
        "http://m4b.local:9090",
        "https://m4b.local:8080",
        "http://m4b.local:8080/other",
        "http://m4b.local",
        "http://m4b.local:8080?x=1",
        "http://m4b.local:8080#frag",
    ],
)
async def test_untrusted_destination_gets_no_key(deployment_key, base_url):
    assert await _auth_header_sent({"base_url": base_url}) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binding",
    [
        None,
        "",
        "m4b.local:8080",
        "ftp://m4b.local:8080",
        "http://",
        "http://m4b.local:0",
        "http://m4b.local:99999",
        "http://user:pw@m4b.local:8080",
        "http://m4b.local:8080?x=1",
        "http://m4b.local:8080#frag",
        "http://m4b.local:8080\\evil",
        "http://m4b.local:8080/a b",
        "http://m4b.local:8080/\x1f",
        "http://m4b.local:8080/\x85",
    ],
)
async def test_missing_or_malformed_binding_gets_no_key(deployment_key, monkeypatch, binding):
    if binding is None:
        monkeypatch.delenv("M4B_CONVERTARR_URL")
    else:
        monkeypatch.setenv("M4B_CONVERTARR_URL", binding)
    assert await _auth_header_sent({"base_url": _TRUSTED}) == ""


@pytest.mark.asyncio
async def test_explicit_key_is_honoured_for_untrusted_url(deployment_key):
    args = {"base_url": "http://attacker.example", "api_key": "dummy-explicit"}
    assert await _auth_header_sent(args) == "Bearer dummy-explicit"


@pytest.mark.asyncio
async def test_explicit_empty_key_suppresses_env_fallback(deployment_key):
    assert await _auth_header_sent({"base_url": _TRUSTED, "api_key": ""}) == ""


@pytest.mark.asyncio
async def test_userinfo_destination_never_gets_env_key(deployment_key):
    # httpx turns userinfo into Basic auth; the deployment key must not appear.
    header = await _auth_header_sent({"base_url": "http://user:pw@m4b.local:8080"})
    assert "dummy-env-key" not in header
    assert not header.startswith("Bearer")
