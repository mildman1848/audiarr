"""API-level tests for the connection test endpoints.

The unreachable-address tests exercise the failure path without mocks or
network access. The SABnzbd/Prowlarr success paths monkeypatch the client
class with an httpx.MockTransport-backed factory (same pattern as
tests/test_audiobookshelf.py).
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.connections.audiobookshelf import AudiobookshelfClient
from app.connections.m4b_convertarr import M4BConvertarrClient
from app.connections.prowlarr import ProwlarrClient
from app.connections.sabnzbd import SABnzbdClient


def _factory(real_cls, handler):
    """Return a drop-in for a connection client wired to a MockTransport."""

    def make(base_url: str = "", api_key: str | None = None, client=None):
        url = base_url or "http://mock.local"
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)
        return real_cls(base_url=url, api_key=api_key, client=mock)

    return make


def test_audiobookshelf_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/audiobookshelf/test",
        json={"url": "http://127.0.0.1:1", "api_key": "x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_m4b_convertarr_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/m4b-convertarr/test",
        json={"url": "http://127.0.0.1:1"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_sabnzbd_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://127.0.0.1:1", "api_key": "x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_prowlarr_test_endpoint_unreachable(app_client):
    response = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://127.0.0.1:1", "api_key": "x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_prowlarr_test_endpoint_malformed_url_is_not_a_500(app_client):
    response = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://prowlarr.local:abc", "api_key": "x"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_sabnzbd_test_endpoint_ok(app_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.3.2"})

    monkeypatch.setattr(
        "app.api.routes_connections.SABnzbdClient", _factory(SABnzbdClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://sab.local", "api_key": "x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "4.3.2" in body["message"]


def test_prowlarr_test_endpoint_ok(app_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "1.21.0", "appName": "Prowlarr"})

    monkeypatch.setattr(
        "app.api.routes_connections.ProwlarrClient", _factory(ProwlarrClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://prowlarr.local", "api_key": "x"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "1.21.0" in body["message"]


def test_sabnzbd_test_endpoint_uses_stored_api_key_when_blank(app_client, monkeypatch):
    settings = app_client.get("/api/v1/settings").json()
    settings["download_clients"] = [
        {
            "name": "SABnzbd",
            "type": "sabnzbd",
            "url": "http://sab.local",
            "api_key": "stored-sab-key",
            "category": "audiobooks",
            "enabled": True,
        }
    ]
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["apikey"] = request.url.params.get("apikey", "")
        return httpx.Response(200, json={"version": "4.3.2"})

    monkeypatch.setattr(
        "app.api.routes_connections.SABnzbdClient", _factory(SABnzbdClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://sab.local/"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["apikey"] == "stored-sab-key"


def test_sabnzbd_test_failure_dispatches_health_issue_without_api_key(app_client, monkeypatch):
    """Issue #28: a failed connection test fires a best-effort
    `health_issue` Connect event that names the integration/url/message but
    never includes the api_key used to probe it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    monkeypatch.setattr(
        "app.api.routes_connections.SABnzbdClient", _factory(SABnzbdClient, handler)
    )

    calls: list[tuple[str, dict]] = []

    async def fake_dispatch(event, payload):
        calls.append((event, payload))

    monkeypatch.setattr("app.api.routes_connections.dispatch_event", fake_dispatch)

    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://sab.local", "api_key": "top-secret-key"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False

    assert len(calls) == 1
    event, payload = calls[0]
    assert event == "health_issue"
    assert payload["integration"] == "SABnzbd"
    assert payload["url"] == "http://sab.local"
    assert "top-secret-key" not in str(payload)


