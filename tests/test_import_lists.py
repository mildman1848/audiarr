"""Unit tests for the generic import-list abstraction (issue #72).

Exercises app.import_lists directly against an isolated DB (same fixture
pattern as tests/test_importer.py), not through the HTTP layer -- see
tests/test_routes_import_lists.py for the route-level equivalents.
"""

from __future__ import annotations

import importlib

import pytest

from app.db import get_conn, migrate
from app.import_lists import (
    LIBERATARR_SOURCE_ID,
    ImportListSyncResult,
    get_source,
    list_sources,
    sync_liberatarr_rows,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated DB: AUDIARR_CONFIG_DIR points to tmp; migrations run on it."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    migrate()


def test_list_sources_returns_liberatarr(db):
    sources = list_sources()
    assert len(sources) == 1
    assert sources[0].id == LIBERATARR_SOURCE_ID
    assert sources[0].status == "unknown"


def test_get_source_returns_none_for_unknown_id(db):
    assert get_source("nope") is None


def test_get_source_finds_liberatarr(db):
    source = get_source(LIBERATARR_SOURCE_ID)
    assert source is not None
    assert source.type == "liberatarr"


def _row(asin, title="Some Title", status=0):
    return {"asin": asin, "title": title, "authors": [], "narrators": [], "status": status, "locale": ""}


def test_sync_liberatarr_rows_creates_books(db):
    with get_conn() as conn:
        result = sync_liberatarr_rows(conn, [_row("B001"), _row("B002")])
    assert result == ImportListSyncResult(created=2, skipped_existing=0, skipped_no_id=0, errors=[])


def test_sync_liberatarr_rows_is_idempotent(db):
    with get_conn() as conn:
        first = sync_liberatarr_rows(conn, [_row("B001")])
    assert first.created == 1

    with get_conn() as conn:
        second = sync_liberatarr_rows(conn, [_row("B001")])
    assert second.created == 0
    assert second.skipped_existing == 1


def test_sync_liberatarr_rows_skips_rows_without_asin(db):
    with get_conn() as conn:
        result = sync_liberatarr_rows(conn, [_row(""), _row("B001")])
    assert result.created == 1
    assert result.skipped_no_id == 1


def test_sync_liberatarr_rows_skips_already_liberated(db):
    with get_conn() as conn:
        result = sync_liberatarr_rows(conn, [_row("B001", status=1), _row("B002", status=0)])
    assert result.created == 1
