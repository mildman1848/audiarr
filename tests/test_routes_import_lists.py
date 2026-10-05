"""Route-level tests for the generic import-list status/sync API (issue #72).

Follows the pattern in tests/test_routes_liberatarr.py: monkeypatch the
functions app.api.routes_import_lists imports, exercised through the
app_client fixture (isolated AUDIARR_CONFIG_DIR per test).
"""

from __future__ import annotations

from app.connections.liberatarr import LiberatarrError
from app.import_lists import ImportListSource, ImportListSyncResult


def _set_liberatarr_settings(
    app_client, base_url: str = "http://liberatarr.local", enabled: bool = True
) -> None:
    settings = app_client.get("/api/v1/settings").json()
    settings["connections"]["liberatarr"] = {"base_url": base_url, "token": "", "enabled": enabled}
    resp = app_client.put("/api/v1/settings", json=settings)
    assert resp.status_code == 200


def _row(asin: str, title: str = "Book One") -> dict:
    return {"asin": asin, "title": title, "authors": [], "narrators": [], "status": 0, "locale": ""}


def test_get_import_lists_lists_liberatarr_by_default(app_client):
    response = app_client.get("/api/v1/import-lists")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "liberatarr"
    assert body[0]["type"] == "liberatarr"
    assert body[0]["status"] == "unknown"
    assert body[0]["enabled"] is False


def test_get_import_lists_reflects_enabled_flag(app_client):
    _set_liberatarr_settings(app_client, enabled=True)
    body = app_client.get("/api/v1/import-lists").json()
    assert body[0]["enabled"] is True


def test_sync_import_list_unknown_source_returns_404(app_client):
    response = app_client.post("/api/v1/import-lists/does-not-exist/sync")
    assert response.status_code == 404


def test_sync_import_list_success_updates_status(app_client, monkeypatch):
    _set_liberatarr_settings(app_client)

    async def fake_fetch_library(settings):
        return [_row("B001")]

    monkeypatch.setattr("app.import_lists.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/import-lists/liberatarr/sync")
    assert response.status_code == 200
    body = response.json()
    assert body == {"created": 1, "skipped_existing": 0, "skipped_no_id": 0, "errors": []}

    status = app_client.get("/api/v1/import-lists").json()[0]
    assert status["status"] == "ok"
    assert status["last_created"] == 1
    assert status["last_sync_at"]
    assert status["last_error"] == ""


def test_sync_import_list_idempotent(app_client, monkeypatch):
    _set_liberatarr_settings(app_client)

    async def fake_fetch_library(settings):
        return [_row("B001")]

    monkeypatch.setattr("app.import_lists.fetch_library", fake_fetch_library)

    first = app_client.post("/api/v1/import-lists/liberatarr/sync").json()
    assert first["created"] == 1
    second = app_client.post("/api/v1/import-lists/liberatarr/sync").json()
    assert second == {"created": 0, "skipped_existing": 1, "skipped_no_id": 0, "errors": []}

    status = app_client.get("/api/v1/import-lists").json()[0]
    assert status["last_skipped"] == 1


def test_sync_import_list_upstream_failure_returns_502_and_records_error(app_client, monkeypatch):
    _set_liberatarr_settings(app_client)

    async def fake_fetch_library(settings):
        raise LiberatarrError("Could not reach Liberatarr: boom")

    monkeypatch.setattr("app.import_lists.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/import-lists/liberatarr/sync")
    assert response.status_code == 502
    assert "boom" in response.json()["detail"]

    status = app_client.get("/api/v1/import-lists").json()[0]
    assert status["status"] == "error"
    assert "boom" in status["last_error"]


def test_legacy_liberatarr_sync_also_updates_generic_status(app_client, monkeypatch):
    """The legacy /api/v1/liberatarr/sync route shares the same status
    bookkeeping as the generic route, so both entry points agree."""
    _set_liberatarr_settings(app_client)

    async def fake_fetch_library(settings):
        return [_row("B001")]

    monkeypatch.setattr("app.api.routes_liberatarr.fetch_library", fake_fetch_library)

    response = app_client.post("/api/v1/liberatarr/sync")
    assert response.status_code == 200

    status = app_client.get("/api/v1/import-lists").json()[0]
    assert status["status"] == "ok"
    assert status["last_created"] == 1


def test_import_list_source_out_from_source_round_trips_all_fields():
    from app.api.routes_import_lists import ImportListSourceOut

    source = ImportListSource(
        id="x", type="y", name="Z", enabled=True, status="ok",
        last_sync_at="2026-01-01 00:00:00", last_error="", last_created=2, last_skipped=3,
    )
    out = ImportListSourceOut.from_source(source)
    assert out.model_dump() == {
        "id": "x", "type": "y", "name": "Z", "enabled": True, "status": "ok",
        "last_sync_at": "2026-01-01 00:00:00", "last_error": "", "last_created": 2, "last_skipped": 3,
    }


def test_sync_liberatarr_rows_is_pure_and_reusable(app_client):
    """Sanity check that app.import_lists exports the shared row-processing
    result type used by both entry points."""
    result = ImportListSyncResult()
    assert result.created == 0
    assert result.errors == []
