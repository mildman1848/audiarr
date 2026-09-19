"""Periodic metadata refresh scheduler tests (issue #26)."""

from __future__ import annotations

import asyncio
import importlib

import pytest

from app.config import load_settings
from app.db import get_conn, migrate
from app.import_scheduler import scheduler_loop
from app.metadata_scheduler import MetadataRefreshScheduler
from app.providers.base import (
    BaseMetadataProvider,
    BookDetailInfo,
    BookQuickInfo,
    BrowseResponse,
    SearchResponse,
)
from app.providers.chain import ProviderChain, ProviderChainConfig


class StubProvider(BaseMetadataProvider):
    """Offline provider: returns a canned hit for keyword search."""

    provider_uid = "stub:1"
    provider_name = "stub"

    CANNED = BookQuickInfo(
        provider_uid="stub:1",
        provider_name="stub",
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
                release_date="2009-01-01",
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
    monkeypatch.setattr("app.metadata_scheduler.build_provider_chain", lambda: chain)
    return chain


def _insert_book(conn, title: str) -> int:
    cur = conn.execute("INSERT INTO books (title) VALUES (?)", (title,))
    return cur.lastrowid


async def test_scheduler_loop_noop_when_interval_disabled(db, monkeypatch):
    """interval_minutes <= 0 must never invoke run_once (0 = off, the default)."""
    scheduler = MetadataRefreshScheduler()
    calls = []

    async def fake_run_once():
        calls.append(True)
        return True

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)

    await scheduler_loop(scheduler, 0, asyncio.Event())

    assert calls == []


async def test_run_once_updates_book_and_records_last_run(db, stub_chain):
    """A tick calls run_backfill_batch(build_provider_chain()), persists the
    matched asin/release_date, and records the last-run timestamp + counts."""
    _insert_book(db, "Der Vorleser")
    db.commit()

    assert load_settings().metadata.last_scheduled_refresh_at == ""

    scheduler = MetadataRefreshScheduler()
    ran = await scheduler.run_once()

    assert ran is True
    row = db.execute("SELECT asin, release_date FROM books WHERE title = 'Der Vorleser'").fetchone()
    assert row["asin"] == "B004UWRY6M"
    assert row["release_date"] == "2009-01-01"

    settings = load_settings()
    assert settings.metadata.last_scheduled_refresh_at != ""
    assert settings.metadata.last_refresh_updated == 1
    assert settings.metadata.last_refresh_failed == 0
    assert settings.metadata.last_refresh_remaining == 0


async def test_run_once_respects_configured_batch_size(db, stub_chain, monkeypatch):
    """The scheduler passes settings.metadata.refresh_batch_size through to
    run_backfill_batch instead of the module default."""
    for i in range(3):
        _insert_book(db, f"Unmatched Book {i}")
    db.commit()

    from app.config import save_settings

    settings = load_settings()
    settings.metadata.refresh_batch_size = 1
    save_settings(settings)

    seen_batch_sizes = []
    real_run_backfill_batch = __import__(
        "app.metadata.backfill", fromlist=["run_backfill_batch"]
    ).run_backfill_batch

    async def spy(chain, batch_size=10):
        seen_batch_sizes.append(batch_size)
        return await real_run_backfill_batch(chain, batch_size)

    monkeypatch.setattr("app.metadata_scheduler.run_backfill_batch", spy)

    scheduler = MetadataRefreshScheduler()
    await scheduler.run_once()

    assert seen_batch_sizes == [1]


async def test_overlapping_tick_is_skipped(db, stub_chain, caplog):
    """A tick that starts while a previous run_once() is still in flight
    must be skipped (single-flight), not queued or run in parallel."""
    scheduler = MetadataRefreshScheduler()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_refresh_once():
        started.set()
        await release.wait()

    scheduler._refresh_once = slow_refresh_once  # type: ignore[method-assign]

    first = asyncio.create_task(scheduler.run_once())
    await started.wait()

    with caplog.at_level("DEBUG", logger="audiarr.metadata_scheduler"):
        skipped = await scheduler.run_once()

    assert skipped is False
    assert "skipped" in caplog.text

    release.set()
    assert await first is True