def test_sabnzbd_test_success_does_not_dispatch_health_issue(app_client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.3.2"})

    monkeypatch.setattr(
        "app.api.routes_connections.SABnzbdClient", _factory(SABnzbdClient, handler)
    )

    calls: list[tuple[str, dict]] = []

    async def fake_dispatch(event, payload):
        calls.append((event, payload))

    monkeypatch.setattr("app.api.routes_connections.dispatch_event", fake_dispatch)

    response = app_client.post(
        "/api/v1/connections/sabnzbd/test", json={"url": "http://sab.local", "api_key": "x"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == []


def test_prowlarr_test_endpoint_uses_stored_api_key_when_blank(app_client, monkeypatch):
    settings = app_client.get("/api/v1/settings").json()
    settings["indexers"] = [
        {
            "name": "Prowlarr",
            "type": "prowlarr",
            "url": "http://prowlarr.local",
            "api_key": "stored-prowlarr-key",
            "enabled": True,
        }
    ]
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["x-api-key"] = request.headers.get("X-Api-Key", "")
        return httpx.Response(200, json={"version": "1.21.0", "appName": "Prowlarr"})

    monkeypatch.setattr(
        "app.api.routes_connections.ProwlarrClient", _factory(ProwlarrClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://prowlarr.local"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen["x-api-key"] == "stored-prowlarr-key"


def test_sabnzbd_test_endpoint_does_not_send_stored_key_to_other_url(app_client, monkeypatch):
    settings = app_client.get("/api/v1/settings").json()
    settings["download_clients"] = [
        {
            "name": "SABnzbd",
            "type": "sabnzbd",
            "url": "http://sab.local",
            "api_key": "stored-sab-key",
            "category": "audiobooks",
            "enabled": True,
        }
    ]
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("apikey", ""))
        return httpx.Response(200, json={"version": "4.3.2"})

    monkeypatch.setattr(
        "app.api.routes_connections.SABnzbdClient", _factory(SABnzbdClient, handler)
    )
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://other.local"},
    )
    assert response.status_code == 200
    assert seen == [""]


def test_prowlarr_test_endpoint_does_not_send_stored_key_to_other_url(app_client, monkeypatch):
    settings = app_client.get("/api/v1/settings").json()
    settings["indexers"] = [
        {
            "name": "Prowlarr",
            "type": "prowlarr",
            "url": "http://prowlarr.local/",
            "api_key": "stored-prowlarr-key",
            "enabled": True,
        }
    ]
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-Api-Key", ""))
        return httpx.Response(200, json={"version": "1.21.0", "appName": "Prowlarr"})

    monkeypatch.setattr(
        "app.api.routes_connections.ProwlarrClient", _factory(ProwlarrClient, handler)
    )
    other = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://other.local"},
    )
    assert other.status_code == 200
    assert seen == [""]

    # Same URL (trailing-slash normalized) still reuses the stored key.
    seen.clear()
    same = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://prowlarr.local"},
    )
    assert same.status_code == 200
    assert seen == ["stored-prowlarr-key"]


# ---------------------------------------------------------------------------
# PROWLARR_API_KEY / FILE__PROWLARR_API_KEY must never be aimed at an
# arbitrary test URL; only a destination that is a configured Prowlarr may use
# the deployment secret.
# ---------------------------------------------------------------------------

_ENV_PROWLARR_KEY = "env-prowlarr-key"


@pytest.fixture(params=["env", "file"])
def prowlarr_env_secret(request, monkeypatch, tmp_path_factory):
    """Provide ``_ENV_PROWLARR_KEY`` via the plain or the ``FILE__`` variable."""
    monkeypatch.delenv("PROWLARR_API_KEY", raising=False)
    monkeypatch.delenv("FILE__PROWLARR_API_KEY", raising=False)
    if request.param == "env":
        monkeypatch.setenv("PROWLARR_API_KEY", _ENV_PROWLARR_KEY)
    else:
        secret_file = tmp_path_factory.mktemp("prowlarr-secret") / "prowlarr_key"
        secret_file.write_text(_ENV_PROWLARR_KEY + "\n", encoding="utf-8")
        monkeypatch.setenv("FILE__PROWLARR_API_KEY", str(secret_file))
    return request.param


def _save_prowlarr_indexer(app_client, *, url: str, api_key: str) -> None:
    settings = app_client.get("/api/v1/settings").json()
    settings["indexers"] = [
        {"name": "Prowlarr", "type": "prowlarr", "url": url, "api_key": api_key, "enabled": True}
    ]
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200


def _record_prowlarr_keys(monkeypatch) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-Api-Key", ""))
        return httpx.Response(200, json={"version": "1.21.0", "appName": "Prowlarr"})

    monkeypatch.setattr(
        "app.api.routes_connections.ProwlarrClient", _factory(ProwlarrClient, handler)
    )
    return seen


@pytest.mark.parametrize("configured", [False, True], ids=["no-indexer", "other-indexer-configured"])
def test_prowlarr_test_endpoint_never_sends_env_key_to_unmatched_url(
    app_client, monkeypatch, prowlarr_env_secret, configured
):
    if configured:
        _save_prowlarr_indexer(app_client, url="http://prowlarr.local", api_key="")
    seen = _record_prowlarr_keys(monkeypatch)

    response = app_client.post(
        "/api/v1/connections/prowlarr/test", json={"url": "http://attacker.example"}
    )
    assert response.status_code == 200
    assert seen == [""]

    # An explicitly empty key means the same thing: no key, no env fallback.
    seen.clear()
    response = app_client.post(
        "/api/v1/connections/prowlarr/test", json={"url": "http://attacker.example", "api_key": ""}
    )
    assert response.status_code == 200
    assert seen == [""]

    # A key typed into the form is still honoured for any destination.
    seen.clear()
    response = app_client.post(
        "/api/v1/connections/prowlarr/test",
        json={"url": "http://attacker.example", "api_key": "typed-key"},
    )
    assert response.status_code == 200
    assert seen == ["typed-key"]


def test_prowlarr_test_endpoint_configured_url_may_use_env_key(
    app_client, monkeypatch, prowlarr_env_secret
):
    _save_prowlarr_indexer(app_client, url="http://prowlarr.local/", api_key="")
    # The deployment key is only usable when the non-secret PROWLARR_URL matches.
    monkeypatch.setenv("PROWLARR_URL", "http://prowlarr.local")
    seen = _record_prowlarr_keys(monkeypatch)

    same_url =app_client.post(
        "/api/v1/connections/prowlarr/test", json={"url": "http://prowlarr.local"}
    )
    assert same_url.status_code == 200
    assert seen == [_ENV_PROWLARR_KEY]


def test_prowlarr_test_endpoint_saved_key_wins_over_env_key_at_configured_url(
    app_client, monkeypatch, prowlarr_env_secret
):
    _save_prowlarr_indexer(app_client, url="http://prowlarr.local", api_key="stored-prowlarr-key")
    seen = _record_prowlarr_keys(monkeypatch)

    response = app_client.post(
        "/api/v1/connections/prowlarr/test", json={"url": "http://prowlarr.local"}
    )
    assert response.status_code == 200
    assert seen == ["stored-prowlarr-key"]


# ---------------------------------------------------------------------------
# SABNZBD_API_KEY / FILE__SABNZBD_API_KEY must never be aimed at an arbitrary
# test URL; only a destination that is a configured SABnzbd may use it.
# ---------------------------------------------------------------------------

_ENV_SAB_KEY = "env-sabnzbd-key"


@pytest.fixture(params=["env", "file"])
def sabnzbd_env_secret(request, monkeypatch, tmp_path_factory):
    """Provide ``_ENV_SAB_KEY`` via the plain or the ``FILE__`` variable."""
    monkeypatch.delenv("SABNZBD_API_KEY", raising=False)
    monkeypatch.delenv("FILE__SABNZBD_API_KEY", raising=False)
    if request.param == "env":
        monkeypatch.setenv("SABNZBD_API_KEY", _ENV_SAB_KEY)
    else:
        secret_file = tmp_path_factory.mktemp("sabnzbd-secret") / "sabnzbd_key"
        secret_file.write_text(_ENV_SAB_KEY + "\n", encoding="utf-8")
        monkeypatch.setenv("FILE__SABNZBD_API_KEY", str(secret_file))
    return request.param


def _save_sabnzbd_client(app_client, *, url: str, api_key: str) -> None:
    settings = app_client.get("/api/v1/settings").json()
    settings["download_clients"] = [
        {
            "name": "SABnzbd",
            "type": "sabnzbd",
            "url": url,
            "api_key": api_key,
            "category": "audiobooks",
            "enabled": True,
        }
    ]
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200


def _record_sabnzbd_keys(monkeypatch) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("apikey", ""))
        return httpx.Response(200, json={"version": "4.3.2"})

    monkeypatch.setattr(
        "app.api.routes_connections.SABnzbdClient", _factory(SABnzbdClient, handler)
    )
    return seen


