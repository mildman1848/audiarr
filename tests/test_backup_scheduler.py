"""Periodic config-backup scheduler tests (issue #32, phase 5)."""

from __future__ import annotations

import asyncio

from app.backup_scheduler import BackupScheduler
from app.backup_service import BackupError
from app.import_scheduler import scheduler_loop


async def test_run_once_delegates_to_thread(monkeypatch):
    calls = []

    def fake_create_backup(reason):
        calls.append(reason)
        return {"path": "/tmp/x.zip", "size_bytes": 1}

    monkeypatch.setattr("app.backup_scheduler.create_backup", fake_create_backup)

    scheduler = BackupScheduler()
    ran = await scheduler.run_once()

    assert ran is True
    assert calls == ["scheduled"]


async def test_run_once_warns_on_backup_error(monkeypatch, caplog):
    def failing_create_backup(reason):
        raise BackupError("boom")

    monkeypatch.setattr("app.backup_scheduler.create_backup", failing_create_backup)

    scheduler = BackupScheduler()
    with caplog.at_level("WARNING", logger="audiarr.backup_scheduler"):
        ran = await scheduler.run_once()

    assert ran is True  # the tick itself completed; the backup attempt failed internally
    assert "failed" in caplog.text


async def test_overlapping_tick_is_skipped(caplog):
    """A tick that starts while a previous run_once() is still in flight
    must be skipped (single-flight), not queued or run in parallel."""
    scheduler = BackupScheduler()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_backup_once():
        started.set()
        await release.wait()

    scheduler._backup_once = slow_backup_once  # type: ignore[method-assign]

    first = asyncio.create_task(scheduler.run_once())
    await started.wait()

    with caplog.at_level("DEBUG", logger="audiarr.backup_scheduler"):
        skipped = await scheduler.run_once()

    assert skipped is False
    assert "skipped" in caplog.text

    release.set()
    assert await first is True


async def test_scheduler_loop_delay_first_waits_before_first_run():
    """delay_first=True must not run a tick until one full interval has elapsed."""
    scheduler = BackupScheduler()
    calls = []

    async def fake_run_once():
        calls.append(True)
        return True

    scheduler.run_once = fake_run_once  # type: ignore[method-assign]

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        scheduler_loop(scheduler, interval_minutes=1, stop_event=stop_event, delay_first=True)
    )
    await asyncio.sleep(0.05)
    assert calls == []  # still well within the 60s delay window

    stop_event.set()
    await asyncio.wait_for(task, timeout=5)
    assert calls == []  # stopped before the delayed first tick ever fired


async def test_scheduler_loop_without_delay_first_runs_immediately():
    """Default delay_first=False keeps firing the first tick right away
    (existing behaviour relied on by the import/metadata/wanted schedulers)."""
    scheduler = BackupScheduler()
    calls = []

    async def fake_run_once():
        calls.append(True)
        return True

    scheduler.run_once = fake_run_once  # type: ignore[method-assign]

    stop_event = asyncio.Event()
    task = asyncio.create_task(scheduler_loop(scheduler, interval_minutes=1, stop_event=stop_event))
    await asyncio.sleep(0.05)
    assert calls == [True]

    stop_event.set()
    await asyncio.wait_for(task, timeout=5)
