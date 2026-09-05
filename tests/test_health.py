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


def test_dashboard_renders(app_client):
    response = app_client.get("/")
    assert response.status_code == 200
    assert "Audiarr" in response.text
