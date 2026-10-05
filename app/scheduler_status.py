"""Process-local runtime registry for the background schedulers (issue #73).

``app.import_scheduler.scheduler_loop`` reports into this registry when it is
given a task identifier, so the System -> Tasks view can show the *actual*
live schedule instead of guessing from persisted settings. Everything here is
in memory only: nothing is written to disk or the DB, and all timestamps are
lost on restart (a task that is not registered has simply not been started in
this process).

Timestamps are timezone-aware UTC ``datetime`` objects; the clock is
injectable so tests stay deterministic.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Stable task identifiers, in display order. These are API values.
TASK_IMPORT_SCAN = "import_scan"
TASK_SAB_AUTO_IMPORT = "sab_auto_import"
TASK_METADATA_REFRESH = "metadata_refresh"
TASK_WANTED_SEARCH = "wanted_search"
TASK_BACKUP = "backup"
TASK_IDS = (
    TASK_IMPORT_SCAN,
    TASK_SAB_AUTO_IMPORT,
    TASK_METADATA_REFRESH,
    TASK_WANTED_SEARCH,
    TASK_BACKUP,
)

# Runtime states. ``not_started`` is never stored: it is what a snapshot
# reports for a known task that no loop registered in this process.
STATE_NOT_STARTED = "not_started"
STATE_SCHEDULED = "scheduled"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class TaskRuntime:
    """Live state of one scheduler loop."""

    task_id: str
    interval_minutes: int
    state: str = STATE_SCHEDULED
    last_run_at: datetime | None = None
    next_due_at: datetime | None = None


class SchedulerRegistry:
    """Thread-safe in-memory map of task id -> :class:`TaskRuntime`."""

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskRuntime] = {}

    def register(self, task_id: str, interval_minutes: int, *, delay_first: bool) -> None:
        """A loop started: due now (immediate-first) or after one full interval."""
        now = self._clock()
        due = now + timedelta(minutes=interval_minutes) if delay_first else now
        with self._lock:
            self._tasks[task_id] = TaskRuntime(
                task_id=task_id, interval_minutes=interval_minutes, next_due_at=due
            )

    def run_started(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.state = STATE_RUNNING
                task.next_due_at = None

    def run_finished(self, task_id: str, *, ran: bool) -> None:
        """A tick ended. ``ran=False`` (skipped tick) leaves last-run untouched."""
        now = self._clock()
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.state = STATE_SCHEDULED
            if ran:
                task.last_run_at = now
            task.next_due_at = now + timedelta(minutes=task.interval_minutes)

    def stopped(self, task_id: str) -> None:
        """Loop ended (shutdown or error): keep last-run, drop the next due time."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.state = STATE_STOPPED
                task.next_due_at = None

    def reset(self) -> None:
        with self._lock:
            self._tasks.clear()

    def snapshot(self) -> list[dict]:
        """One plain dict per known task id (copies, safe to serialise)."""
        with self._lock:
            tasks = {tid: TaskRuntime(**vars(t)) for tid, t in self._tasks.items()}
        result = []
        for task_id in TASK_IDS:
            task = tasks.get(task_id)
            if task is None:
                result.append(
                    {
                        "id": task_id,
                        "state": STATE_NOT_STARTED,
                        "interval_minutes": None,
                        "last_run_at": None,
                        "next_due_at": None,
                    }
                )
                continue
            result.append(
                {
                    "id": task_id,
                    "state": task.state,
                    "interval_minutes": task.interval_minutes,
                    "last_run_at": task.last_run_at,
                    "next_due_at": task.next_due_at,
                }
            )
        return result


# Process-wide registry used by app.main's schedulers and the tasks route.
REGISTRY = SchedulerRegistry()
