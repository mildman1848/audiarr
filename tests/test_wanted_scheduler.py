"""Periodic wanted-search scheduler tests (issue #26).

Offline/stubbed: ProwlarrClient/SABnzbdClient are replaced with
MockTransport-backed factories (same pattern as tests/test_wanted.py),
reusing the real find_fitting_release/grab_cutoff_release/
list_cutoff_candidates helpers from app.api.routes_wanted -- no second
scoring engine.
"""

from __future__ import annotations

import asyncio
import importlib

import httpx
import pytest

from app.config import load_settings, save_settings
from app.connections.prowlarr import ProwlarrClient
from app.connections.sabnzbd import SABnzbdClient
from app.db import get_conn, migrate
from app.models.settings import DownloadClient, Indexer, QualityProfile
from app.wanted_scheduler import WantedSearchScheduler


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated DB: AUDIARR_CONFIG_DIR points to tmp; migrations run on it."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    migrate()
    with get_conn() as conn:
        yield conn


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


def _set_profile(**overrides) -> None:
    settings = load_settings()
    settings.quality_profiles = [QualityProfile(name="Standard", **overrides)]
    save_settings(settings)


def _create_cutoff_candidate(conn, title: str = "Der Vorleser") -> int:
    """A monitored book with one below-cutoff (mp3) file."""
    cur = conn.execute("INSERT INTO books (title) VALUES (?)", (title,))
    book_id = cur.lastrowid
    author_cur = conn.execute(
        "INSERT INTO authors (name) VALUES (?)", ("Bernhard Schlink",)
    )
    conn.execute(
        "INSERT INTO book_authors (book_id, author_id, position) VALUES (?, ?, 0)",
        (book_id, author_cur.lastrowid),
    )
    edition_cur = conn.execute(
        "INSERT INTO editions (book_id, format, locale) VALUES (?, 'mp3', 'de')", (book_id,)
    )
    edition_id = edition_cur.lastrowid
    conn.execute(
        "INSERT INTO library_files (edition_id, path, size_bytes, format) VALUES (?, ?, ?, 'mp3')",
        (edition_id, f"/data/book/{title} part1 MP3 320kbps.mp3", 1000),
    )
    conn.commit()
    return book_id


def _factory(real_cls, handler):
    def make(base_url: str = "", api_key: str | None = None, client=None):
        url = base_url or "http://mock.local"
        mock = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)
        return real_cls(base_url=url, api_key=api_key, client=mock)

    return make


_FITTING_RELEASE = {
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
}

_UNFITTING_RELEASE = {
    "guid": "unrecognized",
    "indexerId": 4,
    "indexer": "Idx",
    "title": "Der Vorleser Unabridged",
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


async def test_scheduler_loop_noop_when_interval_disabled(db, monkeypatch):
    """interval_minutes <= 0 must never invoke run_once (0 = off, the default)."""
    from app.import_scheduler import scheduler_loop

    scheduler = WantedSearchScheduler()
    calls = []

    async def fake_run_once():
        calls.append(True)
        return True

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)

    await scheduler_loop(scheduler, 0, asyncio.Event())

    assert calls == []


async def test_run_once_noop_without_enabled_prowlarr(db, monkeypatch):
    _set_profile()
    _configure_connections(prowlarr=False)
    _create_cutoff_candidate(db)

    scheduler = WantedSearchScheduler()
    ran = await scheduler.run_once()

    assert ran is True
    assert db.execute("SELECT COUNT(*) FROM wanted_search_state").fetchone()[0] == 0
    assert load_settings().wanted.last_scheduled_search_at == ""


