"""GET /api/v1/system/status health aggregation (issue #49 dashboard banner).

Reuses app.library.folder_health.probe_root_folder -- the same probe already
used by GET /api/v1/library/root-folders -- so this only asserts the new
`health` field surfaces the same facts, not a second implementation.
"""

from __future__ import annotations


def test_health_reports_missing_root_folder_by_default(app_client):
    """The default settings document ships one root folder
    (/data/audiobooks) that does not exist outside the container, so a
    fresh config starts in a degraded state -- exactly the case the
    Dashboard health banner exists to surface."""
    body = app_client.get("/api/v1/system/status").json()
    health = body["health"]
    assert health["ok"] is False
    assert health["rootFolderIssues"] == [{"path": "/data/audiobooks", "issue": "missing"}]


def test_health_reports_ok_when_no_root_folders_configured(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["root_folders"] = []
    assert app_client.put("/api/v1/settings", json=current).status_code == 200

    body = app_client.get("/api/v1/system/status").json()
    assert body["health"] == {"ok": True, "rootFolderIssues": []}


def test_health_reports_ok_for_an_existing_writable_root_folder(app_client, tmp_path):
    root = tmp_path / "audiobooks"
    root.mkdir()

    current = app_client.get("/api/v1/settings").json()
    current["root_folders"] = [{"path": str(root)}]
    assert app_client.put("/api/v1/settings", json=current).status_code == 200

    body = app_client.get("/api/v1/system/status").json()
    assert body["health"] == {"ok": True, "rootFolderIssues": []}
