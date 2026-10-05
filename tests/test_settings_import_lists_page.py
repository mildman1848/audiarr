"""Web tests for the Settings > Import Lists page (issue #72)."""

from __future__ import annotations


def test_import_lists_settings_page_returns_200(app_client):
    page = app_client.get("/settings/import-lists")
    assert page.status_code == 200
    assert "import-lists-table" in page.text


def test_settings_overview_links_to_import_lists(app_client):
    overview = app_client.get("/settings")
    assert overview.status_code == 200
    assert 'href="/settings/import-lists"' in overview.text


def test_import_lists_page_has_no_save_bar(app_client):
    """Status-only page (status: "readonly" in SETTINGS_SECTIONS), same as
    the existing OPDS section -- no save machinery to accidentally submit."""
    page = app_client.get("/settings/import-lists")
    assert "settings-save-bar" not in page.text