@pytest.mark.parametrize("configured", [False, True], ids=["no-client", "other-client-configured"])
def test_sabnzbd_test_endpoint_never_sends_env_key_to_unmatched_url(
    app_client, monkeypatch, sabnzbd_env_secret, configured
):
    # Even with a trusted SABNZBD_URL set, an arbitrary URL gets no key.
    monkeypatch.setenv("SABNZBD_URL", "http://sab.local")
    if configured:
        _save_sabnzbd_client(app_client, url="http://sab.local", api_key="")
    seen = _record_sabnzbd_keys(monkeypatch)

    response = app_client.post(
        "/api/v1/connections/sabnzbd/test", json={"url": "http://attacker.example"}
    )
    assert response.status_code == 200
    assert seen == [""]

    # An explicitly empty key means the same thing: no key, no env fallback.
    seen.clear()
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test", json={"url": "http://attacker.example", "api_key": ""}
    )
    assert response.status_code == 200
    assert seen == [""]

    # A key typed into the form is still honoured for the requested URL.
    seen.clear()
    response = app_client.post(
        "/api/v1/connections/sabnzbd/test",
        json={"url": "http://attacker.example", "api_key": "typed-key"},
    )
    assert response.status_code == 200
    assert seen == ["typed-key"]


def test_sabnzbd_test_endpoint_configured_url_may_use_env_key(
    app_client, monkeypatch, sabnzbd_env_secret
):
    _save_sabnzbd_client(app_client, url="http://sab.local/", api_key="")
    # The deployment key is only usable when the non-secret SABNZBD_URL matches.
    monkeypatch.setenv("SABNZBD_URL", "http://sab.local")
    seen = _record_sabnzbd_keys(monkeypatch)

    response = app_client.post(
        "/api/v1/connections/sabnzbd/test", json={"url": "http://sab.local"}
    )
    assert response.status_code == 200
    assert seen == [_ENV_SAB_KEY]

    # Omitted URL keeps falling back to the configured client.
    seen.clear()
    response = app_client.post("/api/v1/connections/sabnzbd/test", json={})
    assert response.status_code == 200
    assert seen == [_ENV_SAB_KEY]


