"""qBittorrent routes (issue #81): manual grab routing, connection test,
settings secret handling and the no-automatic-torrent-grab guarantee.

MockTransport only; no live qBittorrent/Prowlarr/SABnzbd is contacted.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import load_settings, save_settings
from app.connections.prowlarr import ProwlarrClient
from app.connections.qbittorrent import QBittorrentClient
from app.connections.sabnzbd import SABnzbdClient
from app.models.settings import DownloadClient, Indexer

HASH = "a" * 40
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=Some+Book"
QB_URL = "http://qb.local:8080"
PROWLARR_DL = "http://prowlarr.local/1/download?apikey=P-SECRET&link=Zm9v&file=b.torrent"


def _qb_factory(handler):
    def make(base_url="", api_key=None, username=None, password=None, client=None):
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url or QB_URL)
        return QBittorrentClient(
            base_url=base_url or QB_URL, api_key=api_key, username=username,
            password=password, client=mock,
        )

    return make


def _simple_factory(real_cls, handler):
    def make(base_url="", api_key=None, client=None):
        url = base_url or "http://mock.local"
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)
        return real_cls(base_url=url, api_key=api_key, client=mock)

    return make


def _configure(*, qb=True, sab=True, prowlarr=True, qb_kwargs=None) -> None:
    settings = load_settings()
    settings.indexers = (
        [Indexer(name="Prowlarr", type="prowlarr", url="http://prowlarr.local",
                 api_key="p-key", enabled=True)] if prowlarr else []
    )
    clients = []
    if sab:
        clients.append(DownloadClient(name="SABnzbd", type="sabnzbd", url="http://sab.local",
                                      api_key="sab-key", category="audiobooks", enabled=True))
    if qb:
        kwargs = {"api_key": "qb-key-secret"}
        kwargs.update(qb_kwargs or {})
        clients.append(DownloadClient(**{
            "name": "qBittorrent", "type": "qbittorrent", "url": QB_URL,
            "category": "audiobooks", "tag": "audiarr", "enabled": True, **kwargs,
        }))
    settings.download_clients = clients
    save_settings(settings)


@pytest.fixture()
def forbid_sab(monkeypatch):
    def handler(request):  # pragma: no cover - must not run
        raise AssertionError("SABnzbd must not be contacted for a torrent grab")

    monkeypatch.setattr("app.api.routes_releases.SABnzbdClient", _simple_factory(SABnzbdClient, handler))
    monkeypatch.setattr("app.api.routes_releases.ProwlarrClient", _simple_factory(ProwlarrClient, handler))


@pytest.fixture()
def no_events(monkeypatch):
    events: list = []

    async def fake_dispatch(event, payload):
        events.append((event, payload))

    monkeypatch.setattr("app.api.routes_releases.dispatch_event", fake_dispatch)
    monkeypatch.setattr("app.api.routes_connections.dispatch_event", fake_dispatch)
    return events


def _grab(app_client, **overrides):
    body = {"indexer_id": 4, "guid": "g", "title": "Some Book", "protocol": "torrent",
            "magnet_url": MAGNET, "download_url": PROWLARR_DL}
    body.update(overrides)
    return app_client.post("/api/v1/releases/grab", json=body)


# ------------------------------------------------------------- manual grab


def test_torrent_grab_goes_to_qbittorrent_with_fixed_category_and_tag(
    app_client, monkeypatch, forbid_sab, no_events
):
    _configure()
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="Ok.")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    resp = _grab(app_client)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["torrent_hash"] == HASH and body["nzo_id"] is None
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer qb-key-secret"
    form = httpx.QueryParams(seen[0].content.decode())
    assert form["urls"] == MAGNET
    assert form["category"] == "audiobooks" and form["tags"] == "audiarr"
    assert "savepath" not in seen[0].content.decode().lower()
    assert no_events and no_events[0][0] == "grab"
    assert "qb-key-secret" not in json.dumps(no_events) and "P-SECRET" not in json.dumps(no_events)


def test_request_cannot_choose_savepath_or_category(app_client, monkeypatch, forbid_sab, no_events):
    _configure()
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        "app.api.routes_releases.QBittorrentClient",
        _qb_factory(lambda r: (seen.append(r), httpx.Response(200, text="Ok."))[1]),
    )
    resp = _grab(app_client, savepath="/etc", category="evil", tags="evil")
    assert resp.status_code == 200
    form = httpx.QueryParams(seen[0].content.decode())
    assert set(form.keys()) == {"urls", "category", "tags"}
    assert form["category"] == "audiobooks" and form["tags"] == "audiarr"


def test_http_torrent_url_from_configured_prowlarr_is_accepted(
    app_client, monkeypatch, forbid_sab, no_events
):
    _configure()
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        "app.api.routes_releases.QBittorrentClient",
        _qb_factory(lambda r: (seen.append(r), httpx.Response(200, text="Ok."))[1]),
    )
    resp = _grab(app_client, magnet_url=None)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert httpx.QueryParams(seen[0].content.decode())["urls"] == PROWLARR_DL
    assert resp.json()["torrent_hash"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"magnet_url": None, "download_url": "http://evil.example/x.torrent?apikey=LEAKME"},
        {"magnet_url": None, "download_url": "file:///etc/passwd"},
        {"magnet_url": None, "download_url": "ftp://prowlarr.local/x"},
        {"magnet_url": "magnet:?xt=urn:btih:short&tr=http://t/?k=LEAKME", "download_url": ""},
        {"magnet_url": None, "download_url": "magnet:?dn=LEAKME"},
        {"magnet_url": None, "download_url": ""},
    ],
)
def test_invalid_torrent_urls_are_rejected_without_echo(
    app_client, monkeypatch, forbid_sab, no_events, overrides
):
    _configure()

    def handler(request):  # pragma: no cover
        raise AssertionError("qBittorrent must not be contacted for an invalid URL")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    resp = _grab(app_client, **overrides)
    assert resp.status_code == 400
    assert "LEAKME" not in resp.text and "evil.example" not in resp.text


@pytest.mark.parametrize("qb_kwargs", [{"tag": ""}, {"tag": "  "}, {"category": ""}, {"category": " "}])
def test_grab_fails_clearly_when_category_or_tag_is_blank(
    app_client, monkeypatch, forbid_sab, no_events, qb_kwargs
):
    _configure(qb_kwargs=qb_kwargs)

    def handler(request):  # pragma: no cover
        raise AssertionError("qBittorrent must not be contacted without category and tag")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    resp = _grab(app_client)
    assert resp.status_code == 422
    assert "category and a tag" in resp.json()["detail"]
    assert no_events == []


@pytest.mark.parametrize(
    "download_url",
    [
        "http://prowlarr.local/api/v1/system/status?apikey=LEAKME",
        "http://prowlarr.local/1/download/../../api/v1/indexer?apikey=LEAKME",
        "http://prowlarr.local/x/download?apikey=LEAKME",
        "http://prowlarr.local/1/download/extra?apikey=LEAKME",
        "http://prowlarr.local/%2e%2e/1/download?apikey=LEAKME",
        "http://user:pw@prowlarr.local/1/download?apikey=LEAKME",
        "http://prowlarr.local:9999/1/download?apikey=LEAKME",
    ],
)
def test_prowlarr_origin_urls_must_be_canonical_download_urls(
    app_client, monkeypatch, forbid_sab, no_events, download_url
):
    _configure()

    def handler(request):  # pragma: no cover
        raise AssertionError("qBittorrent must not be contacted for a non-download URL")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    resp = _grab(app_client, magnet_url=None, download_url=download_url)
    assert resp.status_code == 400
    assert "LEAKME" not in resp.text


def test_prowlarr_download_url_with_url_base_is_accepted(
    app_client, monkeypatch, forbid_sab, no_events
):
    _configure()
    settings = load_settings()
    settings.indexers[0].url = "http://prowlarr.local/prowlarr/"
    save_settings(settings)
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        "app.api.routes_releases.QBittorrentClient",
        _qb_factory(lambda r: (seen.append(r), httpx.Response(200, text="Ok."))[1]),
    )
    ok = "http://prowlarr.local/prowlarr/12/download?apikey=K&link=Zm9v&file=b.torrent"
    assert _grab(app_client, magnet_url=None, download_url=ok).status_code == 200
    assert _grab(app_client, magnet_url=None, download_url=PROWLARR_DL).status_code == 400


def test_torrent_grab_503_without_qbittorrent_and_does_not_fall_back_to_sab(
    app_client, forbid_sab
):
    _configure(qb=False)
    resp = _grab(app_client)
    assert resp.status_code == 503
    assert "qBittorrent" in resp.json()["detail"]


def test_disabled_qbittorrent_is_not_used(app_client, forbid_sab):
    _configure()
    settings = load_settings()
    for c in settings.download_clients:
        if c.type == "qbittorrent":
            c.enabled = False
    save_settings(settings)
    assert _grab(app_client).status_code == 503


@pytest.mark.parametrize(
    "handler, expected",
    [
        (lambda r: httpx.Response(200, text="Fails."), "did not accept"),
        (lambda r: httpx.Response(403), "authentication failed"),
        (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("t", request=r)), "timed out"),
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("c", request=r)), "Could not reach"),
        (lambda r: httpx.Response(500), "unexpected HTTP status"),
    ],
)
def test_qbittorrent_failures_return_safe_messages(
    app_client, monkeypatch, forbid_sab, no_events, handler, expected
):
    _configure()
    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    resp = _grab(app_client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False and expected in body["message"]
    for secret in ("qb-key-secret", "P-SECRET", HASH, "prowlarr.local"):
        assert secret not in resp.text
    assert not no_events  # no grab event on failure


def test_usenet_grab_still_goes_to_sabnzbd_even_with_qbittorrent_configured(
    app_client, monkeypatch
):
    _configure()

    def qb_handler(request):  # pragma: no cover
        raise AssertionError("qBittorrent must not be contacted for usenet")

    def prowlarr_handler(request):
        return httpx.Response(200, content=b"<nzb/>")

    def sab_handler(request):
        assert request.url.params["mode"] == "addfile"
        return httpx.Response(200, json={"status": True, "nzo_ids": ["nzo_1"]})

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(qb_handler))
    monkeypatch.setattr(
        "app.api.routes_releases.ProwlarrClient", _simple_factory(ProwlarrClient, prowlarr_handler)
    )
    monkeypatch.setattr(
        "app.api.routes_releases.SABnzbdClient", _simple_factory(SABnzbdClient, sab_handler)
    )

    for extra in ({}, {"protocol": "usenet"}, {"protocol": "usenet", "magnet_url": MAGNET}):
        resp = app_client.post(
            "/api/v1/releases/grab",
            json={"indexer_id": 4, "guid": "g", "title": "T",
                  "download_url": "http://prowlarr.local/1/download?link=x", **extra},
        )
        assert resp.status_code == 200
        assert resp.json()["nzo_id"] == "nzo_1" and resp.json()["torrent_hash"] is None


def test_usenet_grab_without_download_url_is_still_422(app_client, forbid_sab):
    _configure()
    resp = app_client.post("/api/v1/releases/grab", json={"indexer_id": 4, "guid": "g", "title": "T"})
    assert resp.status_code == 422


# ----------------------------------------------------- connection test route


def test_qbittorrent_test_endpoint_ok_with_api_key(app_client, monkeypatch, no_events):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text="v5.2.0")

    monkeypatch.setattr("app.api.routes_connections.QBittorrentClient", _qb_factory(handler))
    resp = app_client.post("/api/v1/connections/qbittorrent/test", json={"url": QB_URL, "api_key": "k1"})
    assert resp.json() == {"ok": True, "message": "qBittorrent v5.2.0"}
    assert seen[0].headers["authorization"] == "Bearer k1"


def test_qbittorrent_test_endpoint_failure_is_safe(app_client, monkeypatch, no_events):
    monkeypatch.setattr(
        "app.api.routes_connections.QBittorrentClient", _qb_factory(lambda r: httpx.Response(403))
    )
    resp = app_client.post(
        "/api/v1/connections/qbittorrent/test", json={"url": QB_URL, "api_key": "top-secret"}
    )
    body = resp.json()
    assert resp.status_code == 200 and body["ok"] is False
    assert "authentication failed" in body["message"]
    assert "top-secret" not in resp.text and "top-secret" not in json.dumps(no_events)


def test_qbittorrent_test_endpoint_unreachable_without_mocks(app_client):
    resp = app_client.post(
        "/api/v1/connections/qbittorrent/test", json={"url": "http://127.0.0.1:1", "api_key": "x"}
    )
    assert resp.status_code == 200 and resp.json()["ok"] is False


def test_qbittorrent_test_uses_stored_secret_only_for_same_url(app_client, monkeypatch):
    _configure(qb_kwargs={"api_key": "stored-key"})
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, text="v5.2.0")

    monkeypatch.setattr("app.api.routes_connections.QBittorrentClient", _qb_factory(handler))
    assert app_client.post("/api/v1/connections/qbittorrent/test", json={"url": QB_URL}).json()["ok"] is True
    assert seen == ["Bearer stored-key"]

    # A different URL must NOT receive the stored key: fails closed, no request.
    other = app_client.post("/api/v1/connections/qbittorrent/test", json={"url": "http://other.example"}).json()
    assert other["ok"] is False and "no credentials" in other["message"]
    assert seen == ["Bearer stored-key"]


def test_qbittorrent_test_legacy_password_filled_from_stored_for_same_user(app_client, monkeypatch):
    _configure(qb_kwargs={"api_key": "", "username": "alice", "password": "stored-pw"})
    bodies = []

    def handler(request):
        if request.url.path == "/api/v2/auth/login":
            bodies.append(request.content.decode())
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=s1; path=/"})
        return httpx.Response(200, text="v4.6.0")

    monkeypatch.setattr("app.api.routes_connections.QBittorrentClient", _qb_factory(handler))
    resp = app_client.post("/api/v1/connections/qbittorrent/test", json={"url": QB_URL, "username": "alice"})
    assert resp.json()["ok"] is True
    assert "password=stored-pw" in bodies[0]


# ------------------------------------------------------ settings secrets


SECRETS = ("sab-key", "qb-key-secret", "legacy-pw", "p-key")


def _raw_get(app_client) -> str:
    resp = app_client.get("/api/v1/settings")
    assert resp.status_code == 200
    return resp.text


def test_settings_get_and_put_never_return_download_client_secrets(app_client):
    _configure(qb_kwargs={"username": "alice", "password": "legacy-pw"})
    raw = _raw_get(app_client)
    for secret in ("sab-key", "qb-key-secret", "legacy-pw"):
        assert secret not in raw
    body = json.loads(raw)
    for client in body["download_clients"]:
        assert "api_key" not in client and "password" not in client
    qb = next(c for c in body["download_clients"] if c["type"] == "qbittorrent")
    assert qb["username"] == "alice" and qb["api_key_set"] is True and qb["password_set"] is True
    sab = next(c for c in body["download_clients"] if c["type"] == "sabnzbd")
    assert sab["api_key_set"] is True and sab["password_set"] is False

    put = app_client.put("/api/v1/settings", json={**body, "download_clients": [
        {**c, "api_key": "sab-key" if c["type"] == "sabnzbd" else "qb-key-secret"}
        for c in body["download_clients"]
    ]})
    assert put.status_code == 200
    for secret in ("sab-key", "qb-key-secret", "legacy-pw"):
        assert secret not in put.text


def test_full_settings_save_without_secrets_preserves_them(app_client):
    _configure(qb_kwargs={"username": "alice", "password": "legacy-pw"})
    doc = json.loads(_raw_get(app_client))  # secrets are absent, exactly like the UI sees them
    doc["ui"]["language"] = "de"
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200

    stored = {c.type: c for c in load_settings().download_clients}
    assert stored["sabnzbd"].api_key == "sab-key"
    assert stored["qbittorrent"].api_key == "qb-key-secret"
    assert stored["qbittorrent"].password == "legacy-pw"
    assert load_settings().ui.language == "de"

    # Blank strings (the existing UI's "empty means keep") preserve as well.
    doc["download_clients"] = [{**c, "api_key": "", "password": ""} for c in doc["download_clients"]]
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200
    stored = {c.type: c for c in load_settings().download_clients}
    assert stored["sabnzbd"].api_key == "sab-key"
    assert stored["qbittorrent"].password == "legacy-pw"


def test_new_non_blank_secret_replaces_stored_and_only_matching_client_is_filled(app_client):
    _configure()
    doc = json.loads(_raw_get(app_client))
    for c in doc["download_clients"]:
        if c["type"] == "qbittorrent":
            c["api_key"] = "rotated-key"
        if c["type"] == "sabnzbd":
            c["name"] = "Renamed SAB"  # sole SAB client: unambiguous rename keeps the key
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200
    stored = {c.type: c for c in load_settings().download_clients}
    assert stored["qbittorrent"].api_key == "rotated-key"
    assert stored["sabnzbd"].api_key == "sab-key" and stored["sabnzbd"].name == "Renamed SAB"


def _two_sab_settings():
    settings = load_settings()
    settings.download_clients = [
        DownloadClient(name="SAB A", type="sabnzbd", url="http://a", api_key="key-a", enabled=True),
        DownloadClient(name="SAB B", type="sabnzbd", url="http://b", api_key="key-b", enabled=True),
    ]
    save_settings(settings)


def test_ambiguous_rename_with_blank_secrets_is_rejected_and_stores_nothing(app_client):
    _two_sab_settings()
    doc = json.loads(_raw_get(app_client))
    doc["download_clients"][0]["name"] = "SAB A renamed"
    doc["download_clients"][1]["name"] = "SAB B renamed"
    resp = app_client.put("/api/v1/settings", json=doc)
    assert resp.status_code == 422
    assert "key-a" not in resp.text and "key-b" not in resp.text
    assert [c.name for c in load_settings().download_clients] == ["SAB A", "SAB B"]
    assert [c.api_key for c in load_settings().download_clients] == ["key-a", "key-b"]


def test_ambiguous_rename_succeeds_when_secrets_are_re_entered(app_client):
    _two_sab_settings()
    doc = json.loads(_raw_get(app_client))
    for c, key in zip(doc["download_clients"], ("new-a", "new-b"), strict=True):
        c["name"] += " renamed"
        c["api_key"] = key
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200
    assert [c.api_key for c in load_settings().download_clients] == ["new-a", "new-b"]


def test_adding_second_client_keeps_existing_secrets(app_client):
    _two_sab_settings()
    doc = json.loads(_raw_get(app_client))
    doc["download_clients"].append({"name": "SAB C", "type": "sabnzbd", "url": "http://c"})
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200
    assert [c.api_key for c in load_settings().download_clients] == ["key-a", "key-b", ""]


def test_deleting_a_client_is_not_blocked(app_client):
    _two_sab_settings()
    doc = json.loads(_raw_get(app_client))
    doc["download_clients"] = doc["download_clients"][:1]
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200
    assert [c.api_key for c in load_settings().download_clients] == ["key-a"]


def test_indexer_secret_handling_is_unchanged(app_client):
    """Out of scope for #81: indexers keep their existing behaviour."""
    _configure()
    assert "p-key" in _raw_get(app_client)


