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
