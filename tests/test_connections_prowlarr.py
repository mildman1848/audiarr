from __future__ import annotations

import logging

import httpx
import pytest

from app.connections.prowlarr import (
    ProwlarrClient,
    is_trusted_prowlarr_url,
    normalize_prowlarr_base_url,
    resolve_prowlarr_api_key,
)


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


# ---------------------------------------------------------------------------
# The PROWLARR_API_KEY / FILE__PROWLARR_API_KEY deployment secret is bound to
# the non-secret PROWLARR_URL: it is only used for exactly that destination.
# ---------------------------------------------------------------------------

_ENV_KEY = "env-prowlarr-key"
_TRUSTED = "http://prowlarr.local:9696/prowlarr"


@pytest.fixture(autouse=True)
def _clean_prowlarr_env(monkeypatch):
    for name in ("PROWLARR_URL", "PROWLARR_API_KEY", "FILE__PROWLARR_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(params=["env", "file"])
def env_key(request, monkeypatch, tmp_path):
    """Provide ``_ENV_KEY`` via the plain or the ``FILE__`` variable."""
    if request.param == "env":
        monkeypatch.setenv("PROWLARR_API_KEY", _ENV_KEY)
    else:
        secret_file = tmp_path / "prowlarr_key"
        secret_file.write_text(_ENV_KEY + "\n", encoding="utf-8")
        monkeypatch.setenv("FILE__PROWLARR_API_KEY", str(secret_file))
    return request.param


def test_api_key_via_file_env(tmp_path, monkeypatch):
    secret_file = tmp_path / "prowlarr_key"
    secret_file.write_text("from-file-secret\n", encoding="utf-8")
    monkeypatch.setenv("FILE__PROWLARR_API_KEY", str(secret_file))
    monkeypatch.setenv("PROWLARR_URL", "http://prowlarr.local")

    client = ProwlarrClient(base_url="http://prowlarr.local")
    assert client.api_key == "from-file-secret"


@pytest.mark.parametrize(
    "requested",
    [
        _TRUSTED,
        _TRUSTED + "/",
        "HTTP://PROWLARR.LOCAL:9696/prowlarr",
        "  http://prowlarr.local:9696/prowlarr  ",
    ],
)
def test_env_and_file_key_used_at_trusted_url(env_key, monkeypatch, requested):
    monkeypatch.setenv("PROWLARR_URL", _TRUSTED)

    assert resolve_prowlarr_api_key(None, requested) == _ENV_KEY
    assert resolve_prowlarr_api_key("", requested) == _ENV_KEY
    assert ProwlarrClient(base_url=requested).api_key == _ENV_KEY


@pytest.mark.parametrize(
    ("trusted", "requested"),
    [
        ("http://prowlarr.local", "http://prowlarr.local:80"),
        ("http://prowlarr.local:80/", "http://prowlarr.local"),
        ("https://prowlarr.local", "https://prowlarr.local:443/"),
        ("http://prowlarr.local/", "http://prowlarr.local"),
    ],
)
def test_default_ports_and_trailing_slash_are_equivalent(env_key, monkeypatch, trusted, requested):
    monkeypatch.setenv("PROWLARR_URL", trusted)

    assert resolve_prowlarr_api_key(None, requested) == _ENV_KEY


@pytest.mark.parametrize(
    "requested",
    [
        "http://other.local:9696/prowlarr",  # wrong host
        "http://prowlarr.local:9697/prowlarr",  # wrong port
        "http://prowlarr.local/prowlarr",  # wrong (implicit) port
        "https://prowlarr.local:9696/prowlarr",  # wrong scheme
        "http://prowlarr.local:9696",  # missing base path
        "http://prowlarr.local:9696/other",  # wrong base path
        "http://prowlarr.local:9696/prowlarr/sub",  # deeper base path
        "http://prowlarr.local:9696/Prowlarr",  # path is case-sensitive
        "http://prowlarr.local:9696/prowlarr//",  # more than one trailing slash
        "http://prowlarr.local.evil.example:9696/prowlarr",
        "http://user:pw@prowlarr.local:9696/prowlarr",  # userinfo
        "http://prowlarr.local:9696@evil.example/prowlarr",  # userinfo trick
        "http://prowlarr.local:9696/prowlarr?x=1",  # query
        "http://prowlarr.local:9696/prowlarr#frag",  # fragment
        "http://prowlarr.local:abc/prowlarr",  # malformed port
        "http://prowlarr.local:9696\\@evil.example/prowlarr",  # backslash
        "http://prowlarr.local:9696/pro\nwlarr",  # control character
        "prowlarr.local:9696/prowlarr",  # no scheme
        "ftp://prowlarr.local:9696/prowlarr",
        "",
        None,
    ],
)
def test_env_key_suppressed_for_untrusted_url(env_key, monkeypatch, requested):
    monkeypatch.setenv("PROWLARR_URL", _TRUSTED)

    assert is_trusted_prowlarr_url(requested) is False
    assert resolve_prowlarr_api_key(None, requested) == ""
    if requested:
        assert ProwlarrClient(base_url=requested).api_key == ""


def test_env_key_suppressed_when_prowlarr_url_unset(env_key):
    assert resolve_prowlarr_api_key(None, _TRUSTED) == ""
    assert ProwlarrClient(base_url=_TRUSTED).api_key == ""


@pytest.mark.parametrize(
    "trusted",
    [
        "",
        "   ",
        "prowlarr.local:9696",
        "ftp://prowlarr.local",
        "http://",
        "http://user:pw@prowlarr.local:9696/prowlarr",
        "http://prowlarr.local:9696/prowlarr?apikey=x",
        "http://prowlarr.local:9696/prowlarr#x",
        "http://prowlarr.local:99999/prowlarr",
        "http://prowlarr.local:0/prowlarr",
    ],
)
def test_env_key_suppressed_when_prowlarr_url_malformed(env_key, monkeypatch, trusted):
    monkeypatch.setenv("PROWLARR_URL", trusted)

    assert normalize_prowlarr_base_url(trusted) is None
    # Even a request spelled identically to the malformed setting gets no key.
    assert resolve_prowlarr_api_key(None, trusted) == ""
    assert resolve_prowlarr_api_key(None, _TRUSTED) == ""


def test_persisted_explicit_key_still_works_without_binding(env_key, monkeypatch):
    assert resolve_prowlarr_api_key("stored-key", "http://anywhere.example") == "stored-key"
    assert ProwlarrClient(base_url=_TRUSTED, api_key="stored-key").api_key == "stored-key"

    # ...and wins over the environment key at the trusted URL.
    monkeypatch.setenv("PROWLARR_URL", _TRUSTED)
    assert resolve_prowlarr_api_key("stored-key", _TRUSTED) == "stored-key"
    assert ProwlarrClient(base_url=_TRUSTED, api_key="stored-key").api_key == "stored-key"


def test_explicit_empty_key_never_falls_back_to_env(env_key, monkeypatch):
    monkeypatch.setenv("PROWLARR_URL", _TRUSTED)

    assert ProwlarrClient(base_url=_TRUSTED, api_key="").api_key == ""


@pytest.mark.asyncio
async def test_status_sends_env_key_header_only_at_trusted_url(env_key, monkeypatch):
    monkeypatch.setenv("PROWLARR_URL", _TRUSTED)
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-api-key"))
        return httpx.Response(200, json={"version": "1.0.0"})

    for base_url in (_TRUSTED, "http://prowlarr.local:9696/other"):
        mock_client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url=base_url
        )
        client = ProwlarrClient(base_url=base_url, client=mock_client)
        assert await client.status() == {"version": "1.0.0"}
        await mock_client.aclose()

    assert seen == [_ENV_KEY, None]


def test_key_value_is_never_logged(env_key, monkeypatch, caplog):
    monkeypatch.setenv("PROWLARR_URL", _TRUSTED)

    with caplog.at_level(logging.DEBUG):
        assert resolve_prowlarr_api_key(None, _TRUSTED) == _ENV_KEY
        assert resolve_prowlarr_api_key(None, "http://other.local") == ""
        assert resolve_prowlarr_api_key(None, "http://u:p@other.local/?k=v") == ""

    assert _ENV_KEY not in caplog.text
    assert "u:p@" not in caplog.text