@pytest.mark.parametrize("trusted", [None, "http://other-sab.local"], ids=["unset", "mismatch"])
def test_sabnzbd_test_endpoint_configured_url_without_trusted_binding_gets_no_key(
    app_client, monkeypatch, sabnzbd_env_secret, trusted
):
    _save_sabnzbd_client(app_client, url="http://sab.local", api_key="")
    if trusted is None:
        monkeypatch.delenv("SABNZBD_URL", raising=False)
    else:
        monkeypatch.setenv("SABNZBD_URL", trusted)
    seen = _record_sabnzbd_keys(monkeypatch)

    response = app_client.post(
        "/api/v1/connections/sabnzbd/test", json={"url": "http://sab.local"}
    )
    assert response.status_code == 200
    assert seen == [""]


def test_sabnzbd_test_endpoint_saved_key_wins_over_env_key_at_configured_url(
    app_client, monkeypatch, sabnzbd_env_secret
):
    _save_sabnzbd_client(app_client, url="http://sab.local", api_key="stored-sab-key")
    seen = _record_sabnzbd_keys(monkeypatch)

    response = app_client.post(
        "/api/v1/connections/sabnzbd/test", json={"url": "http://sab.local"}
    )
    assert response.status_code == 200
    assert seen == ["stored-sab-key"]


