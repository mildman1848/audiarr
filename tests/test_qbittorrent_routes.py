"""qBittorrent routes (issue #81): manual grab routing, connection test,
settings secret handling and the no-automatic-torrent-grab guarantee.

MockTransport only; no live qBittorrent/Prowlarr/SABnzbd is contacted.
"""

from __future__ import annotations

import json
import logging

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
PROWLARR_DL = "http://prowlarr.local/1/download?apikey=p-key&link=Zm9v&file=b.torrent"


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
    assert "qb-key-secret" not in json.dumps(no_events) and "p-key" not in json.dumps(no_events)


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
    ok = "http://prowlarr.local/prowlarr/12/download?apikey=p-key&link=Zm9v&file=b.torrent"
    assert _grab(app_client, magnet_url=None, download_url=ok).status_code == 200
    assert _grab(app_client, magnet_url=None, download_url=PROWLARR_DL).status_code == 400


def test_prowlarr_proxy_query_values_are_url_decoded_and_order_independent(
    app_client, monkeypatch, forbid_sab, no_events
):
    _configure()
    settings = load_settings()
    settings.indexers[0].api_key = "p/key+1 x"
    save_settings(settings)
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        "app.api.routes_releases.QBittorrentClient",
        _qb_factory(lambda r: (seen.append(r), httpx.Response(200, text="Ok."))[1]),
    )
    real = (
        "http://prowlarr.local/7/download?apikey=p%2Fkey%2B1%20x"
        "&link=Zm9vJTJCYmFy%3D%3D&file=My%20Book%20%5BM4B%5D.torrent"
    )
    reordered = (
        "http://prowlarr.local/7/download?file=b.torrent&link=Zm9v&apikey=p%2Fkey%2B1+x"
    )
    for url in (real, reordered):
        resp = _grab(app_client, magnet_url=None, download_url=url)
        assert resp.status_code == 200 and resp.json()["ok"] is True
    assert [httpx.QueryParams(r.content.decode())["urls"] for r in seen] == [real, reordered]


_BASE = "http://prowlarr.local/1/download"


@pytest.mark.parametrize(
    "download_url",
    [
        # extra query parameter, including a smuggled absolute URL
        f"{_BASE}?apikey=p-key&link=Zm9v&file=b.torrent&url=http://127.0.0.1/x.torrent",
        f"{_BASE}?apikey=p-key&link=Zm9v&file=b.torrent&extra=1",
        f"{_BASE}?apikey=p-key&link=Zm9v&file=b.torrent&",
        # duplicate keys
        f"{_BASE}?apikey=p-key&link=Zm9v&link=YmFy&file=b.torrent",
        f"{_BASE}?apikey=p-key&apikey=p-key&link=Zm9v&file=b.torrent",
        f"{_BASE}?apikey=p-key&link=Zm9v&file=a.torrent&file=b.torrent",
        # missing keys / no query at all
        f"{_BASE}?apikey=p-key&link=Zm9v",
        f"{_BASE}?apikey=p-key&file=b.torrent",
        f"{_BASE}?link=Zm9v&file=b.torrent",
        _BASE,
        # blank values
        f"{_BASE}?apikey=p-key&link=&file=b.torrent",
        f"{_BASE}?apikey=p-key&link=Zm9v&file=",
        f"{_BASE}?apikey=&link=Zm9v&file=b.torrent",
        f"{_BASE}?apikey=p-key&link&file=b.torrent",
        # API key mismatch (case, prefix, other key)
        f"{_BASE}?apikey=P-KEY&link=Zm9v&file=b.torrent",
        f"{_BASE}?apikey=p-key2&link=Zm9v&file=b.torrent",
        f"{_BASE}?apikey=LEAKME&link=Zm9v&file=b.torrent",
        # malformed percent encoding
        f"{_BASE}?apikey=p-key&link=Zm9v%&file=b.torrent",
        f"{_BASE}?apikey=p-key&link=Zm9v&file=b%2.torrent",
        f"{_BASE}?apikey=p-key&link=Zm9v&file=b%zz.torrent",
        f"{_BASE}?apikey=p-key&link=Zm9v&file=%ff.torrent",
        # fragment
        f"{_BASE}?apikey=p-key&link=Zm9v&file=b.torrent#frag",
        f"{_BASE}?apikey=p-key&link=Zm9v&file=b.torrent#",
    ],
)
def test_prowlarr_download_url_requires_canonical_proxy_query(
    app_client, monkeypatch, forbid_sab, no_events, download_url
):
    _configure()

    def handler(request):  # pragma: no cover
        raise AssertionError("qBittorrent must not be contacted for a non-canonical query")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    resp = _grab(app_client, magnet_url=None, download_url=download_url)
    assert resp.status_code == 400
    for leaked in ("LEAKME", "p-key", "127.0.0.1", "Zm9v", "prowlarr.local"):
        assert leaked not in resp.text
    assert no_events == []


