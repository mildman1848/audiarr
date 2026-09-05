def test_get_settings_defaults(app_client):
    response = app_client.get("/api/v1/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["ui"]["language"] == "en"
    assert body["metadata"]["provider_order"] == ["audible", "audnexus"]
    assert body["metadata"]["audible_locale"] == "de"


def test_put_settings_persists(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["ui"]["language"] = "de"
    current["root_folders"] = [{"path": "/data/hoerbuecher"}]

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    get_response = app_client.get("/api/v1/settings")
    body = get_response.json()
    assert body["ui"]["language"] == "de"
    assert body["root_folders"] == [{"path": "/data/hoerbuecher"}]