# ---------------------------------------------------------------------------
# AUDIOBOOKSHELF_API_KEY / M4B_CONVERTARR_API_KEY (and their FILE__ variants)
# are deployment secrets: a blank-key test must never aim them at a URL that
# is not the persisted configured destination.
# ---------------------------------------------------------------------------

_BEARER_INTEGRATIONS = {
    "audiobookshelf": {
        "route": "audiobookshelf",
        "client": AudiobookshelfClient,
        "section": "audiobookshelf",
        "env": "AUDIOBOOKSHELF_API_KEY",
        "url_env": "AUDIOBOOKSHELF_URL",
    },
    "m4b-convertarr": {
        "route": "m4b-convertarr",
        "client": M4BConvertarrClient,
        "section": "m4b_convertarr",
        "env": "M4B_CONVERTARR_API_KEY",
        "url_env": "M4B_CONVERTARR_URL",
    },
}
_ENV_BEARER_KEY = "env-deployment-key"


@pytest.fixture(params=["env", "file"])
def bearer_env_secret(request, monkeypatch, tmp_path_factory):
    """Provide ``_ENV_BEARER_KEY`` for both integrations via env or ``FILE__``.

    The non-secret ``*_URL`` binding names ``http://configured.local``.
    """
    for spec in _BEARER_INTEGRATIONS.values():
        monkeypatch.setenv(spec["url_env"], "http://configured.local")
        monkeypatch.delenv(spec["env"], raising=False)
        monkeypatch.delenv(f"FILE__{spec['env']}", raising=False)
        if request.param == "env":
            monkeypatch.setenv(spec["env"], _ENV_BEARER_KEY)
        else:
            secret_file = tmp_path_factory.mktemp("bearer-secret") / "key"
            secret_file.write_text(_ENV_BEARER_KEY + "\n", encoding="utf-8")
            monkeypatch.setenv(f"FILE__{spec['env']}", str(secret_file))
    return request.param


def _save_bearer_connection(app_client, integration: str, *, url: str, api_key: str) -> None:
    section = _BEARER_INTEGRATIONS[integration]["section"]
    settings = app_client.get("/api/v1/settings").json()
    settings["connections"][section] = {"url": url, "api_key": api_key, "enabled": True}
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200


def _record_bearer_headers(monkeypatch, integration: str, status: int = 200) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(status)

    spec = _BEARER_INTEGRATIONS[integration]
    attr = spec["client"].__name__
    monkeypatch.setattr(f"app.api.routes_connections.{attr}", _factory(spec["client"], handler))
    return seen


def _post_bearer_test(app_client, integration: str, **body):
    route = _BEARER_INTEGRATIONS[integration]["route"]
    return app_client.post(f"/api/v1/connections/{route}/test", json=body)


@pytest.mark.parametrize("integration", list(_BEARER_INTEGRATIONS))
@pytest.mark.parametrize("configured", [False, True], ids=["nothing-configured", "other-url-configured"])
def test_bearer_test_endpoint_never_sends_env_key_to_unmatched_url(
    app_client, monkeypatch, bearer_env_secret, integration, configured
):
    if configured:
        _save_bearer_connection(app_client, integration, url="http://configured.local", api_key="")
    seen = _record_bearer_headers(monkeypatch, integration)

    # Omitted key.
    response = _post_bearer_test(app_client, integration, url="http://attacker.example")
    assert response.status_code == 200
    assert seen == [""]

    # An explicitly blank key means the same thing: no key, no env fallback.
    seen.clear()
    response = _post_bearer_test(app_client, integration, url="http://attacker.example", api_key="")
    assert response.status_code == 200
    assert seen == [""]

    # A look-alike of the configured URL is still a different destination.
    if configured:
        seen.clear()
        response = _post_bearer_test(app_client, integration, url="http://configured.local:8080")
        assert response.status_code == 200
        assert seen == [""]