def _file_url(file_value: str) -> str:
    return f"{_BASE}?apikey=p-key&link=Zm9v&file={file_value}"


@pytest.mark.parametrize(
    "file_value",
    [
        # plain traversal / absolute paths
        "../etc/passwd",
        "/etc/passwd",
        "a/../b",
        "a/./b",
        "a%5C..%5Cb",
        ".",
        "..",
        # Windows drive and UNC paths
        "C%3A%5CWindows%5CSystem32%5Cevil.torrent",
        "C%3A/Windows/evil.torrent",
        "%5C%5Cuncsrv%5Cshare%5Cevil.torrent",
        "//uncsrv/share/evil.torrent",
        # percent-encoded once, decoded by the framework
        "%2e%2e%2fetc%2fpasswd",
        "%2Fetc%2Fpasswd",
        "..%5CWindows%5Cevil.torrent",
        # double-encoded, decoded again by Prowlarr's UrlDecode
        "%252e%252e%252fetc%252fpasswd",
        "%252Fetc%252Fpasswd",
        "..%255CWindows%255Cevil.torrent",
        "a%252F..%252Fb",
        "C%253A%255CWindows%255Cevil.torrent",
        # control characters, plain and double-encoded
        "a%00b.torrent",
        "a%250Ab.torrent",
        "a%0D%0Ab.torrent",
        # invalid UTF-8 that only appears at the second decode
        "%25ff.torrent",
    ],
)
def test_prowlarr_download_url_rejects_path_abuse_in_file(
    app_client, monkeypatch, forbid_sab, no_events, caplog, file_value
):
    _configure()

    def handler(request):  # pragma: no cover
        raise AssertionError("qBittorrent must not be contacted for an unsafe filename")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    with caplog.at_level(logging.DEBUG):
        resp = _grab(app_client, magnet_url=None, download_url=_file_url(file_value))
    assert resp.status_code == 400
    for leaked in ("passwd", "Windows", "uncsrv", "evil", "p-key", "prowlarr.local", "Zm9v"):
        assert leaked not in resp.text
        assert leaked not in caplog.text
    assert no_events == []


@pytest.mark.parametrize(
    "file_value",
    [
        "AC/DC%20-%20Back%20in%20Black.torrent",
        "AC%2FDC%20-%20Back%20in%20Black.torrent",
        "AC%252FDC%20-%20Back%20in%20Black.torrent",
        "AC%5CDC.torrent",
        "Book%3A%20Part%201.torrent",
        "Dr.%20Who%20%5BM4B%5D.torrent",
        "My..Book.torrent",
        "..hidden.torrent",
    ],
)
def test_prowlarr_download_url_accepts_ordinary_titles_in_file(
    app_client, monkeypatch, forbid_sab, no_events, file_value
):
    _configure()
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        "app.api.routes_releases.QBittorrentClient",
        _qb_factory(lambda r: (seen.append(r), httpx.Response(200, text="Ok."))[1]),
    )
    resp = _grab(app_client, magnet_url=None, download_url=_file_url(file_value))
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert len(seen) == 1


