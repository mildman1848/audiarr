import pytest

from app.config import load_settings


def test_get_settings_defaults(app_client):
    response = app_client.get("/api/v1/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["ui"]["language"] == "en"
    assert body["metadata"]["provider_order"] == ["audible", "audnexus"]
    assert body["metadata"]["audible_locale"] == "us"
    assert body["translation"]["backend"] == "none"


def test_import_scan_interval_defaults_to_off(app_client):
    body = app_client.get("/api/v1/settings").json()
    assert body["media_management"]["import_scan_interval_minutes"] == 0
    assert body["media_management"]["last_scheduled_scan_at"] == ""


def test_import_scan_interval_persists(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["media_management"]["import_scan_interval_minutes"] = 30

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    assert body["media_management"]["import_scan_interval_minutes"] == 30


def test_import_scan_interval_rejects_negative_values(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["media_management"]["import_scan_interval_minutes"] = -1

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 422


def test_sab_auto_import_defaults_to_off(app_client):
    body = app_client.get("/api/v1/settings").json()
    assert body["media_management"]["sab_auto_import_enabled"] is False
    assert body["media_management"]["sab_auto_import_category"] == ""
    assert body["media_management"]["sab_auto_import_interval_minutes"] == 5


def test_sab_auto_import_settings_persist(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["media_management"]["sab_auto_import_enabled"] = True
    current["media_management"]["sab_auto_import_category"] = "hoerbuecher"
    current["media_management"]["sab_auto_import_interval_minutes"] = 15

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    assert body["media_management"]["sab_auto_import_enabled"] is True
    assert body["media_management"]["sab_auto_import_category"] == "hoerbuecher"
    assert body["media_management"]["sab_auto_import_interval_minutes"] == 15


def test_sab_auto_import_interval_rejects_non_positive_values(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["media_management"]["sab_auto_import_interval_minutes"] = 0

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 422


def test_metadata_refresh_scheduler_defaults_to_off(app_client):
    body = app_client.get("/api/v1/settings").json()
    assert body["metadata"]["refresh_interval_minutes"] == 0
    assert body["metadata"]["refresh_batch_size"] == 10
    assert body["metadata"]["last_scheduled_refresh_at"] == ""
    assert body["metadata"]["last_refresh_updated"] == 0
    assert body["metadata"]["last_refresh_failed"] == 0
    assert body["metadata"]["last_refresh_remaining"] == 0


def test_metadata_refresh_scheduler_settings_persist(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["metadata"]["refresh_interval_minutes"] = 120
    current["metadata"]["refresh_batch_size"] = 25

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    assert body["metadata"]["refresh_interval_minutes"] == 120
    assert body["metadata"]["refresh_batch_size"] == 25


def test_metadata_refresh_interval_rejects_negative_values(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["metadata"]["refresh_interval_minutes"] = -1

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 422


def test_metadata_refresh_batch_size_rejects_non_positive_values(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["metadata"]["refresh_batch_size"] = 0

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 422


def test_wanted_search_scheduler_defaults_to_off(app_client):
    body = app_client.get("/api/v1/settings").json()
    assert body["wanted"]["search_interval_minutes"] == 0
    assert body["wanted"]["last_scheduled_search_at"] == ""
    assert body["wanted"]["last_search_grabbed"] == 0
    assert body["wanted"]["last_search_no_release"] == 0
    assert body["wanted"]["last_search_skipped"] == 0


def test_wanted_search_scheduler_settings_persist(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["wanted"]["search_interval_minutes"] = 60

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    assert body["wanted"]["search_interval_minutes"] == 60


def test_wanted_search_interval_rejects_negative_values(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["wanted"]["search_interval_minutes"] = -1

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 422


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


def test_legacy_quality_profile_json_still_parses():
    """Older settings.json only had name/allowed_formats/cutoff_format on a
    quality profile (issue #13 adds quality_ids/cutoff_quality_id/
    upgrade_allowed). Both must still validate, and the new fields must
    take sensible defaults."""
    from app.models.settings import Settings

    legacy = {
        "quality_profiles": [
            {"name": "Standard", "allowed_formats": ["m4b", "mp3"], "cutoff_format": "m4b"}
        ]
    }
    parsed = Settings.model_validate(legacy)
    profile = parsed.quality_profiles[0]
    assert profile.name == "Standard"
    assert profile.allowed_formats == ["m4b", "mp3"]
    assert profile.cutoff_format == "m4b"
    assert profile.quality_ids  # non-empty default
    assert profile.cutoff_quality_id
    assert profile.upgrade_allowed is True


def test_quality_definitions_have_audiobook_defaults(app_client):
    """Default quality_definitions are audiobook-shaped (container/codec/
    bitrate band/chapters), not a copy of video quality rungs."""
    body = app_client.get("/api/v1/settings").json()
    definitions = body["quality_definitions"]
    assert definitions
    ids = [d["id"] for d in definitions]
    assert len(ids) == len(set(ids)), "quality definition ids must be unique"
    for d in definitions:
        assert d["min_bitrate_kbps"] <= d["preferred_bitrate_kbps"] <= d["max_bitrate_kbps"]
        assert d["chapters"] in ("required", "preferred", "not_required")
    lossless = [d for d in definitions if d["lossless"]]
    assert lossless, "at least one lossless tier (e.g. FLAC) should be modeled"


def test_quality_definitions_and_profiles_persist(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["quality_definitions"] = [
        {
            "id": "custom-tier",
            "name": "Custom Tier",
            "container": "m4b",
            "codec": "aac",
            "lossless": False,
            "min_bitrate_kbps": 48,
            "preferred_bitrate_kbps": 96,
            "max_bitrate_kbps": 128,
            "chapters": "required",
        }
    ]
    current["quality_profiles"] = [
        {
            "name": "Audiobooks",
            "allowed_formats": ["m4b"],
            "cutoff_format": "m4b",
            "quality_ids": ["custom-tier"],
            "cutoff_quality_id": "custom-tier",
            "upgrade_allowed": False,
        }
    ]

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    definition = body["quality_definitions"][0]
    assert definition["id"] == "custom-tier"
    assert definition["min_bitrate_kbps"] == 48
    assert definition["chapters"] == "required"

    profile = body["quality_profiles"][0]
    assert profile["name"] == "Audiobooks"
    assert profile["quality_ids"] == ["custom-tier"]
    assert profile["cutoff_quality_id"] == "custom-tier"
    assert profile["upgrade_allowed"] is False



_SECRET_CASES = [
    pytest.param(
        ("connections", "audiobookshelf", "api_key"),
        ("connections", "audiobookshelf", "api_key_set"),
        ("connections", "audiobookshelf", "url"),
        id="audiobookshelf",
    ),
    pytest.param(
        ("connections", "m4b_convertarr", "api_key"),
        ("connections", "m4b_convertarr", "api_key_set"),
        ("connections", "m4b_convertarr", "url"),
        id="m4b-convertarr",
    ),
    pytest.param(
        ("connections", "liberatarr", "token"),
        ("connections", "liberatarr", "token_set"),
        ("connections", "liberatarr", "base_url"),
        id="liberatarr",
    ),
    pytest.param(
        ("conversion", "api_key"),
        ("conversion", "api_key_set"),
        ("conversion", "base_url"),
        id="conversion-api",
    ),
    pytest.param(
        ("translation", "api_key"),
        ("translation", "api_key_set"),
        ("translation", "base_url"),
        id="translation",
    ),
    pytest.param(
        ("conversion", "webhook_api_key"),
        ("conversion", "webhook_api_key_set"),
        None,
        id="conversion-webhook",
    ),
]


def _nested_value(document, path):
    value = document
    for part in path:
        value = value[part]
    return value


def _set_nested_value(document, path, value):
    parent = document
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = value


@pytest.mark.parametrize(("secret_path", "flag_path", "url_path"), _SECRET_CASES)
def test_integration_settings_secrets_are_write_only_and_preserved(
    app_client, secret_path, flag_path, url_path
):
    secret = "test-only-integration-secret"
    replacement = "test-only-replacement-secret"
    settings = app_client.get("/api/v1/settings").json()
    original_url = "http://configured.example/base"
    if url_path:
        _set_nested_value(settings, url_path, original_url)
    _set_nested_value(settings, secret_path, secret)

    created = app_client.put("/api/v1/settings", json=settings)
    assert created.status_code == 200
    for response in (created, app_client.get("/api/v1/settings")):
        assert secret not in response.text
        assert _nested_value(response.json(), flag_path) is True
        assert secret_path[-1] not in _nested_value(response.json(), secret_path[:-1])

    # A regular blank-secret PUT at the same destination retains the value.
    blank = app_client.get("/api/v1/settings").json()
    same_destination = app_client.put("/api/v1/settings", json=blank)
    assert same_destination.status_code == 200
    assert secret not in same_destination.text
    assert _nested_value(same_destination.json(), flag_path) is True
    stored = load_settings()
    assert _nested_value(stored.model_dump(), secret_path) == secret

    if url_path:
        # A blank secret must not be inherited by a changed endpoint.
        changed = app_client.get("/api/v1/settings").json()
        _set_nested_value(changed, url_path, "http://new-destination.example/base")
        rejected = app_client.put("/api/v1/settings", json=changed)
        assert rejected.status_code == 422
        assert secret not in rejected.text
        persisted = load_settings().model_dump()
        assert _nested_value(persisted, url_path) == original_url
        assert _nested_value(persisted, secret_path) == secret

        # Explicitly entering a replacement permits the endpoint change.
        _set_nested_value(changed, secret_path, replacement)
        accepted = app_client.put("/api/v1/settings", json=changed)
        assert accepted.status_code == 200
        assert replacement not in accepted.text
        assert _nested_value(accepted.json(), flag_path) is True
        persisted = load_settings().model_dump()
        assert _nested_value(persisted, url_path) == "http://new-destination.example/base"
        assert _nested_value(persisted, secret_path) == replacement
    else:
        # The conversion webhook key is inbound; changing the outbound base URL
        # must not clear or retarget it.
        changed = app_client.get("/api/v1/settings").json()
        changed["conversion"]["base_url"] = "http://new-converter.example"
        preserved = app_client.put("/api/v1/settings", json=changed)
        assert preserved.status_code == 200
        assert secret not in preserved.text
        assert preserved.json()["conversion"]["webhook_api_key_set"] is True
        assert load_settings().conversion.webhook_api_key == secret


def test_auth_api_key_remains_in_settings_response(app_client):
    settings = app_client.get("/api/v1/settings").json()
    settings["auth"]["api_key"] = "test-only-auth-key"
    response = app_client.put("/api/v1/settings", json=settings)
    assert response.status_code == 200
    assert response.json()["auth"]["api_key"] == "test-only-auth-key"


def test_release_preferences_have_audiobook_shaped_defaults(app_client):
    """Issue #70: default release preferences ship audiobook-specific
    examples (unabridged preferred, abridged/dramatized blocked), not an
    empty custom-format DSL."""
    body = app_client.get("/api/v1/settings").json()
    prefs = body["release_preferences"]
    preferred_terms = [t["term"] for t in prefs["preferred_terms"]]
    blocked_terms = [t["term"] for t in prefs["blocked_terms"]]
    assert "unabridged" in preferred_terms
    assert "abridged" in blocked_terms
    assert "dramatized" in blocked_terms
    assert prefs["minimum_preference_score"] == 0


def test_release_preferences_persist(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["release_preferences"] = {
        "preferred_terms": [
            {"id": "p1", "term": "full cast", "score": 5, "category": "edition", "enabled": True}
        ],
        "blocked_terms": [{"id": "b1", "term": "sample", "enabled": True}],
        "minimum_preference_score": 3,
    }

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    prefs = body["release_preferences"]
    assert prefs["preferred_terms"] == [
        {"id": "p1", "term": "full cast", "score": 5, "category": "edition", "enabled": True}
    ]
    assert prefs["blocked_terms"] == [{"id": "b1", "term": "sample", "enabled": True}]
    assert prefs["minimum_preference_score"] == 3
