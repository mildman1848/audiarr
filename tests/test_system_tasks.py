"""Scheduler runtime registry and GET /api/v1/system/tasks (issue #73)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import scheduler_status
from app.import_scheduler import scheduler_loop
from app.scheduler_status import (
    REGISTRY,
    STATE_NOT_STARTED,
    STATE_RUNNING,
    STATE_SCHEDULED,
    STATE_STOPPED,
    TASK_IDS,
    SchedulerRegistry,
)

TASKS_URL = "/api/v1/system/tasks"
START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def _task(registry: SchedulerRegistry, task_id: str) -> dict:
    return next(t for t in registry.snapshot() if t["id"] == task_id)


async def _until(predicate) -> None:
    """Yield to the loop until ``predicate()`` holds (no real sleeping)."""

    async def _spin() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(_spin(), timeout=5)


@pytest.fixture(autouse=True)
def clean_registry():
    REGISTRY.reset()
    yield
    REGISTRY.reset()


# -- Registry transitions ------------------------------------------------------


def test_snapshot_lists_every_known_task_as_not_started():
    snapshot = SchedulerRegistry(FakeClock()).snapshot()
    assert [t["id"] for t in snapshot] == list(TASK_IDS)
    for task in snapshot:
        assert task == {
            "id": task["id"],
            "state": STATE_NOT_STARTED,
            "interval_minutes": None,
            "last_run_at": None,
            "next_due_at": None,
        }


def test_immediate_first_task_is_due_at_start():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    registry.register("import_scan", 15, delay_first=False)

    task = _task(registry, "import_scan")
    assert task["state"] == STATE_SCHEDULED
    assert task["interval_minutes"] == 15
    assert task["next_due_at"] == START
    assert task["last_run_at"] is None


def test_delay_first_task_is_due_after_one_interval():
    registry = SchedulerRegistry(FakeClock())
    registry.register("backup", 360, delay_first=True)

    task = _task(registry, "backup")
    assert task["next_due_at"] == START + timedelta(minutes=360)
    assert task["last_run_at"] is None


def test_tick_sets_last_run_to_completion_and_next_from_completion():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    registry.register("wanted_search", 10, delay_first=False)

    registry.run_started("wanted_search")
    running = _task(registry, "wanted_search")
    assert running["state"] == STATE_RUNNING
    assert running["next_due_at"] is None

    clock.advance(seconds=90)  # the run itself takes a while
    registry.run_finished("wanted_search", ran=True)
    done = _task(registry, "wanted_search")
    completed = START + timedelta(seconds=90)
    assert done["state"] == STATE_SCHEDULED
    assert done["last_run_at"] == completed
    assert done["next_due_at"] == completed + timedelta(minutes=10)


def test_skipped_tick_does_not_fabricate_a_last_run():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    registry.register("import_scan", 5, delay_first=False)

    registry.run_started("import_scan")
    clock.advance(seconds=30)
    registry.run_finished("import_scan", ran=False)

    task = _task(registry, "import_scan")
    assert task["last_run_at"] is None
    assert task["next_due_at"] == START + timedelta(seconds=30, minutes=5)


def test_stopped_keeps_last_run_and_clears_next_due():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    registry.register("metadata_refresh", 5, delay_first=False)
    registry.run_started("metadata_refresh")
    registry.run_finished("metadata_refresh", ran=True)
    registry.stopped("metadata_refresh")

    task = _task(registry, "metadata_refresh")
    assert task["state"] == STATE_STOPPED
    assert task["last_run_at"] == START
    assert task["next_due_at"] is None


def test_transitions_for_unregistered_task_are_ignored():
    registry = SchedulerRegistry(FakeClock())
    registry.run_started("backup")
    registry.run_finished("backup", ran=True)
    registry.stopped("backup")
    assert _task(registry, "backup")["state"] == STATE_NOT_STARTED


def test_snapshot_is_a_copy():
    registry = SchedulerRegistry(FakeClock())
    registry.register("backup", 60, delay_first=True)
    registry.snapshot()[-1]["state"] = "tampered"
    assert _task(registry, "backup")["state"] == STATE_SCHEDULED


# -- scheduler_loop integration ------------------------------------------------


class FakeScheduler:
    """run_once() advances the fake clock and reports ``result``."""

    def __init__(self, clock: FakeClock, result: bool = True, error: Exception | None = None):
        self.clock = clock
        self.result = result
        self.error = error
        self.calls = 0

    async def run_once(self) -> bool:
        self.calls += 1
        self.clock.advance(seconds=20)
        if self.error is not None:
            raise self.error
        return self.result


async def test_loop_immediate_first_tracks_run_and_stops():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    scheduler = FakeScheduler(clock)
    stop = asyncio.Event()

    task = asyncio.create_task(
        scheduler_loop(scheduler, 30, stop, task_id="import_scan", registry=registry)
    )
    await _until(lambda: scheduler.calls == 1 and _task(registry, "import_scan")["last_run_at"])

    snap = _task(registry, "import_scan")
    completed = START + timedelta(seconds=20)
    assert snap["state"] == STATE_SCHEDULED
    assert snap["interval_minutes"] == 30
    assert snap["last_run_at"] == completed
    assert snap["next_due_at"] == completed + timedelta(minutes=30)

    stop.set()
    await asyncio.wait_for(task, timeout=5)
    stopped = _task(registry, "import_scan")
    assert stopped["state"] == STATE_STOPPED
    assert stopped["next_due_at"] is None
    assert stopped["last_run_at"] == completed
    assert scheduler.calls == 1


async def test_loop_delay_first_does_not_run_or_record_last_run():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    scheduler = FakeScheduler(clock)
    stop = asyncio.Event()

    task = asyncio.create_task(
        scheduler_loop(
            scheduler, 360, stop, delay_first=True, task_id="backup", registry=registry
        )
    )
    await _until(lambda: _task(registry, "backup")["state"] == STATE_SCHEDULED)
    snap = _task(registry, "backup")
    assert snap["next_due_at"] == START + timedelta(minutes=360)
    assert snap["last_run_at"] is None
    assert scheduler.calls == 0

    stop.set()
    await asyncio.wait_for(task, timeout=5)
    stopped = _task(registry, "backup")
    assert stopped["state"] == STATE_STOPPED
    assert stopped["last_run_at"] is None
    assert stopped["next_due_at"] is None
    assert scheduler.calls == 0


async def test_loop_skipped_tick_leaves_last_run_empty():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    scheduler = FakeScheduler(clock, result=False)
    stop = asyncio.Event()

    task = asyncio.create_task(
        scheduler_loop(scheduler, 5, stop, task_id="wanted_search", registry=registry)
    )
    await _until(lambda: scheduler.calls == 1 and _task(registry, "wanted_search")["next_due_at"])
    assert _task(registry, "wanted_search")["last_run_at"] is None

    stop.set()
    await asyncio.wait_for(task, timeout=5)


async def test_loop_error_marks_stopped_without_fabricating_a_run():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    scheduler = FakeScheduler(clock, error=RuntimeError("boom"))

    with pytest.raises(RuntimeError):
        await scheduler_loop(scheduler, 5, asyncio.Event(), task_id="import_scan", registry=registry)

    snap = _task(registry, "import_scan")
    assert snap["state"] == STATE_STOPPED
    assert snap["last_run_at"] is None
    assert snap["next_due_at"] is None


async def test_loop_without_task_id_leaves_registry_untouched():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    scheduler = FakeScheduler(clock)
    stop = asyncio.Event()

    task = asyncio.create_task(scheduler_loop(scheduler, 5, stop, registry=registry))
    await _until(lambda: scheduler.calls == 1)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert all(t["state"] == STATE_NOT_STARTED for t in registry.snapshot())


async def test_loop_with_disabled_interval_does_not_register():
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    await scheduler_loop(FakeScheduler(clock), 0, asyncio.Event(), task_id="backup", registry=registry)
    assert _task(registry, "backup")["state"] == STATE_NOT_STARTED


# -- GET /api/v1/system/tasks ---------------------------------------------------


def test_route_shape_defaults_to_not_started(app_client, monkeypatch):
    # The default settings start the 24h backup loop in the app lifespan, so
    # swap in an empty registry to observe the all-not-started shape.
    monkeypatch.setattr("app.api.routes_system.REGISTRY", SchedulerRegistry(FakeClock()))
    resp = app_client.get(TASKS_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"tasks"}
    assert [t["id"] for t in body["tasks"]] == list(TASK_IDS)
    for task in body["tasks"]:
        assert set(task) == {
            "id",
            "state",
            "intervalMinutes",
            "lastRunAt",
            "nextRunAt",
            "manualTrigger",
        }
        assert task["state"] == "not_started"
        assert task["intervalMinutes"] is None
        assert task["lastRunAt"] is None
        assert task["nextRunAt"] is None
    # Only backups have a safe manual action.
    assert {t["id"] for t in body["tasks"] if t["manualTrigger"]} == {"backup"}


def test_route_reports_registry_timestamps_in_utc(app_client, monkeypatch):
    clock = FakeClock()
    registry = SchedulerRegistry(clock)
    monkeypatch.setattr("app.api.routes_system.REGISTRY", registry)
    registry.register("backup", 360, delay_first=True)
    registry.register("import_scan", 15, delay_first=False)
    registry.run_started("import_scan")
    registry.run_finished("import_scan", ran=True)

    tasks = {t["id"]: t for t in app_client.get(TASKS_URL).json()["tasks"]}
    assert tasks["backup"]["state"] == "scheduled"
    assert tasks["backup"]["intervalMinutes"] == 360
    assert tasks["backup"]["lastRunAt"] is None
    assert tasks["backup"]["nextRunAt"] == "2026-01-01T18:00:00+00:00"
    assert tasks["import_scan"]["lastRunAt"] == "2026-01-01T12:00:00+00:00"
    assert tasks["import_scan"]["nextRunAt"] == "2026-01-01T12:15:00+00:00"
    assert tasks["sab_auto_import"]["state"] == "not_started"


def test_route_is_read_only(app_client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(app_client, method)(TASKS_URL).status_code == 405


def test_route_does_not_leak_settings_or_paths(app_client):
    settings = app_client.get("/api/v1/settings").json()
    api_key = settings["auth"]["api_key"]
    settings["backup"]["folder"] = "/srv/private-backup-dir"
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    text = app_client.get(TASKS_URL).text
    assert "/srv/private-backup-dir" not in text
    assert api_key not in text


def test_route_requires_auth_when_enabled(app_client):
    settings = app_client.get("/api/v1/settings").json()
    api_key = settings["auth"]["api_key"]
    settings["auth"]["method"] = "forms"
    settings["auth"]["username"] = "admin"
    settings["auth"]["password"] = "s3cret-pw"
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    assert app_client.get(TASKS_URL).status_code == 401
    assert app_client.get(TASKS_URL, headers={"X-Api-Key": api_key}).status_code == 200
    login = app_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "s3cret-pw"}
    )
    assert login.status_code == 200
    assert app_client.get(TASKS_URL).status_code == 200


def test_lifespan_registers_only_started_loops(tmp_path, monkeypatch):
    """A delay-first backup loop is registered live; nothing else is started."""
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)
    settings = config_module.load_settings()
    settings.backup.interval_hours = 6
    config_module.save_settings(settings)

    from app import main as main_module

    importlib.reload(main_module)
    with TestClient(main_module.app) as client:
        tasks = {t["id"]: t for t in client.get(TASKS_URL).json()["tasks"]}
        assert tasks["backup"]["state"] == "scheduled"
        assert tasks["backup"]["intervalMinutes"] == 360
        assert tasks["backup"]["lastRunAt"] is None  # delay-first: no run yet
        assert tasks["backup"]["nextRunAt"] is not None
        for other in ("import_scan", "sab_auto_import", "metadata_refresh", "wanted_search"):
            assert tasks[other]["state"] == "not_started"

    assert next(t for t in REGISTRY.snapshot() if t["id"] == "backup")["state"] == STATE_STOPPED


# -- UI structure / i18n --------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", ["Next run (UTC)", "Status", "Action", "reset on restart"]),
        ("de", ["Nächster Lauf (UTC)", "Status", "Aktion", "Neustart zurückgesetzt"]),
    ],
)
def test_tasks_panel_has_schedule_columns_in_both_languages(app_client, language, expected):
    settings = app_client.get("/api/v1/settings").json()
    settings["ui"]["language"] = language
    assert app_client.put("/api/v1/settings", json=settings).status_code == 200

    page = app_client.get("/system/status").text
    assert 'id="system-tasks-table"' in page
    assert 'id="system-tasks-msg"' in page
    head = page[page.index('id="system-tasks-table"') : page.index('id="system-tasks-tbody"')]
    assert head.count("<th>") == 6
    for text in expected:
        assert text in page


def test_system_js_tasks_ui_uses_live_endpoint_and_single_safe_action():
    js = Path("app/web/static/js/system.js").read_text(encoding="utf-8")
    tasks_js = js[js.index("// -- Tasks tab") : js.index("// -- Events tab")]

    assert "/api/v1/system/tasks" in tasks_js
    assert "${esc(r.name)}" in tasks_js
    assert "${esc(nextRun)}" in tasks_js
    assert "${esc(taskStatusLabel(task.state))}" in tasks_js
    assert "data-task-action=\"backup-now\"" in tasks_js
    # Disables during the request and refreshes data afterwards.
    assert "btn.disabled = true" in tasks_js
    assert "refreshTasks();" in tasks_js
    # The only POST from the Tasks tab is the existing backup endpoint.
    assert tasks_js.count('method: "POST"') == 1
    assert 'fetch("/api/v1/system/backup", { method: "POST" })' in tasks_js
    for forbidden in ("/import/", "/sabnzbd", "/wanted", "/releases"):
        assert forbidden not in tasks_js


def test_task_i18n_keys_exist_and_are_localized():
    i18n = Path("app/web/i18n")
    en = json.loads((i18n / "en.json").read_text(encoding="utf-8"))
    de = json.loads((i18n / "de.json").read_text(encoding="utf-8"))
    keys = [
        "tasks_col_next_run",
        "tasks_col_status",
        "tasks_col_actions",
        "tasks_status_scheduled",
        "tasks_status_running",
        "tasks_status_stopped",
        "tasks_status_not_started",
        "tasks_backup_now",
        "tasks_backup_running",
        "tasks_backup_done",
        "tasks_backup_error",
    ]
    for key in keys:
        assert en[key].strip() and de[key].strip()
    assert en["tasks_backup_now"] == "Backup now"
    assert de["tasks_backup_now"] != en["tasks_backup_now"]
    assert scheduler_status.TASK_BACKUP == "backup"
