from __future__ import annotations

import importlib
from pathlib import Path


def test_health(app_client):
    response = app_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_system_status(app_client):
    response = app_client.get("/api/v1/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["appName"] == "Audiarr"
    assert "version" in body
    assert "pythonVersion" in body
    assert "osName" in body
    # Settings-derived maintenance state for the System/Status page.
    assert body["updates"] == {"branch": "main", "automatic": False}
    assert body["backup"] == {
        "folder": "/config/backups",
        "intervalHours": 24,
        "retentionCopies": 7,
    }
    assert body["logging"] == {"level": "INFO", "retentionDays": 14}


def test_dashboard_renders(app_client):
    response = app_client.get("/")
    assert response.status_code == 200
    assert "Audiarr" in response.text


def _client_with_backup_folder(tmp_path, monkeypatch, folder: Path):
    """app_client-equivalent, but with settings.backup.folder set before app startup
    (the default /config/backups is not writable outside the container)."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    settings = config_module.load_settings()
    settings.backup.folder = str(folder)
    config_module.save_settings(settings)

    from app import main as main_module

    importlib.reload(main_module)

    from fastapi.testclient import TestClient

    return TestClient(main_module.app)


def test_backup_post_and_get_endpoints(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    with _client_with_backup_folder(tmp_path, monkeypatch, backup_dir) as client:
        post_resp = client.post("/api/v1/system/backup")
        assert post_resp.status_code == 200
        body = post_resp.json()
        assert body["reason"] == "manual"
        assert Path(body["path"]).exists()
        assert {f["name"] for f in body["files"]} == {"audiarr.db", "settings.json"}

        get_resp = client.get("/api/v1/system/backup")
        assert get_resp.status_code == 200
        get_body = get_resp.json()
        assert len(get_body["backups"]) == 1
        assert get_body["backups"][0]["name"] == Path(body["path"]).name
        assert get_body["settings"] == {
            "folder": str(backup_dir),
            "intervalHours": 24,
            "retentionCopies": 7,
        }


def test_backup_post_returns_500_on_backup_error(tmp_path, monkeypatch):
    from app.backup_service import BackupError

    def failing_create_backup(reason):
        raise BackupError("boom")

    backup_dir = tmp_path / "backups"
    with _client_with_backup_folder(tmp_path, monkeypatch, backup_dir) as client:
        monkeypatch.setattr("app.api.routes_system.create_backup", failing_create_backup)
        resp = client.post("/api/v1/system/backup")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "boom"
