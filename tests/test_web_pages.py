"""Render tests for the server-rendered UI pages.

Covers the Library, Metadata Search, Connections, and Settings pages. Each
page must return 200 and contain a translated marker string for both the
English and German UI language settings, and mark its own nav entry active.
"""

from __future__ import annotations

import pytest


def _set_ui_language(app_client, language: str) -> None:
    current = app_client.get("/api/v1/settings").json()
    current["ui"]["language"] = language
    assert app_client.put("/api/v1/settings", json=current).status_code == 200


@pytest.mark.parametrize(
    ("language", "library_marker", "metadata_marker"),
    [
        ("en", "Root Folders", "Search audiobook metadata providers"),
        ("de", "Stammordner", "Durchsuche Metadaten-Anbieter"),
    ],
)
def test_library_and_metadata_pages_render(
    app_client, language, library_marker, metadata_marker
):
    _set_ui_language(app_client, language)

    library = app_client.get("/library")
    assert library.status_code == 200
    assert library_marker in library.text

    metadata = app_client.get("/metadata")
    assert metadata.status_code == 200
    assert metadata_marker in metadata.text


@pytest.mark.parametrize(
    ("language", "connections_marker", "settings_marker"),
    [
        ("en", "Scan now", "Conversion backend"),
        ("de", "Jetzt scannen", "Konvertierungs-Backend"),
    ],
)
def test_connections_and_settings_pages_render(
    app_client, language, connections_marker, settings_marker
):
    _set_ui_language(app_client, language)

    connections = app_client.get("/connections")
    assert connections.status_code == 200
    assert connections_marker in connections.text

    settings = app_client.get("/settings")
    assert settings.status_code == 200
    assert settings_marker in settings.text


def test_navigation_marks_active_route(app_client):
    _set_ui_language(app_client, "en")

    library = app_client.get("/library")
    assert '<a href="/library" class="active">' in library.text

    metadata = app_client.get("/metadata")
    assert '<a href="/metadata" class="active">' in metadata.text

    connections = app_client.get("/connections")
    assert '<a href="/connections" class="active">' in connections.text

    settings = app_client.get("/settings")
    assert '<a href="/settings" class="active">' in settings.text
