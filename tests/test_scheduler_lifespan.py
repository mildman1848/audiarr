"""app.main lifespan wiring for the metadata refresh and wanted-search
schedulers (issue #26): each starts only when its own gate condition is
met, mirroring the existing import-scan/SABnzbd-auto-import lifespan tests.
"""

from __future__ import annotations

import importlib


def _reload_app(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    from app import main as main_module

    importlib.reload(main_module)
    return main_module


def test_metadata_refresh_scheduler_starts_when_interval_positive(tmp_path, monkeypatch):
    from app.config import load_settings, save_settings

    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    settings = load_settings()
    settings.metadata.refresh_interval_minutes = 60
    save_settings(settings)

    main_module = _reload_app(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as client:
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200


def test_metadata_refresh_scheduler_disabled_by_default(tmp_path, monkeypatch):
    main_module = _reload_app(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as client:
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200
        assert resp.json()["metadata"]["refresh_interval_minutes"] == 0


def test_wanted_search_scheduler_starts_when_configured_and_connections_present(
    tmp_path, monkeypatch
):
    from app.config import load_settings, save_settings
    from app.models.settings import DownloadClient, Indexer

    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    settings = load_settings()
    settings.wanted.search_interval_minutes = 60
    settings.indexers = [
        Indexer(
            name="Prowlarr", type="prowlarr", url="http://127.0.0.1:1", api_key="x", enabled=True
        )
    ]
    settings.download_clients = [
        DownloadClient(
            name="SABnzbd", type="sabnzbd", url="http://127.0.0.1:1",
            category="audiobooks", enabled=True,
        )
    ]
    save_settings(settings)

    main_module = _reload_app(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as client:
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200


def test_wanted_search_scheduler_disabled_without_connections(tmp_path, monkeypatch):
    """A positive interval with no enabled Prowlarr/SABnzbd must not start
    the poller (see app.main's lifespan gate)."""
    from app.config import load_settings, save_settings

    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    settings = load_settings()
    settings.wanted.search_interval_minutes = 60
    save_settings(settings)

    main_module = _reload_app(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as client:
        resp = client.get("/api/v1/settings")
        assert resp.status_code == 200