# --------------------------------------------- no automatic torrent grab


async def test_wanted_scheduler_never_contacts_qbittorrent(monkeypatch):
    """qBittorrent is manual-grab only: even with Prowlarr + qBittorrent (and
    a torrent release that fits) the scheduled search never submits anything."""
    import importlib
    import tempfile

    from tests.test_wanted_scheduler import (
        _FITTING_RELEASE,
        _create_cutoff_candidate,
        _set_profile,
    )

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("AUDIARR_CONFIG_DIR", tmp)
        from app import config as config_module

        importlib.reload(config_module)
        from app.db import get_conn, migrate
        from app.wanted_scheduler import WantedSearchScheduler

        migrate()
        _set_profile()
        _configure(sab=False)

        async def forbidden(self, *a, **k):  # pragma: no cover - must not run
            raise AssertionError("qBittorrent contacted by the wanted scheduler")

        monkeypatch.setattr(QBittorrentClient, "_call", forbidden)
        torrent_release = {**_FITTING_RELEASE, "protocol": "torrent", "magnetUrl": MAGNET}

        def prowlarr_handler(request):
            return httpx.Response(200, json=[torrent_release])

        monkeypatch.setattr(
            "app.api.routes_wanted.ProwlarrClient", _simple_factory(ProwlarrClient, prowlarr_handler)
        )
        with get_conn() as conn:
            _create_cutoff_candidate(conn)
            assert await WantedSearchScheduler().run_once() is True
            states = [r["status"] for r in conn.execute("SELECT status FROM wanted_search_state")]
        assert "grabbed" not in states
