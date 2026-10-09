"""qBittorrent client tests (issue #81): MockTransport only, no live client."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.connections.qbittorrent import (
    QBittorrentClient,
    QBittorrentError,
    magnet_info_hash,
    normalize_magnet,
)

HASH_A = "a" * 40
HASH_B = "b" * 40
BASE = "http://qb.local:8080"


def _client(handler, **kwargs) -> tuple[QBittorrentClient, httpx.AsyncClient]:
    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE)
    return QBittorrentClient(base_url=BASE, client=mock, **kwargs), mock


def _torrent(**overrides) -> dict:
    item = {
        "hash": HASH_A,
        "name": "Book",
        "category": "audiobooks",
        "tags": "audiarr, other",
        "state": "stalledUP",
        "progress": 1,
        "save_path": "/downloads",
        "content_path": "/downloads/Book",
    }
    item.update(overrides)
    return item


# --------------------------------------------------------------- API-key auth


async def test_api_key_mode_sends_bearer_and_never_logs_in():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="v5.2.0")

    client, mock = _client(handler, api_key="k" * 32, username="u", password="p")
    assert await client.version() == "v5.2.0"
    await mock.aclose()

    assert [r.url.path for r in seen] == ["/api/v2/app/version"]
    assert seen[0].headers["authorization"] == "Bearer " + "k" * 32
    assert "cookie" not in seen[0].headers


@pytest.mark.parametrize("status", [401, 403])
async def test_api_key_auth_failure_fails_closed_without_legacy_fallback(status):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(status)

    client, mock = _client(handler, api_key="bad-key", username="u", password="p")
    with pytest.raises(QBittorrentError) as exc:
        await client.version()
    await mock.aclose()

    assert exc.value.reason == "auth"
    assert seen == ["/api/v2/app/version"]  # never tried /auth/login
    assert "bad-key" not in str(exc.value)


async def test_no_credentials_fails_closed_without_any_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request expected")

    client, mock = _client(handler)
    with pytest.raises(QBittorrentError) as exc:
        await client.version()
    await mock.aclose()
    assert exc.value.reason == "not_configured"


# ------------------------------------------------------------- legacy login


def _legacy_handler(log: list[httpx.Request], *, login_text="Ok.", expire_once=False):
    state = {"expired": False}

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        if request.url.path == "/api/v2/auth/login":
            body = request.content.decode()
            assert "username=user" in body and "password=pw" in body
            assert request.headers["referer"] == BASE
            headers = {"set-cookie": "SID=sid-123; HttpOnly; path=/"} if login_text == "Ok." else {}
            return httpx.Response(200, text=login_text, headers=headers)
        if expire_once and not state["expired"]:
            state["expired"] = True
            return httpx.Response(403)
        assert request.headers["cookie"].startswith("SID=sid-")
        assert "authorization" not in request.headers
        return httpx.Response(200, text="v4.6.7")

    return handler


async def test_legacy_login_then_cookie_sent():
    log: list[httpx.Request] = []
    client, mock = _client(_legacy_handler(log), username="user", password="pw")
    assert await client.version() == "v4.6.7"
    assert await client.version() == "v4.6.7"
    await mock.aclose()
    # One login, two authenticated calls reusing the session.
    assert [r.url.path for r in log].count("/api/v2/auth/login") == 1
    assert log[1].headers["cookie"] == "SID=sid-123"


async def test_legacy_wrong_credentials_200_fails_is_auth_error():
    log: list[httpx.Request] = []
    client, mock = _client(_legacy_handler(log, login_text="Fails."), username="user", password="pw")
    with pytest.raises(QBittorrentError) as exc:
        await client.version()
    await mock.aclose()
    assert exc.value.reason == "auth"
    assert [r.url.path for r in log] == ["/api/v2/auth/login"]


async def test_legacy_login_403_banned_is_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="banned")

    client, mock = _client(handler, username="user", password="pw")
    with pytest.raises(QBittorrentError) as exc:
        await client.version()
    await mock.aclose()
    assert exc.value.reason == "auth"


async def test_legacy_relogs_once_when_session_expired():
    log: list[httpx.Request] = []
    client, mock = _client(_legacy_handler(log, expire_once=True), username="user", password="pw")
    assert await client.version() == "v4.6.7"
    await mock.aclose()
    assert [r.url.path for r in log].count("/api/v2/auth/login") == 2


# --------------------------------------------------------------- submission


async def test_add_url_payload_has_category_tag_and_no_savepath():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["body"] = request.content.decode()
        return httpx.Response(200, text="Ok.")

    client, mock = _client(handler, api_key="key")
    magnet = f"magnet:?xt=urn:btih:{HASH_A}&dn=Book"
    await client.add_url(magnet, "audiobooks", "audiarr")
    await mock.aclose()

    assert captured["method"] == "POST" and captured["path"] == "/api/v2/torrents/add"
    form = httpx.QueryParams(captured["body"])
    assert form["urls"] == magnet
    assert form["category"] == "audiobooks"
    assert form["tags"] == "audiarr"
    assert set(form.keys()) == {"urls", "category", "tags"}
    assert "savepath" not in captured["body"].lower()


async def test_add_url_fails_text_is_rejected_without_leaking_url():
    secret_url = "http://prowlarr.local/1/download?apikey=SECRETKEY&link=x"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Fails.")

    client, mock = _client(handler, api_key="key")
    with pytest.raises(QBittorrentError) as exc:
        await client.add_url(secret_url, "audiobooks", "audiarr")
    await mock.aclose()
    assert exc.value.reason == "rejected"
    assert "SECRETKEY" not in str(exc.value)


async def test_add_url_accepts_json_count_summary():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success_count": 0, "pending_count": 1, "failure_count": 0})

    client, mock = _client(handler, api_key="key")
    await client.add_url(f"magnet:?xt=urn:btih:{HASH_A}", "c", "t")
    await mock.aclose()


async def test_add_url_json_failure_count_is_rejected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success_count": 0, "pending_count": 0, "failure_count": 1})

    client, mock = _client(handler, api_key="key")
    with pytest.raises(QBittorrentError):
        await client.add_url(f"magnet:?xt=urn:btih:{HASH_A}", "c", "t")
    await mock.aclose()


# ------------------------------------------------------------ safe failures


async def test_timeout_is_mapped_and_does_not_leak_secrets(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    caplog.set_level(logging.DEBUG)
    client, mock = _client(handler, api_key="topsecretkey")
    with pytest.raises(QBittorrentError) as exc:
        await client.version()
    await mock.aclose()
    assert exc.value.reason == "timeout"
    assert "topsecretkey" not in caplog.text


async def test_connection_error_is_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client, mock = _client(handler, api_key="key")
    with pytest.raises(QBittorrentError) as exc:
        await client.version()
    await mock.aclose()
    assert exc.value.reason == "unreachable"


async def test_server_error_and_redirect_are_http_errors():
    for status in (500, 302):
        client, mock = _client(lambda r, s=status: httpx.Response(s), api_key="key")
        with pytest.raises(QBittorrentError) as exc:
            await client.version()
        await mock.aclose()
        assert exc.value.reason == "http_error"


@pytest.mark.parametrize("body", ["", "<html>login</html>", "x" * 200])
async def test_malformed_version_body(body):
    client, mock = _client(lambda r: httpx.Response(200, text=body), api_key="key")
    with pytest.raises(QBittorrentError) as exc:
        await client.version()
    await mock.aclose()
    assert exc.value.reason == "malformed"


@pytest.mark.parametrize("body", [b"not json", b'{"hash": "x"}', b"null"])
async def test_list_completed_malformed_json(body):
    client, mock = _client(lambda r: httpx.Response(200, content=body), api_key="key")
    with pytest.raises(QBittorrentError) as exc:
        await client.list_completed("audiobooks", "audiarr")
    await mock.aclose()
    assert exc.value.reason == "malformed"


async def test_invalid_base_url_is_contained():
    client = QBittorrentClient(base_url="not a url", api_key="key")
    with pytest.raises(QBittorrentError):
        await client.version()


# ------------------------------------------------------------ completed list


async def test_list_completed_uses_filter_category_and_tag():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        captured["path"] = request.url.path
        return httpx.Response(200, json=[_torrent()])

    client, mock = _client(handler, api_key="key")
    items = await client.list_completed("audiobooks", "audiarr")
    await mock.aclose()

    assert captured == {
        "path": "/api/v2/torrents/info",
        "filter": "completed",
        "category": "audiobooks",
        "tag": "audiarr",
    }
    assert len(items) == 1
    assert items[0]["hash"] == HASH_A
    assert items[0]["tags"] == ["audiarr", "other"]
    assert items[0]["content_path"] == "/downloads/Book"


async def test_list_completed_rechecks_category_tag_and_hash_client_side():
    """A server ignoring the query params must not widen the import set;
    all documented completed/seeding/paused states are tolerated."""
    payload = [
        _torrent(hash=HASH_A, state="uploading"),
        _torrent(hash=HASH_B.upper(), state="pausedUP"),
        _torrent(hash="c" * 40, category="movies"),
        _torrent(hash="d" * 40, tags="other"),
        _torrent(hash="e" * 40, progress=0.5),
        _torrent(hash="not-a-hash"),
        "garbage",
        {"name": "no hash"},
    ]
    client, mock = _client(lambda r: httpx.Response(200, json=payload), api_key="key")
    items = await client.list_completed("audiobooks", "audiarr")
    await mock.aclose()
    assert [i["hash"] for i in items] == [HASH_A, HASH_B]


# ------------------------------------------------------------------ magnets


def test_normalize_magnet_and_hash():
    magnet = f"magnet:?xt=urn:btih:{HASH_A.upper()}&dn=x"
    assert normalize_magnet(f"  {magnet}  ") == magnet
    assert magnet_info_hash(magnet) == HASH_A
    # base32 form of 20 zero bytes
    assert magnet_info_hash("magnet:?xt=urn:btih:" + "A" * 32) == "00" * 20


@pytest.mark.parametrize(
    "bad",
    [None, "", "http://x/y", "magnet:?dn=nohash", "magnet:?xt=urn:btih:short",
     "magnet:?xt=urn:btih:" + "a" * 40 + "&dn=a b", "magnet:?xt=urn:btih:" + "a" * 40 + "x" * 9000],
)
def test_normalize_magnet_rejects_invalid(bad):
    assert normalize_magnet(bad) is None
