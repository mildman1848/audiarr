"""Conversion job tests: enqueue, list, retry, cancel, failure handling."""

from __future__ import annotations

import sqlite3

import pytest

from app.db import migrate


def _fresh_db(tmp_path, monkeypatch) -> sqlite3.Connection:
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    import importlib

    from app import config as config_module

    importlib.reload(config_module)
    migrate()
    conn = sqlite3.connect(config_module.get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture()
def db(tmp_path, monkeypatch):
    conn = _fresh_db(tmp_path, monkeypatch)
    yield conn
    conn.close()


@pytest.fixture()
def book_id(db) -> int:
    cur = db.execute(
        "INSERT INTO books (title, language) VALUES ('Test Book', 'de')"
    )
    db.commit()
    return int(cur.lastrowid)


@pytest.fixture()
def client(app_client):
    return app_client


async def test_enqueue_requires_enabled_backend(app_client, monkeypatch):
    # Default settings have conversion disabled -> 409
    resp = app_client.post(
        "/api/v1/conversion/jobs",
        json={"book_id": 1, "source_path": "/data/x"},
    )
    assert resp.status_code == 409


async def test_enqueue_creates_queued_job(app_client, book_id, monkeypatch):
    _enable_backend(app_client, monkeypatch)
    resp = app_client.post(
        "/api/v1/conversion/jobs",
        json={"book_id": book_id, "source_path": "/data/x", "output_path": "/data/x.m4b"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["job_id"] >= 1

    jobs = app_client.get("/api/v1/conversion/jobs").json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["backend"] == "command"  # matches enabled test backend


async def test_enqueue_rejects_unknown_book(app_client, monkeypatch):
    _enable_backend(app_client, monkeypatch)
    resp = app_client.post(
        "/api/v1/conversion/jobs",
        json={"book_id": 99999, "source_path": "/data/x"},
    )
    assert resp.status_code == 404


async def test_enqueue_rejects_active_duplicate(app_client, book_id, monkeypatch):
    _enable_backend(app_client, monkeypatch)
    first = app_client.post(
        "/api/v1/conversion/jobs", json={"book_id": book_id, "source_path": "/a"}
    )
    assert first.status_code == 200
    second = app_client.post(
        "/api/v1/conversion/jobs", json={"book_id": book_id, "source_path": "/a"}
    )
    assert second.status_code == 409


async def test_retry_failed_job(app_client, book_id, monkeypatch):
    _enable_backend(app_client, monkeypatch)
    job = app_client.post(
        "/api/v1/conversion/jobs", json={"book_id": book_id, "source_path": "/a"}
    ).json()

    # Simulate worker marking the job failed
    _set_status(app_client, job["job_id"], "failed", "backend unreachable")

    resp = app_client.post(f"/api/v1/conversion/jobs/{job['job_id']}/retry")
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert resp.json()["error"] is None


async def test_cancel_queued_job(app_client, book_id, monkeypatch):
    _enable_backend(app_client, monkeypatch)
    job = app_client.post(
        "/api/v1/conversion/jobs", json={"book_id": book_id, "source_path": "/a"}
    ).json()

    resp = app_client.post(f"/api/v1/conversion/jobs/{job['job_id']}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


async def test_cancel_rejects_running_job(app_client, book_id, monkeypatch):
    _enable_backend(app_client, monkeypatch)
    job = app_client.post(
        "/api/v1/conversion/jobs", json={"book_id": book_id, "source_path": "/a"}
    ).json()
    _set_status(app_client, job["job_id"], "running")

    resp = app_client.post(f"/api/v1/conversion/jobs/{job['job_id']}/cancel")
    assert resp.status_code == 409


async def test_migration_v3_creates_conversion_jobs(db):
    tables = [
        r["name"]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    ]
    assert "conversion_jobs" in tables
    # FK enforcement sanity: inserting a job for a missing book fails
    db.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO conversion_jobs (book_id, source_path)
               VALUES (424242, '/nonexistent')"""
        )
        db.commit()


async def test_command_backend_renders_template(tmp_path):
    from app.conversion import CommandBackend
    from app.models.settings import ConversionSettings

    marker = tmp_path / "marker"
    settings = ConversionSettings(
        backend="command",
        command_template="touch {{output}}",
    )
    backend = CommandBackend(settings)
    result = await backend.convert(str(tmp_path / "src"), str(marker))
    assert result.ok, result.detail
    assert marker.exists()


async def test_command_backend_failed_command(tmp_path):
    from app.conversion import CommandBackend
    from app.models.settings import ConversionSettings

    settings = ConversionSettings(
        backend="command", command_template="exit 3"
    )
    backend = CommandBackend(settings)
    result = await backend.convert("/src", str(tmp_path / "out"))
    assert not result.ok
    assert "exited 3" in result.detail


async def test_m4b_client_reports_unreachable_backend():
    from app.conversion import M4BConvertarrClient
    from app.models.settings import ConversionSettings

    settings = ConversionSettings(
        backend="m4b-convertarr", base_url="http://127.0.0.1:59999"
    )
    client = M4BConvertarrClient(settings)
    result = await client.convert("/src")
    assert not result.ok
    assert "unreachable" in result.detail


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _enable_backend(app_client, monkeypatch) -> None:
    """Point conversion settings at a command backend that succeeds."""
    from app.config import load_settings, save_settings

    settings = load_settings()
    settings.conversion.backend = "command"
    settings.conversion.command_template = "true"
    save_settings(settings)


def _set_status(app_client, job_id: int, status: str, error: str | None = None) -> None:
    """Directly set a job's status, simulating worker transitions."""
    import sqlite3

    from app.config import get_db_path

    conn = sqlite3.connect(get_db_path())
    conn.execute(
        "UPDATE conversion_jobs SET status = ?, error = ?, updated_at = datetime('now') WHERE id = ?",
        (status, error, job_id),
    )
    conn.commit()
    conn.close()
