"""Follows page + Book Detail follow buttons (#80)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_JS = Path(__file__).resolve().parent.parent / "app" / "web" / "static" / "js"


def _set_ui_language(app_client, language: str) -> None:
    current = app_client.get("/api/v1/settings").json()
    current["ui"]["language"] = language
    assert app_client.put("/api/v1/settings", json=current).status_code == 200


@pytest.mark.parametrize(
    ("language", "marker"),
    [("en", "Follow authors and series"), ("de", "Folge Autoren und Serien")],
)
def test_follows_page_renders(app_client, language, marker):
    _set_ui_language(app_client, language)
    page = app_client.get("/follows")
    assert page.status_code == 200
    assert marker in page.text
    for element_id in ("follows-list", "follows-candidates", "follows-add-btn",
                       "follows-add-selected-btn", "follows-exclude-selected-btn"):
        assert f'id="{element_id}"' in page.text
    assert "/static/js/follows.js" in page.text


def test_follows_nav_entry_active_only_on_follows(app_client):
    _set_ui_language(app_client, "en")

    def classes(html: str) -> list[str]:
        match = re.search(r'<a href="/follows" class="([^"]*)"', html)
        assert match, "no /follows nav link"
        return match.group(1).split()

    assert "active" in classes(app_client.get("/follows").text)
    assert "active" not in classes(app_client.get("/library").text)


def test_follows_script_is_served_and_uses_follows_api(app_client):
    script = app_client.get("/static/js/follows.js")
    assert script.status_code == 200
    for needle in ("/api/v1/follows", "/candidates/", "refresh", "badge"):
        assert needle in script.text


def test_book_detail_has_follow_buttons(app_client):
    page = app_client.get("/library/books/1")
    assert page.status_code == 200
    assert 'id="book-detail-follow-author-btn"' in page.text
    assert 'id="book-detail-follow-series-btn"' in page.text
    js = (STATIC_JS / "book_detail.js").read_text(encoding="utf-8")
    assert '"/api/v1/follows"' in js