_SSRF = "http://169.254.169.254/latest/meta-data"
_SMUGGLED_MAGNET = f"magnet:?xt=urn:btih:{'b' * 40}"
# Raw (not percent-encoded) control characters. URL parsers drop CR/LF/TAB
# before validating, but the raw string is what qBittorrent would receive in
# its newline-separated ``urls`` field.
_RAW_CONTROL_DOWNLOAD_URLS = [
    # CR / LF / TAB embedded in host, path and query
    pytest.param("http://prowlarr.\rlocal/1/download?apikey=p-key&link=Zm9v&file=b.torrent", id="cr-host"),
    pytest.param("http://prowlarr.\nlocal/1/download?apikey=p-key&link=Zm9v&file=b.torrent", id="lf-host"),
    pytest.param("http://prowlarr.\tlocal/1/download?apikey=p-key&link=Zm9v&file=b.torrent", id="tab-host"),
    pytest.param("http://prowlarr.local/1/down\rload?apikey=p-key&link=Zm9v&file=b.torrent", id="cr-path"),
    pytest.param("http://prowlarr.local/1/down\nload?apikey=p-key&link=Zm9v&file=b.torrent", id="lf-path"),
    pytest.param("http://prowlarr.local/1/down\tload?apikey=p-key&link=Zm9v&file=b.torrent", id="tab-path"),
    pytest.param("http://prowlarr.local/1/download?apikey=p-\rkey&link=Zm9v&file=b.torrent", id="cr-query"),
    pytest.param("http://prowlarr.local/1/download?apikey=p-key&link=Zm9v&file=b.\ntorrent", id="lf-query"),
    pytest.param("http://prowlarr.local/1/download?apikey=p-key&link=Zm9v&file=b.\ttorrent", id="tab-query"),
    # a CRLF-separated second URL smuggled through the query ``file`` value
    pytest.param(f"{_BASE}?apikey=p-key&link=Zm9v&file=b.torrent\r\n{_SSRF}", id="crlf-file-http"),
    pytest.param(f"{_BASE}?apikey=p-key&link=Zm9v&file=b.torrent\n{_SSRF}", id="lf-file-http"),
    pytest.param(
        f"{_BASE}?apikey=p-key&link=Zm9v&file=b.torrent\r\n{_SMUGGLED_MAGNET}",
        id="crlf-file-magnet",
    ),
    # leading / trailing control characters (stripped by .strip() otherwise)
    pytest.param(f"{PROWLARR_DL}\n", id="trailing-lf"),
    pytest.param(f"\r\n{PROWLARR_DL}", id="leading-crlf"),
    pytest.param(f"{PROWLARR_DL}\t", id="trailing-tab"),
    # other C0 / C1 controls
    pytest.param(f"{PROWLARR_DL}\x00", id="nul"),
    pytest.param(f"{PROWLARR_DL}\x7f", id="del"),
    pytest.param(f"{PROWLARR_DL}\x85", id="c1-nel"),
    # raw control characters in a magnet passed through download_url
    pytest.param(f"{MAGNET}\r\n{_SSRF}", id="magnet-crlf-http"),
    pytest.param(f"{MAGNET}\n", id="magnet-trailing-lf"),
]


def _assert_rejected_and_untouched(resp, caplog, no_events) -> None:
    assert 400 <= resp.status_code < 500
    for leaked in ("169.254.169.254", "p-key", "prowlarr.local", "Zm9v", "b" * 40, "latest"):
        assert leaked not in resp.text
        assert leaked not in caplog.text
    assert no_events == []


@pytest.mark.parametrize("download_url", _RAW_CONTROL_DOWNLOAD_URLS)
def test_raw_control_chars_in_download_url_are_rejected_before_forwarding(
    app_client, monkeypatch, forbid_sab, no_events, caplog, download_url
):
    _configure()
    contacted: list[httpx.Request] = []

    def handler(request):  # pragma: no cover - must not run
        contacted.append(request)
        raise AssertionError("qBittorrent must not be contacted for raw control characters")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    with caplog.at_level(logging.DEBUG):
        resp = _grab(app_client, magnet_url=None, download_url=download_url)
    assert resp.status_code == 400
    assert contacted == []
    _assert_rejected_and_untouched(resp, caplog, no_events)


