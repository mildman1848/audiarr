"""Periodic root-folder import scheduler tests (issue #24)."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from app.config import load_settings
from app.db import get_conn, migrate
from app.import_scheduler import ImportScheduler, scheduler_loop
from app.providers.base import (
    BaseMetadataProvider,
    BookDetailInfo,
    BookQuickInfo,
    BrowseResponse,
    SearchResponse,
)
from app.providers.chain import ProviderChain, ProviderChainConfig


class StubProvider(BaseMetadataProvider):
    """Offline provider: returns a canned hit for ASIN and keyword search."""

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
    """Patch the scheduler's provider-chain builder to an offline stub chain."""
    chain = ProviderChain(
        config=ProviderChainConfig(provider_order=["stub"]),
        provider_overrides={"stub": StubProvider()},
    )
    monkeypatch.setattr("app.import_scheduler.build_provider_chain", lambda: chain)
    return chain


def _make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "audiobooks"
    book = root / "Bernhard Schlink - Der Vorleser [B004UWRY6M]"
    book.mkdir(parents=True)
    (book / "01 - Kapitel 1.mp3").write_bytes(b"\x00" * 1024)
    return root


async def test_scheduler_loop_noop_when_interval_disabled(db, monkeypatch):
    """interval_minutes <= 0 must never invoke run_once (0 = off, the default)."""
    scheduler = ImportScheduler()
    calls = []

    async def fake_run_once():
        calls.append(True)
        return True

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)

    await scheduler_loop(scheduler, 0, asyncio.Event())

    assert calls == []


async def test_run_once_scans_all_db_root_folders(tmp_path, db, stub_chain):
    """A tick imports every root folder configured in the DB (not
    settings.root_folders) and records the last-scan timestamp."""
    root = _make_tree(tmp_path)
    db.execute("INSERT INTO root_folders (path) VALUES (?)", (str(root),))
    db.commit()

    assert load_settings().media_management.last_scheduled_scan_at == ""

    scheduler = ImportScheduler()
    ran = await scheduler.run_once()

    assert ran is True
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    assert load_settings().media_management.last_scheduled_scan_at != ""


async def test_overlapping_tick_is_skipped(db, stub_chain, caplog):
    """A tick that starts while a previous run_once() is still in flight
    must be skipped (single-flight), not queued or run in parallel."""
    scheduler = ImportScheduler()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_scan_all():
        started.set()
        await release.wait()

    scheduler._scan_all = slow_scan_all  # type: ignore[method-assign]

    first = asyncio.create_task(scheduler.run_once())
    await started.wait()

    with caplog.at_level("DEBUG", logger="audiarr.import_scheduler"):
        skipped = await scheduler.run_once()

    assert skipped is False
    assert "skipped" in caplog.text

    release.set()
    assert await first is True
