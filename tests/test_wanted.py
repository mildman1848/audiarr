"""Wanted/Missing API tests: monitored books with zero library files, plus
cutoff-unmet tracking and upgrade search/grab for monitored books that
already have files below their quality profile's cutoff.
"""

from __future__ import annotations

import httpx

from app.config import load_settings, save_settings
from app.connections.prowlarr import ProwlarrClient
from app.connections.sabnzbd import SABnzbdClient
from app.db import get_conn
from app.models.settings import DownloadClient, Indexer, QualityProfile


def _create_book(app_client, **overrides) -> dict:
    payload = {
        "title": "Der Vorleser",
        "authors": ["Bernhard Schlink"],
        "language": "de",
        "provider": "audible",
        "provider_id": "B004UWRY6M",
        "locale": "de",
    }
    payload.update(overrides)
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    return resp.json()


def test_wanted_missing_empty_when_no_books(app_client):
    resp = app_client.get("/api/v1/wanted/missing")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/v1/wanted/cutoff
# ---------------------------------------------------------------------------


def _add_library_file(book_id: int, file_name: str) -> None:
    """Attach one edition + library file to a book (below/at-cutoff fixtures)."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, 'm4b', 'de')",
            (book_id,),
        )
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) "
            "VALUES (?, ?, ?, 'm4b')",
            (edition_id, f"/data/book/{file_name}", 1000),
        )


def _set_profile(**overrides) -> None:
    settings = load_settings()
    settings.quality_profiles = [QualityProfile(name="Standard", **overrides)]
    save_settings(settings)


def test_wanted_cutoff_empty_when_no_books(app_client):
    resp = app_client.get("/api/v1/wanted/cutoff")
    assert resp.status_code == 200
    assert resp.json() == []


def test_wanted_cutoff_includes_book_below_cutoff(app_client):
    _set_profile()  # default cutoff_quality_id="m4b-aac-128" (top tier)
    book = _create_book(app_client)
    _add_library_file(book["id"], "part1 MP3 320kbps.mp3")

    resp = app_client.get("/api/v1/wanted/cutoff")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["id"] == book["id"]
    assert row["title"] == "Der Vorleser"
    assert row["current_container"] == "mp3"
    assert row["current_bitrate_kbps"] == 320
    assert row["current_quality_name"] == "MP3 320 kbps"
    assert row["profile_name"] == "Standard"
    assert row["cutoff_name"] == "M4B AAC ~128 kbps"


def test_wanted_cutoff_excludes_book_at_or_above_cutoff(app_client):
    _set_profile()
    book = _create_book(app_client)
    _add_library_file(book["id"], "part1 M4B AAC 128kbps Chaptered.m4b")

    resp = app_client.get("/api/v1/wanted/cutoff")
    assert resp.status_code == 200
    assert resp.json() == []


def test_wanted_cutoff_excludes_when_upgrade_not_allowed(app_client):
    _set_profile(upgrade_allowed=False)
    book = _create_book(app_client)
    _add_library_file(book["id"], "part1 MP3 320kbps.mp3")

    resp = app_client.get("/api/v1/wanted/cutoff")
    assert resp.status_code == 200
    assert resp.json() == []


def test_wanted_cutoff_excludes_unmonitored_books(app_client):
    _set_profile()
    book = _create_book(app_client, monitored=False)
    _add_library_file(book["id"], "part1 MP3 320kbps.mp3")

    resp = app_client.get("/api/v1/wanted/cutoff")
    assert resp.status_code == 200
    assert resp.json() == []


def test_wanted_cutoff_excludes_books_without_files(app_client):
    _set_profile()
    _create_book(app_client)

    resp = app_client.get("/api/v1/wanted/cutoff")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/v1/wanted/cutoff/{book_id}/search
# ---------------------------------------------------------------------------


def _factory(real_cls, handler):
    """Drop-in replacement for a connection client, wired to a MockTransport."""

    def make(base_url: str = "", api_key: str | None = None, client=None):
        url = base_url or "http://mock.local"
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)
        return real_cls(base_url=url, api_key=api_key, client=mock)

    return make


def _configure_connections(*, prowlarr: bool = True, sabnzbd: bool = True) -> None:
    settings = load_settings()
    settings.indexers = (
        [
            Indexer(
                name="Prowlarr",
                type="prowlarr",
                url="http://prowlarr.local",
                api_key="p-key",
                enabled=True,
            )
        ]
        if prowlarr
        else []
    )
    settings.download_clients = (
        [
            DownloadClient(
                name="SABnzbd",
                type="sabnzbd",
                url="http://sab.local",
                api_key="sab-key",
                category="audiobooks",
                enabled=True,
            )
        ]
        if sabnzbd
        else []
    )
    save_settings(settings)


def test_cutoff_search_picks_best_fitting_release_and_grabs_it(app_client, monkeypatch):
    _set_profile()
    _configure_connections()
    book = _create_book(app_client)

    releases = [
        # Below cutoff (mp3-320) -- must not be picked even though it has
        # more seeders than the fitting release below.
        {
            "guid": "mp3-1",
            "indexerId": 4,
            "indexer": "Idx",
            "title": "Der Vorleser MP3 320kbps",
            "size": 100,
            "seeders": 50,
            "leechers": 0,
            "publishDate": "2026-09-01T12:00:00Z",
            "downloadUrl": "https://indexer.example/download/mp3-1.nzb",
            "magnetUrl": None,
            "protocol": "usenet",
            "age": 1,
            "categories": [],
        },
        # Fits the profile at the top tier (m4b-aac-128, "preferred").
        {
            "guid": "m4b-1",
            "indexerId": 4,
            "indexer": "Idx",
            "title": "Der Vorleser M4B AAC 128kbps Chaptered",
            "size": 200,
            "seeders": 5,
            "leechers": 0,
            "publishDate": "2026-09-01T12:00:00Z",
            "downloadUrl": "https://indexer.example/download/m4b-1.nzb",
            "magnetUrl": None,
            "protocol": "usenet",
            "age": 1,
            "categories": [],
        },
    ]

    def prowlarr_search_handler(request: httpx.Request) -> httpx.Response:
        assert "Der Vorleser" in request.url.params["query"]
        assert "Bernhard Schlink" in request.url.params["query"]
        return httpx.Response(200, json=releases)

    def prowlarr_download_handler(request: httpx.Request) -> httpx.Response:
        assert "m4b-1" in str(request.url)
        return httpx.Response(200, content=b"<nzb>payload</nzb>")

    def prowlarr_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/search":
            return prowlarr_search_handler(request)
        return prowlarr_download_handler(request)

    seen: dict[str, object] = {}

    def sab_handler(request: httpx.Request) -> httpx.Response:
        seen["has_title"] = b"Der Vorleser M4B AAC 128kbps Chaptered" in request.content
        return httpx.Response(200, json={"status": True, "nzo_ids": ["nzo_upgrade"]})

    monkeypatch.setattr(
        "app.api.routes_wanted.ProwlarrClient", _factory(ProwlarrClient, prowlarr_handler)
    )
    monkeypatch.setattr(
        "app.api.routes_wanted.SABnzbdClient", _factory(SABnzbdClient, sab_handler)
    )

    resp = app_client.post(f"/api/v1/wanted/cutoff/{book['id']}/search")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["found"] is True
    assert body["nzo_id"] == "nzo_upgrade"
    assert body["release_title"] == "Der Vorleser M4B AAC 128kbps Chaptered"
    assert seen == {"has_title": True}


def test_cutoff_search_returns_202_when_no_release_fits(app_client, monkeypatch):
    _set_profile()
    _configure_connections()
    book = _create_book(app_client)

    def prowlarr_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "guid": "unrecognized",
                    "indexerId": 4,
                    "indexer": "Idx",
                    "title": "Der Vorleser Unabridged",  # no container token
                    "size": 100,
                    "seeders": 5,
                    "leechers": 0,
                    "publishDate": "2026-09-01T12:00:00Z",
                    "downloadUrl": "https://indexer.example/download/unrecognized.nzb",
                    "magnetUrl": None,
                    "protocol": "usenet",
                    "age": 1,
                    "categories": [],
                }
            ],
        )

    def sab_handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("SABnzbd must not be called when no release fits")

    monkeypatch.setattr(
        "app.api.routes_wanted.ProwlarrClient", _factory(ProwlarrClient, prowlarr_handler)
    )
    monkeypatch.setattr(
        "app.api.routes_wanted.SABnzbdClient", _factory(SABnzbdClient, sab_handler)
    )

    resp = app_client.post(f"/api/v1/wanted/cutoff/{book['id']}/search")
    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is False
    assert body["found"] is False
    assert body["reason"]


def test_cutoff_search_404_for_unknown_book(app_client):
    resp = app_client.post("/api/v1/wanted/cutoff/999999/search")
    assert resp.status_code == 404


def test_cutoff_search_503_without_prowlarr(app_client):
    _configure_connections(prowlarr=False)
    book = _create_book(app_client)

    resp = app_client.post(f"/api/v1/wanted/cutoff/{book['id']}/search")
    assert resp.status_code == 503


def test_wanted_missing_includes_monitored_book_without_files(app_client):
    book = _create_book(app_client)

    resp = app_client.get("/api/v1/wanted/missing")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["id"] == book["id"]
    assert row["title"] == "Der Vorleser"
    assert row["authors"] == ["Bernhard Schlink"]
    assert row["monitored"] is True
    assert row["reason"] == "missingFiles"


def test_wanted_missing_excludes_unmonitored_books(app_client):
    _create_book(app_client, monitored=False)

    resp = app_client.get("/api/v1/wanted/missing")
    assert resp.status_code == 200
    assert resp.json() == []


def test_wanted_missing_excludes_books_with_library_files(app_client):
    book = _create_book(app_client)

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, 'm4b', 'de')",
            (book["id"],),
        )
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) "
            "VALUES (?, ?, ?, 'm4b')",
            (edition_id, "/data/book/part1.m4b", 1000),
        )

    resp = app_client.get("/api/v1/wanted/missing")
    assert resp.status_code == 200
    assert resp.json() == []