@pytest.mark.parametrize("integration", list(_BEARER_INTEGRATIONS))
def test_bearer_test_endpoint_does_not_send_stored_key_to_unmatched_url(
    app_client, monkeypatch, bearer_env_secret, integration
):
    _save_bearer_connection(
        app_client, integration, url="http://configured.local", api_key="stored-key"
    )
    seen = _record_bearer_headers(monkeypatch, integration)

    response = _post_bearer_test(app_client, integration, url="http://attacker.example")
    assert response.status_code == 200
    assert seen == [""]


@pytest.mark.parametrize("integration", list(_BEARER_INTEGRATIONS))
def test_bearer_test_endpoint_configured_url_may_use_env_key(
    app_client, monkeypatch, bearer_env_secret, integration
):
    _save_bearer_connection(app_client, integration, url="http://configured.local/", api_key="")
    seen = _record_bearer_headers(monkeypatch, integration)

    for url in ("http://configured.local", "http://configured.local/"):
        response = _post_bearer_test(app_client, integration, url=url)
        assert response.status_code == 200
    assert seen == [f"Bearer {_ENV_BEARER_KEY}"] * 2


@pytest.mark.parametrize("integration", list(_BEARER_INTEGRATIONS))
def test_bearer_test_endpoint_stored_key_wins_over_env_key_at_configured_url(
    app_client, monkeypatch, bearer_env_secret, integration
):
    _save_bearer_connection(
        app_client, integration, url="http://configured.local", api_key="stored-key"
    )
    seen = _record_bearer_headers(monkeypatch, integration)

    response = _post_bearer_test(app_client, integration, url="http://configured.local")
    assert response.status_code == 200
    assert seen == ["Bearer stored-key"]


@pytest.mark.parametrize("integration", list(_BEARER_INTEGRATIONS))
@pytest.mark.parametrize("configured", [False, True], ids=["nothing-configured", "configured-url"])
def test_bearer_test_endpoint_explicit_key_is_honoured(
    app_client, monkeypatch, bearer_env_secret, integration, configured
):
    if configured:
        _save_bearer_connection(
            app_client, integration, url="http://configured.local", api_key="stored-key"
        )
    seen = _record_bearer_headers(monkeypatch, integration)

    for url in ("http://attacker.example", "http://configured.local"):
        response = _post_bearer_test(app_client, integration, url=url, api_key="typed-key")
        assert response.status_code == 200
    assert seen == ["Bearer typed-key"] * 2


def test_bearer_clients_env_fallback_requires_trusted_url(monkeypatch):
    # ``api_key=None`` (what the runtime paths pass for a blank stored key)
    # resolves the deployment secret only at the URL named by *_URL.
    for spec in _BEARER_INTEGRATIONS.values():
        monkeypatch.setenv(spec["env"], "env-key")
        monkeypatch.setenv(spec["url_env"], "http://trusted.local")
        cls = spec["client"]
        assert cls(base_url="http://trusted.local", api_key=None).api_key == "env-key"
        assert cls(base_url="http://other.local", api_key=None).api_key == ""
        assert cls(base_url="http://trusted.local", api_key="").api_key == ""
        monkeypatch.delenv(spec["url_env"])
        assert cls(base_url="http://trusted.local", api_key=None).api_key == ""


def _capture_scan_headers(monkeypatch) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200)

    monkeypatch.setattr(
        "app.api.routes_connections.AudiobookshelfClient",
        _factory(AudiobookshelfClient, handler),
    )
    return seen