@pytest.mark.parametrize(
    "magnet_url",
    [
        pytest.param(f"{MAGNET}\n", id="trailing-lf"),
        pytest.param(f"\r\n{MAGNET}", id="leading-crlf"),
        pytest.param(f"{MAGNET}\r\n{_SSRF}", id="crlf-http"),
        pytest.param(f"{MAGNET}\n{_SMUGGLED_MAGNET}", id="lf-magnet"),
        pytest.param(f"{MAGNET}&dn=a\tb", id="tab-query"),
        pytest.param(f"magnet:?\rxt=urn:btih:{HASH}", id="cr-query-start"),
    ],
)
@pytest.mark.parametrize("download_url", [PROWLARR_DL, ""], ids=["valid-download-url", "no-download-url"])
def test_raw_control_chars_in_magnet_url_are_rejected_even_with_a_valid_fallback(
    app_client, monkeypatch, forbid_sab, no_events, caplog, magnet_url, download_url
):
    """A tainted magnet_url must fail the grab, not silently fall through to
    the other candidate."""
    _configure()
    contacted: list[httpx.Request] = []

    def handler(request):  # pragma: no cover - must not run
        contacted.append(request)
        raise AssertionError("qBittorrent must not be contacted for raw control characters")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    with caplog.at_level(logging.DEBUG):
        resp = _grab(app_client, magnet_url=magnet_url, download_url=download_url)
    assert resp.status_code == 400
    assert contacted == []
    _assert_rejected_and_untouched(resp, caplog, no_events)


def test_raw_control_chars_rejected_when_tainted_url_is_not_the_selected_candidate(
    app_client, monkeypatch, forbid_sab, no_events, caplog
):
    """A valid magnet wins selection, but a control character in the *other*
    candidate still rejects the whole request."""
    _configure()
    contacted: list[httpx.Request] = []

    def handler(request):  # pragma: no cover - must not run
        contacted.append(request)
        raise AssertionError("qBittorrent must not be contacted for raw control characters")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    with caplog.at_level(logging.DEBUG):
        resp = _grab(app_client, magnet_url=MAGNET, download_url=f"{PROWLARR_DL}\r\n{_SSRF}")
    assert resp.status_code == 400
    assert contacted == []
    _assert_rejected_and_untouched(resp, caplog, no_events)


@pytest.mark.parametrize(
    "overrides, expected_urls",
    [
        pytest.param(
            {"magnet_url": f"magnet:?xt=urn:btih:{HASH}&dn=Some%20Book%20%5BM4B%5D%0D%0A"},
            f"magnet:?xt=urn:btih:{HASH}&dn=Some%20Book%20%5BM4B%5D%0D%0A",
            id="encoded-magnet-title",
        ),
        pytest.param(
            {"magnet_url": None, "download_url": _file_url("Dr.%20Who%20%5BM4B%5D%20-%20Part%201.torrent")},
            _file_url("Dr.%20Who%20%5BM4B%5D%20-%20Part%201.torrent"),
            id="encoded-prowlarr-title",
        ),
        pytest.param(
            {"magnet_url": f"  {MAGNET}  "},
            MAGNET,
            id="surrounding-spaces-still-trimmed",
        ),
    ],
)
def test_encoded_titles_and_plain_whitespace_remain_accepted(
    app_client, monkeypatch, forbid_sab, no_events, overrides, expected_urls
):
    _configure()
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        "app.api.routes_releases.QBittorrentClient",
        _qb_factory(lambda r: (seen.append(r), httpx.Response(200, text="Ok."))[1]),
    )
    resp = _grab(app_client, **overrides)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert len(seen) == 1
    assert httpx.QueryParams(seen[0].content.decode())["urls"] == expected_urls


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
    for secret in ("qb-key-secret", "p-key", HASH, "prowlarr.local"):
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
            json={"indexer_id": 4, "guid": "g", "title": "T", "download_url": PROWLARR_DL, **extra},
        )
        assert resp.status_code == 200
        assert resp.json()["nzo_id"] == "nzo_1" and resp.json()["torrent_hash"] is None


