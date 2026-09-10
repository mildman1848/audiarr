def test_get_settings_defaults(app_client):
    response = app_client.get("/api/v1/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["ui"]["language"] == "en"
    assert body["metadata"]["provider_order"] == ["audible", "audnexus"]
    assert body["metadata"]["audible_locale"] == "us"
    assert body["translation"]["backend"] == "none"


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


def test_sabnzbd_and_prowlarr_settings_persist(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["download_clients"] = [
        {
            "name": "SABnzbd",
            "type": "sabnzbd",
            "url": "http://sab:8080",
            "api_key": "sab-secret",
            "category": "audiobooks",
            "enabled": True,
        }
    ]
    current["indexers"] = [
        {
            "name": "Prowlarr",
            "type": "prowlarr",
            "url": "http://prowlarr:9696",
            "api_key": "prowlarr-secret",
            "enabled": True,
        }
    ]

    assert app_client.put("/api/v1/settings", json=current).status_code == 200

    body = app_client.get("/api/v1/settings").json()
    sab = body["download_clients"][0]
    assert sab["type"] == "sabnzbd"
    assert sab["url"] == "http://sab:8080"
    assert sab["category"] == "audiobooks"
    assert sab["enabled"] is True
    idx = body["indexers"][0]
    assert idx["type"] == "prowlarr"
    assert idx["url"] == "http://prowlarr:9696"
    assert idx["enabled"] is True


def test_legacy_download_client_and_indexer_json_still_parses():
    """Older settings.json used host/port for download clients and a
    bare name/url/enabled indexer. Both must still validate."""
    from app.models.settings import Settings

    legacy = {
        "download_clients": [
            {"name": "old-sab", "type": "generic", "host": "10.0.0.5", "port": 8080}
        ],
        "indexers": [{"name": "old-indexer", "url": "http://idx.local", "enabled": True}],
    }
    parsed = Settings.model_validate(legacy)
    assert parsed.download_clients[0].host == "10.0.0.5"
    assert parsed.download_clients[0].base_url() == "http://10.0.0.5:8080"
    assert parsed.indexers[0].type == "generic"
    assert parsed.indexers[0].url == "http://idx.local"


def test_download_client_base_url_prefers_explicit_url():
    from app.models.settings import DownloadClient

    dc = DownloadClient(name="s", type="sabnzbd", url="http://sab:8080/", host="ignored")
    assert dc.base_url() == "http://sab:8080"
    assert DownloadClient(name="s").base_url() == ""