def _save_abs_scan_connection(app_client, *, url: str, api_key: str) -> None:
    settings = app_client.get("/api/v1/settings").json()
    settings["connections"]["audiobookshelf"] = {
        "url": url,
        "api_key": api_key,
        "enabled": True,
        "library_id": "lib-1",
    }
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200


def test_audiobookshelf_scan_runtime_uses_env_key_only_at_trusted_url(
    app_client, monkeypatch, bearer_env_secret
):
    seen = _capture_scan_headers(monkeypatch)

    _save_abs_scan_connection(app_client, url="http://configured.local", api_key="")
    assert app_client.post("/api/v1/connections/audiobookshelf/scan").status_code == 200
    assert seen == [f"Bearer {_ENV_BEARER_KEY}"]

    # A settings URL change must not re-aim the deployment secret.
    seen.clear()
    _save_abs_scan_connection(app_client, url="http://attacker.example", api_key="")
    assert app_client.post("/api/v1/connections/audiobookshelf/scan").status_code == 200
    assert seen == [""]

    # A stored key keeps working independently of the binding.
    seen.clear()
    _save_abs_scan_connection(app_client, url="http://attacker.example", api_key="stored-key")
    assert app_client.post("/api/v1/connections/audiobookshelf/scan").status_code == 200
    assert seen == ["Bearer stored-key"]


def test_audiobookshelf_scan_runtime_without_url_binding_gets_no_key(
    app_client, monkeypatch, bearer_env_secret
):
    monkeypatch.delenv("AUDIOBOOKSHELF_URL")
    seen = _capture_scan_headers(monkeypatch)
    _save_abs_scan_connection(app_client, url="http://configured.local", api_key="")

    assert app_client.post("/api/v1/connections/audiobookshelf/scan").status_code == 200
    assert seen == [""]


@pytest.mark.parametrize("integration", list(_BEARER_INTEGRATIONS))
def test_bearer_test_endpoint_configured_url_without_trusted_binding_gets_no_key(
    app_client, monkeypatch, bearer_env_secret, integration
):
    _save_bearer_connection(app_client, integration, url="http://configured.local", api_key="")
    seen = _record_bearer_headers(monkeypatch, integration)
    url_env = _BEARER_INTEGRATIONS[integration]["url_env"]

    for trusted in (None, "not a url", "http://other.local"):
        if trusted is None:
            monkeypatch.delenv(url_env)
        else:
            monkeypatch.setenv(url_env, trusted)
        response = _post_bearer_test(app_client, integration, url="http://configured.local")
        assert response.status_code == 200
    assert seen == ["", "", ""]


_SECRET_URL = "http://log-user:log-pass@logged.example:9999/path-marker?token=log-token&apikey=log-key"


@pytest.mark.parametrize("status", [200, 500], ids=["ok", "failed"])
@pytest.mark.parametrize("integration", ["audiobookshelf", "m4b-convertarr", "sabnzbd", "prowlarr"])
def test_connection_test_info_logs_never_contain_the_url(
    app_client, monkeypatch, caplog, integration, status
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"version": "1.0", "appName": "x"})

    clients = {
        "audiobookshelf": AudiobookshelfClient,
        "m4b-convertarr": M4BConvertarrClient,
        "sabnzbd": SABnzbdClient,
        "prowlarr": ProwlarrClient,
    }
    cls = clients[integration]
    monkeypatch.setattr(f"app.api.routes_connections.{cls.__name__}", _factory(cls, handler))

    with caplog.at_level(logging.DEBUG):
        response = app_client.post(
            f"/api/v1/connections/{integration}/test",
            json={"url": _SECRET_URL, "api_key": "typed-key"},
        )
    assert response.status_code == 200
    assert response.json()["ok"] is (status == 200)

    route_records = [r for r in caplog.records if r.name == "audiarr.api.connections"]
    assert route_records, "the connection test should still log its outcome"
    for marker in ("log-user", "log-pass", "log-token", "log-key", "logged.example", "path-marker"):
        assert marker not in caplog.text
    assert "typed-key" not in caplog.text
