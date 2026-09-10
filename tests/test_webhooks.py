"""Webhook-driven conversion tests: callback, matching, import, cleanup."""

from __future__ import annotations

import sqlite3


def _db(app_client) -> sqlite3.Connection:
    from app.config import get_db_path

    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _setup_convertarr_backend(app_client, delete_originals=False, webhook_key=""):
    from app.config import load_settings, save_settings

    settings = load_settings()
    settings.conversion.backend = "m4b-convertarr"
    settings.conversion.base_url = "http://127.0.0.1:59997"  # nothing there
    settings.conversion.api_key = "k"
    settings.conversion.webhook_api_key = webhook_key
    settings.conversion.delete_originals = delete_originals
    save_settings(settings)


def _seed_book_and_job(app_client, title="Der Vorleser", source="/tmp/convsrc") -> tuple[int, int]:
    conn = _db(app_client)
    try:
        cur = conn.execute(
            "INSERT INTO books (title, language) VALUES (?, 'de')", (title,)
        )
        book_id = int(cur.lastrowid or 0)
        cur = conn.execute(
            """INSERT INTO conversion_jobs (book_id, source_path, status, backend)
               VALUES (?, ?, 'running', 'm4b-convertarr')""",
            (book_id, source),
        )
        job_id = int(cur.lastrowid or 0)
        conn.commit()
        return book_id, job_id
    finally:
        conn.close()


async def test_webhook_rejects_bad_key(app_client):
    _setup_convertarr_backend(app_client, webhook_key="secret123")
    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={"converted_path": "/x/y.m4b", "title": "T"},
        headers={"X-Api-Key": "wrong"},
    )
    assert resp.status_code == 401


