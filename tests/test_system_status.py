"""System/Status endpoint shape, including the update-check state (issue #33)."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from app import __version__
from app.api import routes_system

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_runtime_version_matches_dockerfile_arg_version():
    """Guards against runtime/build version drift (Hermes live-smoke, issue #33):
    app.__version__ must be bumped alongside the Dockerfile ARG VERSION default."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"^ARG VERSION=(\S+)$", dockerfile, re.MULTILINE)
    assert match, "Dockerfile must declare `ARG VERSION=<version>`"
    assert __version__ == match.group(1)


def test_system_status_includes_update_state(app_client):
    body = app_client.get("/api/v1/system/status").json()
    updates = body["updates"]
    assert body["version"] == __version__
    assert updates["currentVersion"] == __version__
    assert updates["checkEnabled"] is True
    assert updates["latestVersion"] == ""
    assert updates["updateAvailable"] is False
    assert updates["lastCheckedAt"] == ""
    assert updates["lastError"] == ""


def test_update_check_endpoint_disabled_makes_no_request(app_client, monkeypatch):
    current = app_client.get("/api/v1/settings").json()
    current["updates"]["check_enabled"] = False
    assert app_client.put("/api/v1/settings", json=current).status_code == 200

    async def _boom(*args, **kwargs):
        raise AssertionError("no network request should be made when disabled")

    monkeypatch.setattr(httpx.AsyncClient, "get", _boom)

    resp = app_client.post("/api/v1/system/update-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["lastError"] == ""


def test_update_check_endpoint_reports_newer_version(app_client, monkeypatch):
    async def fake_check_for_updates(force: bool = False, client=None) -> dict:
        return {
            "enabled": True,
            "current_version": __version__,
            "latest_version": "999.0.0",
            "latest_url": "https://github.com/mildman1848/audiarr/releases/tag/v999.0.0",
            "latest_name": "Audiarr 999.0.0",
            "update_available": True,
            "checked_at": "2026-01-01 00:00:00",
            "error": "",
        }

    monkeypatch.setattr(routes_system, "check_for_updates", fake_check_for_updates)

    resp = app_client.post("/api/v1/system/update-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["updateAvailable"] is True
    assert body["latestVersion"] == "999.0.0"
    assert body["latestUrl"].endswith("v999.0.0")


def test_update_check_endpoint_unreachable_returns_safe_payload(app_client, monkeypatch):
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    def fake_async_client(*args, **kwargs):
        return real_async_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.update_check.httpx.AsyncClient", fake_async_client)

    resp = app_client.post("/api/v1/system/update-check")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["lastError"]
    assert body["updateAvailable"] is False


def test_update_check_settings_persist(app_client):
    current = app_client.get("/api/v1/settings").json()
    assert current["updates"]["check_enabled"] is True
    current["updates"]["check_enabled"] = False

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    assert body["updates"]["check_enabled"] is False
    assert body["updates"]["repository"] == "mildman1848/audiarr"
