"""Render tests for the server-rendered UI pages.

Covers the Library, Metadata Search, Connections, and Settings pages. Each
page must return 200 and contain a translated marker string for both the
English and German UI language settings, and mark its own nav entry active.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _set_ui_language(app_client, language: str) -> None:
    current = app_client.get("/api/v1/settings").json()
    current["ui"]["language"] = language
    assert app_client.put("/api/v1/settings", json=current).status_code == 200


def _nav_item_classes(html: str, href: str) -> list[str]:
    """Return the class list of the sidebar nav `<a>` for the given href.

    Robust to markup growing extra classes/attributes over time — unlike an
    exact `<a href="..." class="active">` string match, this only cares that
    the "nav-item" and (when active) "active" classes are present.
    """
    match = re.search(rf'<a href="{re.escape(href)}" class="([^"]*)"', html)
    assert match, f"no sidebar nav link found for href={href!r}"
    return match.group(1).split()


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
    ("language", "connections_marker", "conversion_marker"),
    [
        ("en", "Scan now", "Conversion backend"),
        ("de", "Jetzt scannen", "Konvertierungs-Backend"),
    ],
)
def test_connections_and_settings_pages_render(
    app_client, language, connections_marker, conversion_marker
):
    _set_ui_language(app_client, language)

    connections = app_client.get("/connections")
    assert connections.status_code == 200
    assert connections_marker in connections.text

    # The Conversion field lives on its own dedicated settings page now,
    # not on the /settings overview.
    conversion = app_client.get("/settings/conversion")
    assert conversion.status_code == 200
    assert conversion_marker in conversion.text
    assert 'id="conversion-backend"' in conversion.text


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

    downloadclients = app_client.get("/settings/download-clients")
    assert downloadclients.status_code == 200
    assert downloadclients_marker in downloadclients.text
    # ...but the concrete client name is a brand name.
    assert "SABnzbd" in downloadclients.text
    assert 'id="sab-url"' in downloadclients.text
    assert 'id="sab-test-btn"' in downloadclients.text

    indexers = app_client.get("/settings/indexers")
    assert indexers.status_code == 200
    assert indexers_marker in indexers.text
    assert "Prowlarr" in indexers.text
    assert 'id="prowlarr-url"' in indexers.text
    assert 'id="prowlarr-test-btn"' in indexers.text


@pytest.mark.parametrize(
    ("language", "security_marker", "method_marker"),
    [
        ("en", "Security", "Authentication method"),
        ("de", "Sicherheit", "Authentifizierungsmethode"),
    ],
)
def test_settings_page_has_security_section(
    app_client, language, security_marker, method_marker
):
    _set_ui_language(app_client, language)

    general = app_client.get("/settings/general")
    assert general.status_code == 200
    assert security_marker in general.text
    assert method_marker in general.text
    assert 'id="security-method"' in general.text
    assert 'id="security-username"' in general.text
    assert 'id="security-password"' in general.text
    assert 'id="security-api-key"' in general.text
    assert 'id="security-api-key-copy-btn"' in general.text
    assert 'id="security-api-key-regen-btn"' in general.text


@pytest.mark.parametrize(
    ("language", "marker"),
    [
        ("en", "Back to Library"),
        ("de", "Zurück zur Bibliothek"),
    ],
)
def test_book_detail_page_renders(app_client, language, marker):
    _set_ui_language(app_client, language)

    # The route is server-rendered and does not require an existing book;
    # the JS client fetches book data by id and handles a 404 inline.
    page = app_client.get("/library/books/1")
    assert page.status_code == 200
    assert marker in page.text
    assert "/static/js/book_detail.js" in page.text
    assert 'data-book-id="1"' in page.text


def test_navigation_marks_active_route(app_client):
    _set_ui_language(app_client, "en")

    routes = ("/", "/library", "/import", "/metadata", "/connections", "/settings")
    for route in routes:
        page = app_client.get(route)
        classes = _nav_item_classes(page.text, route)
        assert "nav-item" in classes
        assert "active" in classes
        # Every other nav link on the page must not also claim "active".
        for other in routes:
            if other == route:
                continue
            other_classes = _nav_item_classes(page.text, other)
            assert "active" not in other_classes


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


@pytest.mark.parametrize(
    ("language", "marker"),
    [
        ("en", "Unmatched Folders"),
        ("de", "Nicht zugeordnete Ordner"),
    ],
)
def test_import_page_renders(app_client, language, marker):
    _set_ui_language(app_client, language)

    page = app_client.get("/import")
    assert page.status_code == 200
    assert marker in page.text
    assert "/static/js/import.js" in page.text


def test_import_page_marks_nav_active_and_nav_link_present_everywhere(app_client):
    _set_ui_language(app_client, "en")

    import_page = app_client.get("/import")
    assert "active" in _nav_item_classes(import_page.text, "/import")

    for path in ALL_PAGES:
        page = app_client.get(path)
        assert 'href="/import"' in page.text


def test_search_and_activity_mark_nav_active(app_client):
    _set_ui_language(app_client, "en")

    search = app_client.get("/search")
    assert "active" in _nav_item_classes(search.text, "/search")

    activity = app_client.get("/activity")
    assert "active" in _nav_item_classes(activity.text, "/activity")


@pytest.mark.parametrize(
    ("language", "labels"),
    [
        (
            "en",
            [
                "Media Management",
                "Profiles",
                "Quality",
                "Indexers",
                "Download Clients",
                "Connect",
                "Metadata",
                "Tags",
                "General",
                "UI",
                "Conversion",
            ],
        ),
        (
            "de",
            [
                "Medienverwaltung",
                "Profile",
                "Qualität",
                "Indexer",
                "Download-Clients",
                "Verbinden",
                "Metadaten",
                "Tags",
                "Allgemein",
                "Oberfläche",
                "Konvertierung",
            ],
        ),
    ],
)
def test_settings_overview_links_to_dedicated_sections(app_client, language, labels):
    """The settings overview page (Sonarr-style categories grid) links to
    every dedicated /settings/<section> page, with translated card
    labels, in both UI languages."""
    _set_ui_language(app_client, language)

    settings = app_client.get("/settings")
    assert settings.status_code == 200
    for slug in (
        "media-management",
        "profiles",
        "quality",
        "indexers",
        "download-clients",
        "connect",
        "metadata",
        "tags",
        "general",
        "ui",
        "conversion",
    ):
        assert f'href="/settings/{slug}"' in settings.text
    for label in labels:
        assert label in settings.text


SETTINGS_SECTION_URLS = (
    "/settings/media-management",
    "/settings/profiles",
    "/settings/quality",
    "/settings/indexers",
    "/settings/download-clients",
    "/settings/connect",
    "/settings/metadata",
    "/settings/tags",
    "/settings/general",
    "/settings/ui",
    "/settings/conversion",
)


@pytest.mark.parametrize(
    "path",
    (
        "/settings/media-management",
        "/settings/indexers",
        "/settings/download-clients",
        "/settings/general",
        "/settings/conversion",
    ),
)
def test_settings_section_pages_return_200(app_client, path):
    _set_ui_language(app_client, "en")

    page = app_client.get(path)
    assert page.status_code == 200


@pytest.mark.parametrize("path", SETTINGS_SECTION_URLS)
def test_settings_section_page_marks_active_subnav_entry(app_client, path):
    """Each dedicated settings page marks its own sub-nav link active and
    no other section link as active."""
    _set_ui_language(app_client, "en")

    page = app_client.get(path)
    assert page.status_code == 200
    match = re.search(r'<nav class="section-tabs settings-subnav"[^>]*>(.*?)</nav>', page.text, re.S)
    assert match, "settings sub-nav not found"
    subnav_html = match.group(1)

    own_href = f'href="{path}"'
    assert f'<a {own_href} class="active"' in subnav_html
    for other in SETTINGS_SECTION_URLS:
        if other == path:
            continue
        assert f'<a href="{other}" class="active"' not in subnav_html


@pytest.mark.parametrize(
    ("language", "nav_label"),
    [
        ("en", "Download Clients"),
        ("de", "Download-Clients"),
    ],
)
def test_settings_subnav_renders_translated_text(app_client, language, nav_label):
    """The settings sub-nav itself (not just the page heading) renders
    translated section labels in both UI languages."""
    _set_ui_language(app_client, language)

    page = app_client.get("/settings/general")
    assert page.status_code == 200
    match = re.search(r'<nav class="section-tabs settings-subnav"[^>]*>(.*?)</nav>', page.text, re.S)
    assert match, "settings sub-nav not found"
    assert nav_label in match.group(1)


@pytest.mark.parametrize("language", ["en", "de"])
def test_settings_page_has_media_management_and_summary_fields(app_client, language):
    """Media Management exposes the modeled rename/pattern/delete-empty-folder
    fields, and the Profiles/Connect pages render their summary
    containers."""
    _set_ui_language(app_client, language)

    media = app_client.get("/settings/media-management")
    assert media.status_code == 200
    assert 'id="media-rename-files"' in media.text
    assert 'id="media-file-name-pattern"' in media.text
    assert 'id="media-delete-empty-folders"' in media.text

    profiles = app_client.get("/settings/profiles")
    assert profiles.status_code == 200
    assert 'id="profile-summary"' in profiles.text

    connect = app_client.get("/settings/connect")
    assert connect.status_code == 200
    assert 'id="connect-summary"' in connect.text


def test_settings_js_logs_in_after_enabling_forms_auth():
    script = (Path(__file__).parents[1] / "app/web/static/js/settings.js").read_text()

    assert "async function loginAfterAuthChange" in script
    assert 'fetch("/api/v1/auth/login"' in script
    assert 'if (doc.auth.method === "forms" && password)' in script
    assert "await loginAfterAuthChange(doc.auth.username, password)" in script


def test_common_js_has_mobile_sidebar_drawer_logic():
    """The off-canvas sidebar drawer (hamburger toggle, backdrop, Escape-key
    close, and the body.sidebar-open state it all drives) lives in
    common.js so every page gets it for free."""
    script = (Path(__file__).parents[1] / "app/web/static/js/common.js").read_text()

    assert "function openSidebar" in script
    assert "function closeSidebar" in script
    assert 'classList.add("sidebar-open")' in script
    assert 'classList.remove("sidebar-open")' in script
    assert 'sidebar.style.setProperty("translate", "280px 0px", "important")' in script
    assert 'sidebar.style.left = mobileQuery.matches ? "-280px" : ""' in script
    assert 'sidebar.style.translate = ""' in script
    assert 'setAttribute("aria-expanded"' in script
    assert 'setAttribute("aria-hidden"' in script
    assert 'event.key === "Escape"' in script
    assert "matchMedia" in script


ALL_PAGES = (
    "/",
    "/library",
    "/import",
    "/metadata",
    "/search",
    "/activity",
    "/connections",
    "/settings",
)


@pytest.mark.parametrize("path", ALL_PAGES)
def test_app_shell_markers_present(app_client, path):
    """Every page renders the Arr-style shell: brand subtitle, top bar,
    eyebrow, the language quick-switch, the toast container, and the
    Sonarr-like sidebar (desktop rail / mobile off-canvas drawer) markup."""
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
    # Sidebar shell: fixed desktop rail that becomes a mobile drawer.
    assert 'id="app-sidebar"' in page.text
    assert 'class="side-nav"' in page.text
    assert 'class="sidebar-toggle"' in page.text
    assert 'aria-controls="app-sidebar"' in page.text
    assert 'aria-expanded="false"' in page.text
    assert 'aria-label="Open navigation"' in page.text
    assert 'class="sidebar-backdrop"' in page.text


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


@pytest.mark.parametrize("path", ALL_PAGES)
def test_page_toolbar_and_global_search_markup_present(app_client, path):
    """Every page renders the Arr-style page-toolbar region (between the
    topbar and the content) and the global search trigger + overlay markup
    that opens it."""
    _set_ui_language(app_client, "en")

    page = app_client.get(path)
    assert page.status_code == 200
    assert 'class="page-toolbar"' in page.text
    assert 'class="toolbar-btn icon-only global-search-btn"' in page.text
    assert 'id="global-search-overlay"' in page.text
    assert 'class="search-overlay' in page.text
    assert 'id="global-search-input"' in page.text
    assert 'id="global-search-results"' in page.text


@pytest.mark.parametrize(
    ("language", "queue_label", "history_label"),
    [
        ("en", "Queue", "History"),
        ("de", "Warteschlange", "Verlauf"),
    ],
)
def test_activity_page_has_tab_segmented_control(
    app_client, language, queue_label, history_label
):
    _set_ui_language(app_client, language)

    page = app_client.get("/activity")
    assert page.status_code == 200
    assert 'data-activity-tab="queue"' in page.text
    assert 'data-activity-tab="history"' in page.text
    assert 'id="activity-queue-section"' in page.text
    assert 'id="activity-history-section"' in page.text
    assert queue_label in page.text
    assert history_label in page.text


def test_library_page_has_filter_and_sort_controls(app_client):
    _set_ui_language(app_client, "en")

    page = app_client.get("/library")
    assert page.status_code == 200
    assert 'id="library-filter"' in page.text
    assert 'id="library-sort"' in page.text
    assert 'id="library-refresh-top"' in page.text
    assert 'id="view-grid-btn"' in page.text
    assert 'id="view-table-btn"' in page.text


def test_common_js_has_global_search_overlay_logic():
    """The global search overlay lives in common.js so every page gets it
    for free: debounced fetch, Escape-to-close, and backdrop-click-to-close."""
    script = (Path(__file__).parents[1] / "app/web/static/js/common.js").read_text()

    assert "function setupGlobalSearch" in script
    assert "GLOBAL_SEARCH_DEBOUNCE_MS" in script
    assert "setTimeout(() => runSearch(query), GLOBAL_SEARCH_DEBOUNCE_MS)" in script
    assert 'event.key === "Escape" && !overlay.hidden' in script
    assert "event.target === overlay" in script
    assert "function escapeHtmlLocal" in script
