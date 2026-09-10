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


@pytest.mark.parametrize(
    ("language", "downloadclients_marker", "indexers_marker"),
    [
        ("en", "Download Clients", "Indexers"),
        ("de", "Download-Clients", "Indexer"),
    ],
)
def test_settings_page_has_sabnzbd_and_prowlarr_sections(
    app_client, language, downloadclients_marker, indexers_marker
):
    _set_ui_language(app_client, language)

    settings = app_client.get("/settings")
    assert settings.status_code == 200
    # Section headings are translated...
    assert downloadclients_marker in settings.text
    assert indexers_marker in settings.text
    # ...but the concrete client/indexer names are brand names.
    assert "SABnzbd" in settings.text
    assert "Prowlarr" in settings.text
    # Test buttons and their status spans are wired up.
    assert 'id="sab-test-btn"' in settings.text
    assert 'id="prowlarr-test-btn"' in settings.text


def test_navigation_marks_active_route(app_client):
    _set_ui_language(app_client, "en")

    dashboard = app_client.get("/")
    assert '<a href="/" class="active">' in dashboard.text

    library = app_client.get("/library")
    assert '<a href="/library" class="active">' in library.text

    metadata = app_client.get("/metadata")
    assert '<a href="/metadata" class="active">' in metadata.text

    connections = app_client.get("/connections")
    assert '<a href="/connections" class="active">' in connections.text

    settings = app_client.get("/settings")
    assert '<a href="/settings" class="active">' in settings.text


@pytest.mark.parametrize(
    ("language", "welcome_marker"),
    [
        ("en", "Welcome to Audiarr"),
        ("de", "Willkommen bei Audiarr"),
    ],
)
def test_dashboard_page_renders(app_client, language, welcome_marker):
    _set_ui_language(app_client, language)

    dashboard = app_client.get("/")
    assert dashboard.status_code == 200
    assert welcome_marker in dashboard.text


@pytest.mark.parametrize(
    ("language", "search_marker", "activity_marker"),
    [
        ("en", "Search your indexers via Prowlarr", "live SABnzbd download queue"),
        ("de", "Durchsuche deine Indexer über Prowlarr", "aktuelle SABnzbd-Download-Warteschlange"),
    ],
)
def test_search_and_activity_pages_render(
    app_client, language, search_marker, activity_marker
):
    _set_ui_language(app_client, language)

    search = app_client.get("/search")
    assert search.status_code == 200
    assert search_marker in search.text
    assert "/static/js/search.js" in search.text

    activity = app_client.get("/activity")
    assert activity.status_code == 200
    assert activity_marker in activity.text
    assert "/static/js/activity.js" in activity.text


def test_search_and_activity_mark_nav_active(app_client):
    _set_ui_language(app_client, "en")

    search = app_client.get("/search")
    assert '<a href="/search" class="active">' in search.text

    activity = app_client.get("/activity")
    assert '<a href="/activity" class="active">' in activity.text


ALL_PAGES = (
    "/",
    "/library",
    "/metadata",
    "/search",
    "/activity",
    "/connections",
    "/settings",
)


@pytest.mark.parametrize("path", ALL_PAGES)
def test_app_shell_markers_present(app_client, path):
    """Every page renders the Arr-style shell: brand subtitle, top bar,
    eyebrow, the language quick-switch, and the toast container."""
    _set_ui_language(app_client, "en")

    page = app_client.get(path)
    assert page.status_code == 200
    assert 'class="brand-sub"' in page.text
    assert 'class="topbar"' in page.text
    assert 'class="eyebrow"' in page.text
    assert 'class="lang-switch"' in page.text
    assert 'data-lang="en"' in page.text
    assert 'data-lang="de"' in page.text
    assert 'id="toast-container"' in page.text
    assert "/static/js/common.js" in page.text


@pytest.mark.parametrize(
    ("language", "active_lang", "inactive_lang"),
    [
        ("en", 'data-lang="en"', 'data-lang="de"'),
        ("de", 'data-lang="de"', 'data-lang="en"'),
    ],
)
def test_language_switch_marks_current_language(
    app_client, language, active_lang, inactive_lang
):
    _set_ui_language(app_client, language)

    page = app_client.get("/library")
    assert f'<button type="button" class="lang-btn active" {active_lang}>' in page.text
    assert f'<button type="button" class="lang-btn" {inactive_lang}>' in page.text
