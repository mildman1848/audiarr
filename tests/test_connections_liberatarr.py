"""Client-level tests for the Liberatarr connection (issue #64).

Mirrors tests/test_connections_sabnzbd.py: httpx.MockTransport, no network
access, and explicit assertions on what the client sent.

Row shapes: the real Liberatarr API (``_row_to_book`` in its ``ldb.py``)
wraps rows as ``{"books": [...]}`` and reports ``product_id`` (str ASIN),
``language`` (str), and an integer ``status`` enum (0=Not Liberated,
1=Liberated, 2=Error, 0x1000=Partial) alongside a human-readable
``status_text``. A defensive/legacy shape (bare list, ``Asin``/``Locale``/
``Status`` fields) must still be tolerated in case of drift.
"""

from __future__ import annotations

import httpx
import pytest

from app.connections.liberatarr import (
    LiberatarrClient,
    LiberatarrError,
    fetch_library,
    is_not_liberated,
)
from app.connections.liberatarr import test_connection as liberatarr_test_connection
from app.models.settings import LiberatarrSettings


@pytest.mark.asyncio
async def test_health_ok_sends_bearer_header_when_token_set():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.headers["Authorization"] == "Bearer secret-token"
        return httpx.Response(200, json={"status": "ok"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", token="secret-token", client=mock_client)

    result = await client.health()
    assert result == {"ok": True, "message": "Liberatarr is reachable"}
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_health_omits_auth_header_when_token_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"status": "ok"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    result = await client.health()
    assert result["ok"] is True
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_health_false_on_non_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    result = await client.health()
    assert result["ok"] is False
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_health_false_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    result = await client.health()
    assert result["ok"] is False
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_library_normalizes_real_upstream_shape():
    """Real Liberatarr shape: {"books": [...]}, product_id, language, int status."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/library"
        return httpx.Response(
            200,
            json={
                "books": [
                    {
                        "product_id": "B001XYZ",
                        "title": "Book One",
                        "content_type": 1,
                        "authors": ["Author A", "Author B"],
                        "narrators": ["Narrator A"],
                        "minutes": 642,
                        "hours": 10.7,
                        "picture_id": "pid",
                        "cover_url": "https://m.media-amazon.com/images/I/pid.jpg",
                        "published": "2020-01-01",
                        "language": "German",
                        "status": 0,
                        "status_text": "Not Liberated",
                        "pdf_status": None,
                        "tags": [],
                        "rating": None,
                        "account": "acct",
                        "added": "2020-02-01",
                    },
                    {
                        "product_id": "B002ABC",
                        "title": "Book Two",
                        "authors": ["Solo Author"],
                        "narrators": [],
                        "language": "English",
                        "status": 1,
                        "status_text": "Liberated",
                    },
                    {
                        "product_id": "B003MISSING",
                        "title": "Missing Fields Book",
                    },
                ]
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    rows = await client.library()
    await mock_client.aclose()

    assert rows[0] == {
        "asin": "B001XYZ",
        "title": "Book One",
        "authors": ["Author A", "Author B"],
        "narrators": ["Narrator A"],
        "status": 0,
        "locale": "German",
    }
    assert rows[1] == {
        "asin": "B002ABC",
        "title": "Book Two",
        "authors": ["Solo Author"],
        "narrators": [],
        "status": 1,
        "locale": "English",
    }
    assert rows[2] == {
        "asin": "B003MISSING",
        "title": "Missing Fields Book",
        "authors": [],
        "narrators": [],
        "status": "",
        "locale": "",
    }


@pytest.mark.asyncio
async def test_library_normalizes_defensive_bare_list_shape():
    """Defensive/legacy shape: bare list, Asin/Locale/Status fields."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/library"
        return httpx.Response(
            200,
            json=[
                {
                    "Asin": "B001",
                    "Title": "Book One",
                    "Authors": [{"Name": "Author A"}, {"Name": "Author B"}],
                    "Narrators": [{"Name": "Narrator A"}],
                    "Status": "NotLiberated",
                    "Locale": "us",
                },
                {
                    "asin": "b002",
                    "title": "Book Two",
                    "authors": ["Lowercase Author"],
                    "narrators": "Solo Narrator String",
                    "status": "Liberated",
                },
            ],
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    rows = await client.library()
    await mock_client.aclose()

    assert rows[0] == {
        "asin": "B001",
        "title": "Book One",
        "authors": ["Author A", "Author B"],
        "narrators": ["Narrator A"],
        "status": "NotLiberated",
        "locale": "us",
    }
    assert rows[1] == {
        "asin": "b002",
        "title": "Book Two",
        "authors": ["Lowercase Author"],
        "narrators": ["Solo Narrator String"],
        "status": "Liberated",
        "locale": "",
    }


@pytest.mark.asyncio
async def test_library_raises_liberatarr_error_on_upstream_500():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    with pytest.raises(LiberatarrError):
        await client.library()
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_library_raises_liberatarr_error_on_transport_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    with pytest.raises(LiberatarrError):
        await client.library()
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_library_raises_liberatarr_error_on_unexpected_json_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a list"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    with pytest.raises(LiberatarrError):
        await client.library()
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_library_raises_liberatarr_error_when_books_key_is_not_a_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"books": "oops"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    client = LiberatarrClient(base_url="http://lib.local", client=mock_client)

    with pytest.raises(LiberatarrError):
        await client.library()
    await mock_client.aclose()


@pytest.mark.asyncio
async def test_module_level_test_connection_and_fetch_library_use_settings():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(
            200,
            json={"books": [{"product_id": "B999", "title": "T", "status": 0, "language": "English"}]},
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://lib.local")
    settings = LiberatarrSettings(base_url="http://lib.local", token="x", enabled=True)

    result = await liberatarr_test_connection(settings, client=mock_client)
    assert result["ok"] is True

    rows = await fetch_library(settings, client=mock_client)
    assert rows[0]["asin"] == "B999"
    await mock_client.aclose()


@pytest.mark.parametrize(
    "status,expected",
    [
        (0, True),
        (1, False),
        (2, False),
        (0x1000, False),
        ("0", True),
        ("1", False),
        ("NotLiberated", True),
        ("notliberated", True),
        ("Not Liberated", True),
        ("not_liberated", True),
        ("Liberated", False),
        ("liberated", False),
        ("Error", False),
        ("Partial", False),
        ("", False),
        (None, False),
    ],
)
def test_is_not_liberated(status, expected):
    assert is_not_liberated(status) is expected