async def test_webhook_completes_job_and_imports_file(app_client, tmp_path):
    _setup_convertarr_backend(app_client)
    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"\x00" * 128)
    book_id, job_id = _seed_book_and_job(app_client, source=str(tmp_path / "src"))

    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={
            "converted_path": str(m4b),
            "title": "Der Vorleser",
            "author": "Bernhard Schlink",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["accepted"] is True
    assert data["job_id"] == job_id
    assert data["book_id"] == book_id

    conn = _db(app_client)
    try:
        job = conn.execute(
            "SELECT status, completed_path FROM conversion_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        assert job["status"] == "completed"
        assert job["completed_path"] == str(m4b)
        # Edition + library file recorded
        files = conn.execute(
            """SELECT lf.path FROM library_files lf
               JOIN editions e ON e.id = lf.edition_id
               WHERE e.book_id = ? AND lf.format = 'm4b'""",
            (book_id,),
        ).fetchall()
        assert len(files) == 1
        assert files[0]["path"] == str(m4b)
        # Edition locale defaults to the persisted default ("us")
        edition = conn.execute(
            "SELECT locale FROM editions WHERE book_id = ? AND format = 'm4b'",
            (book_id,),
        ).fetchone()
        assert edition["locale"] == "us"
    finally:
        conn.close()


async def test_webhook_import_uses_configured_locale(app_client, tmp_path):
    """The imported edition inherits the persisted default Audible locale."""
    from app.config import load_settings, save_settings

    _setup_convertarr_backend(app_client)
    settings = load_settings()
    settings.metadata.audible_locale = "de"
    save_settings(settings)

    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"\x00" * 128)
    book_id, _job_id = _seed_book_and_job(app_client, source=str(tmp_path / "src"))

    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={"converted_path": str(m4b), "title": "Der Vorleser"},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True

    conn = _db(app_client)
    try:
        edition = conn.execute(
            "SELECT locale FROM editions WHERE book_id = ? AND format = 'm4b'",
            (book_id,),
        ).fetchone()
        assert edition["locale"] == "de"
    finally:
        conn.close()


async def test_webhook_deletes_originals_when_enabled(app_client, tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "01.mp3").write_bytes(b"\x00" * 64)
    (src_dir / "02.mp3").write_bytes(b"\x00" * 64)
    m4b = tmp_path / "converted.m4b"
    m4b.write_bytes(b"\x00" * 128)

    _setup_convertarr_backend(app_client, delete_originals=True)
    book_id, job_id = _seed_book_and_job(app_client, source=str(src_dir))

    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={"converted_path": str(m4b), "title": "Der Vorleser"},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True

    assert not (src_dir / "01.mp3").exists()
    assert not (src_dir / "02.mp3").exists()

    conn = _db(app_client)
    try:
        job = conn.execute(
            "SELECT originals_deleted FROM conversion_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert job["originals_deleted"] == 2
    finally:
        conn.close()


async def test_webhook_keeps_originals_when_disabled(app_client, tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    original = src_dir / "01.mp3"
    original.write_bytes(b"\x00" * 64)
    m4b = tmp_path / "converted.m4b"
    m4b.write_bytes(b"\x00" * 128)

    _setup_convertarr_backend(app_client, delete_originals=False)
    _seed_book_and_job(app_client, source=str(src_dir))

    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={"converted_path": str(m4b), "title": "Der Vorleser"},
    )
    assert resp.status_code == 200
    assert original.exists(), "originals must survive when delete_originals=false"


async def test_webhook_no_matching_job(app_client, tmp_path):
    _setup_convertarr_backend(app_client)
    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"\x00" * 8)

    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={"converted_path": str(m4b), "title": "Unknown Book"},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is False


async def test_webhook_failure_callback_marks_job_failed(app_client):
    _setup_convertarr_backend(app_client)
    _seed_book_and_job(app_client)

    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={"title": "Der Vorleser", "status": "failed"},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True

    conn = _db(app_client)
    try:
        job = conn.execute(
            "SELECT status, error FROM conversion_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert job["status"] == "failed"
        assert "failure" in job["error"]
    finally:
        conn.close()


async def test_webhook_single_running_job_assumed_without_title(app_client, tmp_path):
    _setup_convertarr_backend(app_client)
    m4b = tmp_path / "book.m4b"
    m4b.write_bytes(b"\x00" * 8)
    book_id, job_id = _seed_book_and_job(app_client, title="Some Other Title")

    # No title hint, exactly one running job -> assumed match
    resp = app_client.post(
        "/api/v1/webhooks/m4b-convertarr",
        json={"converted_path": str(m4b), "title": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True
    assert resp.json()["job_id"] == job_id


async def test_worker_http_backend_waits_for_webhook(app_client, tmp_path, monkeypatch):
    """HTTP backend accept must leave the job running (webhook completes it)."""
    from app.conversion.worker import process_one_job

    _setup_convertarr_backend(app_client)
    conn = _db(app_client)
    try:
        cur = conn.execute(
            "INSERT INTO books (title, language) VALUES ('W', 'de')"
        )
        book_id = int(cur.lastrowid or 0)
        conn.execute(
            """INSERT INTO conversion_jobs (book_id, source_path, status, backend)
               VALUES (?, '/src', 'queued', 'm4b-convertarr')""",
            (book_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # Point the backend at a stub that accepts
    from app.config import load_settings
    from app.conversion import M4BConvertarrClient

    class AcceptingStub(M4BConvertarrClient):
        async def convert(self, source_path: str, output_path: str = ""):
            from app.conversion import ConversionResult

            return ConversionResult(ok=True, detail="accepted", output_path=output_path)

    import app.conversion.worker as worker_mod

    orig_build = worker_mod.build_backend
    monkeypatch.setattr(
        worker_mod, "build_backend", lambda s: AcceptingStub(load_settings().conversion)
    )
    try:
        did = await process_one_job()
        assert did is True
    finally:
        monkeypatch.setattr(worker_mod, "build_backend", orig_build)

    conn = _db(app_client)
    try:
        job = conn.execute(
            "SELECT status FROM conversion_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert job["status"] == "running", "HTTP-accepted job must await webhook"
    finally:
        conn.close()


async def test_job_timeout_reaps_stale_running_jobs(app_client):
    from app.conversion.worker import process_timeouts

    _setup_convertarr_backend(app_client)
    conn = _db(app_client)
    try:
        cur = conn.execute("INSERT INTO books (title, language) VALUES ('T', 'de')")
        book_id = int(cur.lastrowid or 0)
        conn.execute(
            """INSERT INTO conversion_jobs
               (book_id, source_path, status, backend, updated_at)
               VALUES (?, '/src', 'running', 'm4b-convertarr',
                       datetime('now', '-8 hours'))""",
            (book_id,),
        )
        conn.commit()
    finally:
        conn.close()

    failed = await process_timeouts()
    assert failed == 1

    conn = _db(app_client)
    try:
        job = conn.execute(
            "SELECT status, error FROM conversion_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert job["status"] == "failed"
        assert "timeout" in job["error"]
    finally:
        conn.close()
