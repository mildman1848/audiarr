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


@pytest.mark.asyncio
async def test_queue_remove_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "queue"
        assert request.url.params["name"] == "delete"
        assert request.url.params["value"] == "nzo_1"
        assert request.url.params["del_files"] == "0"
        assert request.url.params["apikey"] == "sab-key"
        return httpx.Response(200, json={"status": True})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", api_key="sab-key", client=mock_client)

    assert await client.queue_remove("nzo_1") is True
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_queue_remove_false_when_status_false():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": False})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.queue_remove("nzo_1") is False
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_queue_remove_false_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "API Key Incorrect"})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.queue_remove("nzo_1") is False
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_queue_remove_false_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.queue_remove("nzo_1") is False
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_history_remove_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "history"
        assert request.url.params["name"] == "delete"
        assert request.url.params["value"] == "nzo_9"
        assert request.url.params["del_files"] == "0"
        return httpx.Response(200, json={"status": True})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.history_remove("nzo_9") is True
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_history_retry_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "retry"
        assert request.url.params["value"] == "nzo_9"
        return httpx.Response(200, json={"status": True})

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.history_retry("nzo_9") is True
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_history_retry_false_on_non_json_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>login</html>")

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url="http://sab.local", client=mock_client)

    assert await client.history_retry("nzo_9") is False
    await mock_client.aclose()


_TRUSTED = "http://sab.local:8080"


@pytest.fixture(params=["env", "file"])
def deployment_key(request, tmp_path, monkeypatch):
    """Provide a dummy deployment key via the plain or ``FILE__`` variable."""
    monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
    monkeypatch.delenv("FILE__SABNZBD_API_KEY", raising=False)
    if request.param == "env":
        monkeypatch.setenv("SABNZBD_API_KEY", "dummy-deploy-key")
    else:
        secret_file = tmp_path / "sab_key"
        secret_file.write_text("dummy-deploy-key\n", encoding="utf-8")
        monkeypatch.setenv("FILE__SABNZBD_API_KEY", str(secret_file))
    return "dummy-deploy-key"


async def _sent_key(base_url: str, api_key: str | None = None) -> str:
    """Run a version probe through MockTransport; return the apikey sent ('' if none)."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("apikey", ""))
        return httpx.Response(200, json={"version": "4.0.0"})

    # The transport always targets a valid host; ``base_url`` only drives the
    # key-binding decision (it may be deliberately malformed).
    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://sab.local"
    )
    client = SABnzbdClient(base_url=base_url, api_key=api_key, client=mock_client)
    assert await client.version() == "4.0.0"
    await mock_client.aclose()
    assert len(seen) == 1
    return seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [_TRUSTED, _TRUSTED + "/", "HTTP://SAB.LOCAL:8080"],
    ids=["exact", "trailing-slash", "case-folded"],
)
async def test_deployment_key_used_for_trusted_url(deployment_key, monkeypatch, url):
    monkeypatch.setenv("SABNZBD_URL", _TRUSTED)
    assert await _sent_key(url) == deployment_key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://attacker.example",
        "http://sab.local",  # different effective port
        "https://sab.local:8080",  # different scheme
        "http://sab.local:8080/other",  # different path
        "http://user:pw@sab.local:8080",
        "http://sab.local:8080?x=1",
        "http://sab.local:8080#frag",
        "http://sab.local:8080\\evil",
        "http://sab.local:8080/a b",
        "",
    ],
)
async def test_deployment_key_not_sent_to_mismatched_url(deployment_key, monkeypatch, url):
    monkeypatch.setenv("SABNZBD_URL", _TRUSTED)
    assert await _sent_key(url) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trusted",
    [None, "", "not a url", "ftp://sab.local:8080", "http://u:p@sab.local:8080", "http://sab.local:0"],
)
async def test_deployment_key_not_sent_when_trusted_url_missing_or_malformed(
    deployment_key, monkeypatch, trusted
):
    if trusted is None:
        monkeypatch.delenv("SABNZBD_URL", raising=False)
    else:
        monkeypatch.setenv("SABNZBD_URL", trusted)
    assert await _sent_key("http://sab.local:8080") == ""
    assert await _sent_key(trusted or "") == ""


@pytest.mark.asyncio
async def test_explicit_key_is_honoured_and_empty_means_none(deployment_key, monkeypatch):
    monkeypatch.setenv("SABNZBD_URL", _TRUSTED)
    # Explicit nonempty key wins everywhere, even for an untrusted URL.
    assert await _sent_key("http://attacker.example", "typed-key") == "typed-key"
    assert await _sent_key(_TRUSTED, "typed-key") == "typed-key"
    # Explicit empty string suppresses the deployment key even at the trusted URL.
    assert await _sent_key(_TRUSTED, "") == ""


def test_api_key_via_file_env(tmp_path, monkeypatch):
    secret_file = tmp_path / "sab_key"
    secret_file.write_text("from-file-secret\n", encoding="utf-8")
    monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
    monkeypatch.setenv("FILE__SABNZBD_API_KEY", str(secret_file))

    # Unbound: no trusted SABNZBD_URL, so the secret must not be used.
    monkeypatch.delenv("SABNZBD_URL", raising=False)
    assert SABnzbdClient(base_url="http://sab.local").api_key == ""

    monkeypatch.setenv("SABNZBD_URL", "http://sab.local")
    assert SABnzbdClient(base_url="http://sab.local").api_key == "from-file-secret"
