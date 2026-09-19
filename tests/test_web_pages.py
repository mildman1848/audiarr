"""Render tests for the server-rendered UI pages.

Covers the Library, Add Audiobook, Connections, and Settings pages. Each
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
    ("language", "nav_label", "heading_marker"),
    [
        ("en", "Add New", "Add Audiobook"),
        ("de", "Neu hinzufügen", "Hörbuch hinzufügen"),
    ],
)
def test_metadata_page_uses_add_new_labels(app_client, language, nav_label, heading_marker):
    """The Metadata Search page is reframed as an Arr-style Add New /
    Add Audiobook workflow (issue #10) — the sidebar and the page heading
    must agree in both languages."""
    _set_ui_language(app_client, language)

    page = app_client.get("/metadata")
    assert page.status_code == 200
    assert f'<span class="nav-label">{nav_label}</span>' in page.text
    assert f'<h1 class="page-title">{heading_marker}</h1>' in page.text


@pytest.mark.parametrize("language", ["en", "de"])
def test_search_page_uses_releases_label(app_client, language):
    """The release/download search page is labeled Releases (issue #10),
    identically in both languages since it is already an established
    Sonarr/Radarr loanword in German Arr UIs."""
    _set_ui_language(app_client, language)

    page = app_client.get("/search")
    assert page.status_code == 200
    assert '<span class="nav-label">Releases</span>' in page.text
    assert '<h1 class="page-title">Releases</h1>' in page.text


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
    ("language", "toggle_marker", "help_marker"),
    [
        ("en", "Only show profile-fitting releases", "hides releases that are below cutoff"),
        ("de", "Nur passende Releases anzeigen", "werden Releases ausgeblendet"),
    ],
)
def test_search_page_has_quality_fit_toggle(app_client, language, toggle_marker, help_marker):
    """Issue #22: the Releases page ships an optional, default-off toggle
    that hides non-fitting releases, with translated label/help text in
    both UI languages."""
    _set_ui_language(app_client, language)

    page = app_client.get("/search")
    assert page.status_code == 200
    assert 'id="rs-quality-fit-only"' in page.text
    assert 'type="checkbox" id="rs-quality-fit-only"' in page.text
    assert toggle_marker in page.text
    assert help_marker in page.text


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


def test_book_detail_page_has_toolbar_and_root_marker(app_client):
    """Book detail must have a page-toolbar (Back + Delete) above the
    JS-populated detail root, not buttons rendered inline by the JS."""
    _set_ui_language(app_client, "en")

    page = app_client.get("/library/books/1")
    assert page.status_code == 200
    assert 'id="book-detail-toolbar"' in page.text
    assert 'id="book-detail-back-btn"' in page.text
    assert 'id="book-detail-delete-btn"' in page.text
    # Detail root, populated client-side once the book is fetched.
    assert 'id="book-detail"' in page.text


def test_library_page_has_grid_container_and_view_controls(app_client):
    """Library page ships the container the JS grid/table render into, plus
    the grid/table toggle, filter, and sort controls (client-rendered)."""
    _set_ui_language(app_client, "en")

    page = app_client.get("/library")
    assert page.status_code == 200
    assert 'id="library-books"' in page.text
    assert 'id="view-grid-btn"' in page.text
    assert 'id="view-table-btn"' in page.text
    assert 'data-view="grid"' in page.text
    assert 'data-view="table"' in page.text
    assert 'id="library-filter"' in page.text
    assert 'id="library-sort"' in page.text


def test_library_js_defines_card_grid_markup():
    """The Arr-style cover-card grid classes must exist in library.js since
    the grid itself is only rendered client-side (no server-side book data
    in this test suite's HTML assertions)."""
    js = Path("app/web/static/js/library.js").read_text(encoding="utf-8")
    for class_name in (
        "library-grid",
        "library-card",
        "library-cover",
        "library-card-body",
        "library-card-title",
        "library-badge-row",
        "library-card-actions",
    ):
        assert class_name in js


def test_book_detail_js_defines_hero_markup():
    js = Path("app/web/static/js/book_detail.js").read_text(encoding="utf-8")
    for class_name in (
        "book-hero",
        "book-hero-cover",
        "book-hero-body",
        "book-hero-title",
        "book-hero-badges",
        "book-hero-stats",
    ):
        assert class_name in js


def test_navigation_marks_active_route(app_client):
    _set_ui_language(app_client, "en")

    routes = (
        "/", "/library", "/wanted/missing", "/import", "/metadata",
        "/connections", "/settings",
    )
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
    ("language", "marker"),
    [
        ("en", "Application health, version info"),
        ("de", "Anwendungszustand, Versionsinformationen"),
    ],
)
def test_system_status_page_renders(app_client, language, marker):
    _set_ui_language(app_client, language)

    page = app_client.get("/system/status")
    assert page.status_code == 200
    assert marker in page.text
    assert "/static/js/system.js" in page.text


def test_system_status_page_has_status_markers(app_client):
    """The page ships the root container, a refresh button, and the
    JS-populated app-version marker (client-rendered from /api/v1/system/status)."""
    _set_ui_language(app_client, "en")

    page = app_client.get("/system/status")
    assert page.status_code == 200
    assert 'id="system-status-root"' in page.text
    assert 'id="system-refresh-top"' in page.text
    assert 'id="system-version"' in page.text
    assert 'id="system-health-status"' in page.text
    assert 'id="system-app-name"' in page.text
    assert 'id="system-python-version"' in page.text
    assert 'id="system-os-name"' in page.text
    assert 'id="system-updates-branch"' in page.text
    assert 'id="system-backup-folder"' in page.text
    assert 'id="system-logging-level"' in page.text


def test_system_status_page_marks_nav_active(app_client):
    _set_ui_language(app_client, "en")

    page = app_client.get("/system/status")
    assert "active" in _nav_item_classes(page.text, "/system/status")

    other = app_client.get("/settings")
    assert "active" not in _nav_item_classes(other.text, "/system/status")


def test_system_js_defines_refresh_and_status_functions():
    js = Path("app/web/static/js/system.js").read_text(encoding="utf-8")
    assert "async function refreshHealth" in js
    assert "async function refreshStatus" in js
    assert '"/api/v1/system/status"' in js
    assert '"/health"' in js


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
    fields, and the Profiles/Quality/Connect pages render their editor/summary
    containers."""
    _set_ui_language(app_client, language)

    media = app_client.get("/settings/media-management")
    assert media.status_code == 200
    assert 'id="media-rename-files"' in media.text
    assert 'id="media-file-name-pattern"' in media.text
    assert 'id="media-delete-empty-folders"' in media.text
    assert 'id="media-scan-interval"' in media.text
    assert 'id="media-scan-last-run"' in media.text

    profiles = app_client.get("/settings/profiles")
    assert profiles.status_code == 200
    assert 'id="profiles-editor"' in profiles.text
    assert 'id="profiles-add-btn"' in profiles.text

    quality = app_client.get("/settings/quality")
    assert quality.status_code == 200
    assert 'id="quality-definitions"' in quality.text
    assert 'id="quality-add-btn"' in quality.text

    connect = app_client.get("/settings/connect")
    assert connect.status_code == 200
    assert 'id="connect-summary"' in connect.text


@pytest.mark.parametrize(
    ("language", "planned_label"),
    [
        ("en", "Planned"),
        ("de", "Geplant"),
    ],
)
def test_settings_overview_marks_planned_sections(app_client, language, planned_label):
    """Connect and Tags are read-only/placeholder this slice; Profiles and
    Quality now have a real, saveable editor (issue #13), so the overview
    must badge exactly those remaining two as planned."""
    _set_ui_language(app_client, language)

    settings = app_client.get("/settings")
    assert settings.status_code == 200
    badge = f'<span class="badge badge-planned">{planned_label}</span>'
    assert settings.text.count(badge) == 2


@pytest.mark.parametrize(
    "path",
    ("/settings/tags", "/settings/connect"),
)
def test_planned_settings_pages_have_no_save_bar(app_client, path):
    """Placeholder-only settings pages must not render the Arr-style
    No changes / Save changes bar — there is nothing real to save."""
    _set_ui_language(app_client, "en")

    page = app_client.get(path)
    assert page.status_code == 200
    assert 'id="settings-save-bar"' not in page.text
    assert 'id="settings-advanced-toggle"' not in page.text
    assert "badge-planned" in page.text


@pytest.mark.parametrize(
    "path",
    (
        "/settings/media-management",
        "/settings/profiles",
        "/settings/quality",
        "/settings/indexers",
        "/settings/download-clients",
        "/settings/metadata",
        "/settings/general",
        "/settings/ui",
        "/settings/conversion",
    ),
)
def test_active_settings_pages_keep_save_bar(app_client, path):
    """Sections with real, saveable fields keep the dirty-state save bar
    and advanced toggle, and are not marked planned."""
    _set_ui_language(app_client, "en")

    page = app_client.get(path)
    assert page.status_code == 200
    assert 'id="settings-save-bar"' in page.text
    assert 'id="settings-advanced-toggle"' in page.text
    assert "badge-planned" not in page.text


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
    "/calendar",
    "/wanted/missing",
    "/import",
    "/metadata",
    "/search",
    "/activity",
    "/connections",
    "/settings",
    "/system/status",
)


@pytest.mark.parametrize("path", ALL_PAGES)
def test_footer_has_no_scaffold_wording(app_client, path):
    """Issue #9: no rendered page may call Audiarr an MVP/scaffold anymore —
    the footer must use product wording instead."""
    _set_ui_language(app_client, "en")

    page = app_client.get(path)
    assert page.status_code == 200
    assert "scaffold" not in page.text.lower()
    assert "MVP" not in page.text
    assert "Audiobook library automation" in page.text


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


@pytest.mark.parametrize(
    ("language", "marker"),
    [
        ("en", "Wanted / Missing"),
        ("de", "Gesucht / Fehlend"),
    ],
)
def test_wanted_missing_page_renders(app_client, language, marker):
    _set_ui_language(app_client, language)

    page = app_client.get("/wanted/missing")
    assert page.status_code == 200
    assert marker in page.text
    assert "/static/js/wanted.js" in page.text


def test_wanted_missing_page_has_root_marker_and_toolbar_controls(app_client):
    _set_ui_language(app_client, "en")

    page = app_client.get("/wanted/missing")
    assert page.status_code == 200
    assert 'id="wanted-missing-root"' in page.text
    assert 'id="wanted-refresh-top"' in page.text
    assert 'id="wanted-filter"' in page.text
    assert 'id="wanted-missing-list"' in page.text
    assert 'id="wanted-cutoff-list"' in page.text


def test_wanted_missing_page_marks_nav_active(app_client):
    _set_ui_language(app_client, "en")

    page = app_client.get("/wanted/missing")
    assert "active" in _nav_item_classes(page.text, "/wanted/missing")

    other = app_client.get("/library")
    assert "active" not in _nav_item_classes(other.text, "/wanted/missing")


@pytest.mark.parametrize(
    ("language", "marker"),
    [
        ("en", "Calendar"),
        ("de", "Kalender"),
    ],
)
def test_calendar_page_renders(app_client, language, marker):
    _set_ui_language(app_client, language)

    page = app_client.get("/calendar")
    assert page.status_code == 200
    assert marker in page.text
    assert "/static/js/calendar.js" in page.text


def test_calendar_page_has_root_marker_and_toolbar_controls(app_client):
    _set_ui_language(app_client, "en")

    page = app_client.get("/calendar")
    assert page.status_code == 200
    assert 'id="calendar-root"' in page.text
    assert 'id="calendar-prev-btn"' in page.text
    assert 'id="calendar-next-btn"' in page.text
    assert 'id="calendar-today-btn"' in page.text
    assert 'id="calendar-month-label"' in page.text
    assert 'id="calendar-grid"' in page.text
    assert 'id="calendar-agenda-list"' in page.text
    assert 'id="calendar-day-modal"' in page.text


def test_calendar_page_marks_nav_active(app_client):
    _set_ui_language(app_client, "en")

    page = app_client.get("/calendar")
    assert "active" in _nav_item_classes(page.text, "/calendar")

    other = app_client.get("/library")
    assert "active" not in _nav_item_classes(other.text, "/calendar")


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


# -- Issue #12: standardized empty states + dashboard app-home polish -------


def test_common_js_defines_shared_empty_state_helper():
    """Library, Wanted, Calendar, and Activity share one empty-state
    renderer (icon + message + optional action) instead of each page
    inventing its own <p class="muted"> markup."""
    script = (Path(__file__).parents[1] / "app/web/static/js/common.js").read_text()

    assert "function emptyState" in script
    assert "window.AudiarrUI = { emptyState }" in script
    assert '"empty-state"' in script
    assert "empty-state-icon" in script
    assert "empty-state-title" in script


@pytest.mark.parametrize(
    ("js_file", "expected_calls"),
    [
        ("library.js", 3),
        ("wanted.js", 3),
        ("calendar.js", 3),
        ("activity.js", 3),
        ("app.js", 1),
    ],
)
def test_pages_use_shared_empty_state_helper(js_file, expected_calls):
    """Each touched page's client script renders its empty/config-missing
    states through window.AudiarrUI.emptyState rather than ad hoc markup."""
    script = (Path(__file__).parents[1] / "app/web/static/js" / js_file).read_text()
    assert script.count("window.AudiarrUI.emptyState(") == expected_calls


def test_activity_js_config_missing_state_reads_as_queue_history():
    """Issue #12: the SABnzbd-not-configured state must not look like a
    generic error card — it renders through the shared empty-state helper
    (inside the existing Queue/History .card sections) with a Settings CTA,
    not the standalone .danger-card used elsewhere (e.g. search.js)."""
    script = (Path(__file__).parents[1] / "app/web/static/js/activity.js").read_text()

    assert "danger-card" not in script
    assert "function renderConfigWarning" in script
    assert 'href="/settings"' in script


def test_activity_js_wraps_tables_in_table_scroll():
    """Both the queue and history tables must be wrapped in .table-scroll so
    they scroll horizontally on narrow viewports instead of overflowing the
    page (mobile behavior review, issue #12)."""
    script = (Path(__file__).parents[1] / "app/web/static/js/activity.js").read_text()
    assert script.count('<div class="table-scroll">') == 2


def test_style_css_defines_empty_state_and_quick_action_components():
    css = (Path(__file__).parents[1] / "app/web/static/css/style.css").read_text()
    for selector in (
        ".empty-state {",
        ".empty-state-icon",
        ".empty-state-title",
        ".empty-state-hint",
        ".quick-action-grid",
        ".quick-action-card",
    ):
        assert selector in css


@pytest.mark.parametrize(
    ("language", "heading_marker", "action_title_marker"),
    [
        ("en", "Quick actions", "Add Audiobook"),
        ("de", "Schnellzugriff", "Hörbuch hinzufügen"),
    ],
)
def test_dashboard_has_quick_actions(app_client, language, heading_marker, action_title_marker):
    """Issue #12: the dashboard leads with app-home quick actions (built
    from existing routes only, no new backend data) instead of only stat
    cards, in both UI languages."""
    _set_ui_language(app_client, language)

    page = app_client.get("/")
    assert page.status_code == 200
    assert heading_marker in page.text
    assert action_title_marker in page.text
    assert 'class="quick-action-card" href="/metadata"' in page.text
    assert 'class="quick-action-card" href="/library"' in page.text
    assert 'class="quick-action-card" href="/wanted/missing"' in page.text
    assert 'class="quick-action-card" href="/calendar"' in page.text
