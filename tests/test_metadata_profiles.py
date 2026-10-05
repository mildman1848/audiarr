"""Tests for the MetadataProfile settings model (issue #72 groundwork).

Follows the GET -> mutate -> PUT -> GET pattern used throughout
tests/test_settings.py; metadata profiles are settings-only (no DB
migration), same pattern as release_preferences/connect.
"""

from __future__ import annotations


def test_metadata_profiles_default_seeds_a_standard_profile(app_client):
    body = app_client.get("/api/v1/settings").json()
    profiles = body["metadata_profiles"]
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Standard"
    assert profiles[0]["is_default"] is True
    assert profiles[0]["enabled"] is True
    assert profiles[0]["languages"] == []
    assert profiles[0]["blocked_terms"] == []


def test_metadata_profiles_persist_via_settings_put(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["metadata_profiles"].append(
        {
            "id": "kids-safe",
            "name": "Kids Safe",
            "languages": ["us", "de"],
            "blocked_terms": ["explicit"],
            "enabled": True,
            "is_default": False,
        }
    )

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    profiles = {p["id"]: p for p in body["metadata_profiles"]}
    assert set(profiles) == {"default", "kids-safe"}
    assert profiles["kids-safe"]["languages"] == ["us", "de"]
    assert profiles["kids-safe"]["blocked_terms"] == ["explicit"]
    assert profiles["kids-safe"]["is_default"] is False


def test_metadata_profiles_can_be_removed(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["metadata_profiles"] = []

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 200

    body = app_client.get("/api/v1/settings").json()
    assert body["metadata_profiles"] == []


def test_metadata_profile_requires_name(app_client):
    current = app_client.get("/api/v1/settings").json()
    current["metadata_profiles"].append({"id": "no-name"})

    put_response = app_client.put("/api/v1/settings", json=current)
    assert put_response.status_code == 422