_USENET_FOREIGN_DOWNLOAD_URLS = [
    pytest.param("http://evil.example/1/download?apikey=p-key&link=Zm9v&file=b.nzb", id="foreign-host"),
    pytest.param(
        "http://prowlarr.local:9999/1/download?apikey=p-key&link=Zm9v&file=b.nzb", id="other-port"
    ),
    pytest.param("http://prowlarr.local/api/v1/indexer?apikey=p-key&link=Zm9v&file=b.nzb", id="not-download"),
    pytest.param("http://prowlarr.local/1/download?apikey=other-key&link=Zm9v&file=b.nzb", id="wrong-key"),
    pytest.param("http://prowlarr.local/1/download?link=x", id="incomplete-query"),
    pytest.param(f"{PROWLARR_DL}&extra=1", id="extra-query-key"),
    pytest.param("file:///etc/passwd", id="file-scheme"),
    pytest.param("", id="empty"),
    # raw control characters (rejected on the unmodified string)
    pytest.param(f"{PROWLARR_DL}\n", id="trailing-lf"),
    pytest.param(f"\r\n{PROWLARR_DL}", id="leading-crlf"),
    pytest.param(f"{PROWLARR_DL}\r\n{_SSRF}", id="crlf-smuggled-url"),
    pytest.param("http://prowlarr.\tlocal/1/download?apikey=p-key&link=Zm9v&file=b.nzb", id="tab-host"),
    pytest.param(f"{PROWLARR_DL}\x00", id="nul"),
]


@pytest.mark.parametrize("download_url", _USENET_FOREIGN_DOWNLOAD_URLS)
def test_usenet_grab_rejects_non_prowlarr_urls_before_any_client_call(
    app_client, monkeypatch, no_events, caplog, download_url
):
    """The Prowlarr key is attached to the NZB fetch, so a caller-supplied URL
    must be vetted first: no Prowlarr, SABnzbd or qBittorrent request is made."""
    _configure()
    contacted: list[httpx.Request] = []

    def handler(request):  # pragma: no cover - must not run
        contacted.append(request)
        raise AssertionError("no downstream client may be contacted for a rejected NZB URL")

    monkeypatch.setattr("app.api.routes_releases.QBittorrentClient", _qb_factory(handler))
    monkeypatch.setattr("app.api.routes_releases.ProwlarrClient", _simple_factory(ProwlarrClient, handler))
    monkeypatch.setattr("app.api.routes_releases.SABnzbdClient", _simple_factory(SABnzbdClient, handler))

    with caplog.at_level(logging.DEBUG):
        resp = app_client.post(
            "/api/v1/releases/grab",
            json={"indexer_id": 4, "guid": "g", "title": "T", "protocol": "usenet",
                  "download_url": download_url},
        )
    assert resp.status_code in (400, 422)
    assert contacted == []
    for leaked in ("evil.example", "169.254.169.254", "p-key", "other-key", "Zm9v", "sab-key"):
        assert leaked not in resp.text
        assert leaked not in caplog.text
    assert no_events == []


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


def test_indexer_api_key_is_write_only_and_blank_put_preserves_it_at_unchanged_url(app_client):
    _configure()
    raw = _raw_get(app_client)
    assert "p-key" not in raw
    body = json.loads(raw)
    assert len(body["indexers"]) == 1
    assert "api_key" not in body["indexers"][0]
    assert body["indexers"][0]["api_key_set"] is True

    # Round trip exactly as the UI sees it (key absent), then with an explicit blank.
    for blank in (None, ""):
        if blank is not None:
            body["indexers"][0]["api_key"] = blank
        put = app_client.put("/api/v1/settings", json=body)
        assert put.status_code == 200
        assert "p-key" not in put.text
        returned = put.json()["indexers"][0]
        assert "api_key" not in returned and returned["api_key_set"] is True
        assert load_settings().indexers[0].api_key == "p-key"
        assert "p-key" not in _raw_get(app_client)


def test_indexer_blank_key_with_changed_url_is_rejected_and_mutates_nothing(app_client):
    _configure()
    before = load_settings().model_dump()
    doc = json.loads(_raw_get(app_client))
    doc["ui"]["language"] = "de"  # must not be persisted by a rejected save
    doc["indexers"][0]["url"] = "http://attacker.example"

    resp = app_client.put("/api/v1/settings", json=doc)
    assert resp.status_code == 422
    assert "p-key" not in resp.text and "attacker.example" not in resp.text

    after = load_settings()
    assert after.model_dump() == before
    assert after.indexers[0].url == "http://prowlarr.local"
    assert after.indexers[0].api_key == "p-key"
    assert after.ui.language != "de"


