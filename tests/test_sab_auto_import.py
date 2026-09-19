"""Auto-import completed SABnzbd downloads tests (issue #25).

Offline/stubbed: SABnzbdClient is replaced with a canned-history stub (no
network), the provider chain with the same StubProvider pattern used by
tests/test_import_scheduler.py and tests/test_importer.py (no second
scoring engine -- this exercises the real import pipeline).
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.db import get_conn, migrate
from app.models.settings import DownloadClient
from app.providers.base import (
    BaseMetadataProvider,
    BookDetailInfo,
    BookQuickInfo,
    BrowseResponse,
    SearchResponse,
)
from app.providers.chain import ProviderChain, ProviderChainConfig
from app.sab_auto_import import SabAutoImportScheduler


class StubProvider(BaseMetadataProvider):
    """Offline provider: resolves exactly one canned ASIN."""

    provider_uid = "stub:1"
    provider_name = "Stub"

    CANNED = BookQuickInfo(
        provider_uid="stub:1",
        provider_name="Stub",
        title="Der Vorleser",
        authors=["Bernhard Schlink"],
        narrators=["Hans Korte"],
        series="",
        asin="B004UWRY6M",
        locale="de",
    )

    async def search(self, query: str, **kwargs) -> SearchResponse:
        results = [self.CANNED] if "Vorleser" in query or query in self.CANNED.title else []
        return SearchResponse(results=results, query_used=query)

    async def get_detail(self, external_id: str, **kwargs) -> BookDetailInfo | None:
        if external_id == self.CANNED.asin:
            return BookDetailInfo(
                provider_uid=self.CANNED.provider_uid,
                provider_name=self.CANNED.provider_name,
                provider_external_id=external_id,
                title=self.CANNED.title,
                authors=self.CANNED.authors,
                narrators=self.CANNED.narrators,
                asin=self.CANNED.asin,
            )
        return None

    async def browse(self, **kwargs) -> BrowseResponse:
        return BrowseResponse(results=[self.CANNED])


class StubSabClient:
    """Drop-in SABnzbdClient replacement returning a canned history list."""

    HISTORY: list[dict] = []

    def __init__(self, *, base_url: str = "", api_key: str | None = None, client=None) -> None:
        pass

    async def history(self, limit: int = 100) -> list[dict]:
        return StubSabClient.HISTORY


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated DB: AUDIARR_CONFIG_DIR points to tmp; migrations run on it."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    migrate()
    with get_conn() as conn:
        yield conn


@pytest.fixture()
def stub_chain(monkeypatch):
    chain = ProviderChain(
        config=ProviderChainConfig(provider_order=["stub"]),
        provider_overrides={"stub": StubProvider()},
    )
    monkeypatch.setattr("app.sab_auto_import.build_provider_chain", lambda: chain)
    return chain


@pytest.fixture()
def enabled_sab(monkeypatch):
    """Stub _enabled_sabnzbd() so the scheduler thinks a client is configured."""
    client = DownloadClient(
        name="SABnzbd", type="sabnzbd", url="http://sab.local",
        category="audiobooks", enabled=True,
    )
    monkeypatch.setattr("app.sab_auto_import._enabled_sabnzbd", lambda: client)
    monkeypatch.setattr("app.sab_auto_import.SABnzbdClient", StubSabClient)
    return client


def _asin_book_folder(tmp_path: Path) -> Path:
    root = tmp_path / "downloads"
    book = root / "Bernhard Schlink - Der Vorleser [B004UWRY6M]"
    book.mkdir(parents=True)
    (book / "01 - Kapitel 1.mp3").write_bytes(b"\x00" * 1024)
    return book


def _history_item(folder: Path, **overrides) -> dict:
    item = {
        "nzo_id": "nzo_1",
        "name": folder.name,
        "status": "Completed",
        "size": "512 MB",
        "category": "audiobooks",
        "completed_at": 1725100000,
        "storage": str(folder),
        "fail_message": "",
    }
    item.update(overrides)
    return item


async def test_completed_item_in_category_imports_once(tmp_path, db, stub_chain, enabled_sab):
    """A completed history item in the configured category is imported via
    ASIN resolution (import_single_folder), and recorded so a second poll
    of the same history does not import it again."""
    folder = _asin_book_folder(tmp_path)
    StubSabClient.HISTORY = [_history_item(folder)]

    scheduler = SabAutoImportScheduler()
    ran = await scheduler.run_once()

    assert ran is True
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    state = db.execute("SELECT status FROM sab_import_state WHERE nzo_key = 'nzo_1'").fetchone()
    assert state["status"] == "imported"

    # Same history returned again -- the nzo_id is already recorded, so the
    # duplicate guard must skip it (no second book created).
    ran_again = await scheduler.run_once()
    assert ran_again is True
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM sab_import_state").fetchone()[0] == 1


async def test_non_completed_status_ignored(tmp_path, db, stub_chain, enabled_sab):
    folder = _asin_book_folder(tmp_path)
    StubSabClient.HISTORY = [_history_item(folder, status="Downloading")]

    scheduler = SabAutoImportScheduler()
    await scheduler.run_once()

    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM sab_import_state").fetchone()[0] == 0


async def test_wrong_category_ignored(tmp_path, db, stub_chain, enabled_sab):
    folder = _asin_book_folder(tmp_path)
    StubSabClient.HISTORY = [_history_item(folder, category="movies")]

    scheduler = SabAutoImportScheduler()
    await scheduler.run_once()

    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM sab_import_state").fetchone()[0] == 0


async def test_failed_import_records_reason(tmp_path, db, stub_chain, enabled_sab):
    """No ASIN hint and no configured root folder contains the path ->
    recorded as failed with a human-readable reason, visible for Activity."""
    root = tmp_path / "downloads"
    folder = root / "Some Unrecognizable Release Name"
    folder.mkdir(parents=True)
    (folder / "01.mp3").write_bytes(b"\x00" * 1024)

    StubSabClient.HISTORY = [_history_item(folder, nzo_id="nzo_2")]

    scheduler = SabAutoImportScheduler()
    await scheduler.run_once()

    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    state = db.execute(
        "SELECT status, reason FROM sab_import_state WHERE nzo_key = 'nzo_2'"
    ).fetchone()
    assert state["status"] == "failed"
    assert state["reason"]


async def test_no_storage_path_recorded_as_skipped(tmp_path, db, stub_chain, enabled_sab):
    StubSabClient.HISTORY = [_history_item(tmp_path, nzo_id="nzo_3", storage="")]

    scheduler = SabAutoImportScheduler()
    await scheduler.run_once()

    state = db.execute(
        "SELECT status FROM sab_import_state WHERE nzo_key = 'nzo_3'"
    ).fetchone()
    assert state["status"] == "skipped"


async def test_run_once_noop_without_enabled_sab_client(tmp_path, db, stub_chain, monkeypatch):
    monkeypatch.setattr("app.sab_auto_import._enabled_sabnzbd", lambda: None)

    scheduler = SabAutoImportScheduler()
    ran = await scheduler.run_once()

    assert ran is True  # run_once completed a (no-op) tick
    assert db.execute("SELECT COUNT(*) FROM sab_import_state").fetchone()[0] == 0


def test_lifespan_starts_worker_when_enabled_and_sab_client_present(tmp_path, monkeypatch):
    """app.main's lifespan wires up the poller only when both the setting
    and an enabled SABnzbd client are present (see app/main.py)."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    from app.config import load_settings, save_settings

    settings = load_settings()
    settings.media_management.sab_auto_import_enabled = True
    # Long interval + an unroutable port: the immediate first tick fails
    # fast (connection refused) instead of hanging or reaching the network.
    settings.media_management.sab_auto_import_interval_minutes = 60
    settings.download_clients = [
        DownloadClient(
            name="SABnzbd", type="sabnzbd", url="http://127.0.0.1:1",
            category="audiobooks", enabled=True,
        )
    ]
    save_settings(settings)

    from app import main as main_module

    importlib.reload(main_module)
    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as client:
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200


async def test_overlapping_tick_is_skipped(tmp_path, db, stub_chain, enabled_sab):
    import asyncio

    scheduler = SabAutoImportScheduler()
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