async def test_run_once_grabs_fitting_release_and_records_state(db, monkeypatch):
    """A tick reuses find_fitting_release/grab_cutoff_release for each
    candidate, grabs the fitting release, and records grabbed state +
    last-run counts."""
    _set_profile()
    _configure_connections()
    book_id = _create_cutoff_candidate(db)

    search_calls = []

    def prowlarr_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/search":
            search_calls.append(1)
            return httpx.Response(200, json=[_FITTING_RELEASE])
        return httpx.Response(200, content=b"<nzb>payload</nzb>")

    def sab_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": True, "nzo_ids": ["nzo_wanted"]})

    monkeypatch.setattr(
        "app.api.routes_wanted.ProwlarrClient", _factory(ProwlarrClient, prowlarr_handler)
    )
    monkeypatch.setattr(
        "app.api.routes_wanted.SABnzbdClient", _factory(SABnzbdClient, sab_handler)
    )

    scheduler = WantedSearchScheduler()
    ran = await scheduler.run_once()

    assert ran is True
    assert len(search_calls) == 1
    state = db.execute(
        "SELECT status, nzo_id FROM wanted_search_state WHERE book_id = ?", (book_id,)
    ).fetchone()
    assert state["status"] == "grabbed"
    assert state["nzo_id"] == "nzo_wanted"

    settings = load_settings()
    assert settings.wanted.last_scheduled_search_at != ""
    assert settings.wanted.last_search_grabbed == 1
    assert settings.wanted.last_search_no_release == 0
    assert settings.wanted.last_search_skipped == 0

    # A second tick: the book is still below cutoff (no import happened),
    # but it must be skipped -- not re-grabbed -- because it is already
    # marked "grabbed" awaiting import.
    ran_again = await scheduler.run_once()
    assert ran_again is True
    assert len(search_calls) == 1  # no second Prowlarr search call
    assert load_settings().wanted.last_search_skipped == 1
    assert load_settings().wanted.last_search_grabbed == 0


async def test_run_once_no_fitting_release_retries_next_tick(db, monkeypatch):
    """When no release fits, the candidate is retried on the next tick (not
    permanently skipped) -- periodic re-search is the point."""
    _set_profile()
    _configure_connections()
    book_id = _create_cutoff_candidate(db)

    search_calls = []

    def prowlarr_handler(request: httpx.Request) -> httpx.Response:
        search_calls.append(1)
        return httpx.Response(200, json=[_UNFITTING_RELEASE])

    def sab_handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("SABnzbd must not be called when no release fits")

    monkeypatch.setattr(
        "app.api.routes_wanted.ProwlarrClient", _factory(ProwlarrClient, prowlarr_handler)
    )
    monkeypatch.setattr(
        "app.api.routes_wanted.SABnzbdClient", _factory(SABnzbdClient, sab_handler)
    )

    scheduler = WantedSearchScheduler()
    await scheduler.run_once()
    await scheduler.run_once()

    assert len(search_calls) == 2
    state = db.execute(
        "SELECT status FROM wanted_search_state WHERE book_id = ?", (book_id,)
    ).fetchone()
    assert state["status"] == "no_release"
    assert load_settings().wanted.last_search_no_release == 1


async def test_stale_state_is_pruned_when_book_no_longer_a_candidate(db, monkeypatch):
    """A book that upgrades out of the candidate list (or stops being
    monitored) has its state row pruned, not left stale forever."""
    _set_profile()
    _configure_connections()
    book_id = _create_cutoff_candidate(db)

    db.execute(
        "INSERT INTO wanted_search_state (book_id, status, nzo_id) VALUES (?, 'grabbed', 'nzo_x')",
        (book_id,),
    )
    db.commit()

    db.execute("UPDATE books SET monitored = 0 WHERE id = ?", (book_id,))
    db.commit()

    scheduler = WantedSearchScheduler()
    await scheduler.run_once()

    assert db.execute("SELECT COUNT(*) FROM wanted_search_state").fetchone()[0] == 0


async def test_overlapping_tick_is_skipped(db, monkeypatch):
    _set_profile()
    _configure_connections()
    _create_cutoff_candidate(db)

    scheduler = WantedSearchScheduler()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_process_all():
        started.set()
        await release.wait()

    scheduler._process_all = slow_process_all  # type: ignore[method-assign]

    first = asyncio.create_task(scheduler.run_once())
    await started.wait()

    skipped = await scheduler.run_once()
    assert skipped is False

    release.set()
    assert await first is True