def test_indexer_url_change_succeeds_with_an_explicitly_re_entered_key(app_client):
    _configure()
    doc = json.loads(_raw_get(app_client))
    doc["indexers"][0]["url"] = "http://new-prowlarr.local"
    doc["indexers"][0]["api_key"] = "brand-new-key"

    put = app_client.put("/api/v1/settings", json=doc)
    assert put.status_code == 200
    assert "brand-new-key" not in put.text and "p-key" not in put.text
    assert put.json()["indexers"][0]["api_key_set"] is True
    stored = load_settings().indexers[0]
    assert stored.url == "http://new-prowlarr.local"
    assert stored.api_key == "brand-new-key"

    # Rotating the key at an unchanged URL works as well.
    doc["indexers"][0]["api_key"] = "rotated-key"
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200
    assert load_settings().indexers[0].api_key == "rotated-key"


# ---- download-client secrets are bound to their destination

_RETARGET_CASES = [
    pytest.param("sabnzbd", None, {"api_key": "new-sab-key"}, id="sabnzbd-api-key"),
    pytest.param("qbittorrent", None, {"api_key": "new-qb-key"}, id="qbittorrent-api-key"),
    pytest.param(
        "qbittorrent",
        {"api_key": "", "username": "alice", "password": "legacy-pw"},
        {"password": "new-pw"},
        id="qbittorrent-legacy-password",
    ),
]
_NEW_CLIENT_URL = "http://attacker.example:8080"


def _client_doc_retargeted(app_client, client_type: str) -> tuple[dict, dict]:
    doc = json.loads(_raw_get(app_client))
    target = next(c for c in doc["download_clients"] if c["type"] == client_type)
    target["url"] = _NEW_CLIENT_URL
    return doc, target


@pytest.mark.parametrize("client_type, qb_kwargs, reentered", _RETARGET_CASES)
def test_download_client_blank_secret_retarget_is_rejected_and_mutates_nothing(
    app_client, client_type, qb_kwargs, reentered
):
    _configure(qb_kwargs=qb_kwargs)
    before = load_settings().model_dump()
    doc, _ = _client_doc_retargeted(app_client, client_type)
    doc["ui"]["language"] = "de"  # must not be persisted by a rejected save

    resp = app_client.put("/api/v1/settings", json=doc)
    assert resp.status_code == 422
    for secret in (*SECRETS, "attacker.example"):
        assert secret not in resp.text

    after = load_settings()
    assert after.model_dump() == before
    assert after.ui.language != "de"


@pytest.mark.parametrize("client_type, qb_kwargs, reentered", _RETARGET_CASES)
def test_download_client_retarget_succeeds_when_the_secret_is_re_entered(
    app_client, client_type, qb_kwargs, reentered
):
    _configure(qb_kwargs=qb_kwargs)
    doc, target = _client_doc_retargeted(app_client, client_type)
    target.update(reentered)

    put = app_client.put("/api/v1/settings", json=doc)
    assert put.status_code == 200
    for secret in (*reentered.values(), *SECRETS):
        assert secret not in put.text

    stored = {c.type: c for c in load_settings().download_clients}
    assert stored[client_type].url == _NEW_CLIENT_URL
    for field, value in reentered.items():
        assert getattr(stored[client_type], field) == value
    # The other client keeps both its destination and its stored secret.
    if client_type == "qbittorrent":
        assert stored["sabnzbd"].url == "http://sab.local" and stored["sabnzbd"].api_key == "sab-key"
    else:
        assert stored["qbittorrent"].url == QB_URL and stored["qbittorrent"].api_key == "qb-key-secret"


def test_download_client_retarget_requires_every_stored_secret_to_be_re_entered(app_client):
    """qBittorrent with both an API key and a legacy password: re-entering only
    one would still re-aim the other, so it is rejected until both are given."""
    _configure(qb_kwargs={"username": "alice", "password": "legacy-pw"})
    before = load_settings().model_dump()
    doc, target = _client_doc_retargeted(app_client, "qbittorrent")
    target["api_key"] = "new-qb-key"

    assert app_client.put("/api/v1/settings", json=doc).status_code == 422
    assert load_settings().model_dump() == before

    target["password"] = "new-pw"
    assert app_client.put("/api/v1/settings", json=doc).status_code == 200
    stored = next(c for c in load_settings().download_clients if c.type == "qbittorrent")
    assert (stored.url, stored.api_key, stored.password) == (_NEW_CLIENT_URL, "new-qb-key", "new-pw")


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
