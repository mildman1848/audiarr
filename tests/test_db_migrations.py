"""Migration runner tests: explicit, ordered, idempotent."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import SCHEMA_VERSION, migrate


def test_fresh_db_reaches_latest_schema(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    version = migrate(db)
    assert version == SCHEMA_VERSION == 11

    conn = sqlite3.connect(db)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    expected = {
        "schema_version", "root_folders", "authors", "narrators", "series",
        "books", "book_authors", "book_narrators", "editions", "provider_ids",
        "library_files", "import_jobs", "conversion_jobs", "import_ignores",
        "sab_import_state", "wanted_search_state",
        "tags", "book_tags", "root_folder_tags",
    }
    assert expected <= tables


def test_fresh_db_books_have_asin_column(tmp_path: Path) -> None:
    db = tmp_path / "fresh_asin.db"
    migrate(db)

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(books)")}
    conn.close()
    assert "asin" in cols
    assert "idx_books_asin" in indexes


def test_fresh_db_books_have_monitored_column_defaulting_to_true(tmp_path: Path) -> None:
    db = tmp_path / "fresh_monitored.db"
    migrate(db)

    conn = sqlite3.connect(db)
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(books)")}
    assert "monitored" in cols
    # PRAGMA table_info row shape: (cid, name, type, notnull, dflt_value, pk)
    assert cols["monitored"][4] == "1"

    conn.execute(
        "INSERT INTO books (title) VALUES ('Untitled')"
    )
    conn.commit()
    monitored = conn.execute(
        "SELECT monitored FROM books WHERE title = 'Untitled'"
    ).fetchone()[0]
    conn.close()
    assert monitored == 1


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "idem.db"
    first = migrate(db)
    second = migrate(db)
    assert first == second == SCHEMA_VERSION

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    conn.close()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


def test_v1_db_upgrades_to_latest(tmp_path: Path) -> None:
    """Simulate a v1 database (only schema_version with version=1)."""
    db = tmp_path / "v1.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION

    conn = sqlite3.connect(db)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "books" in tables
    assert "provider_ids" in tables
    assert "conversion_jobs" in tables
    assert "import_ignores" in tables


def test_v3_db_upgrades_to_v4(tmp_path: Path) -> None:
    """Simulate a v3 database: conversion_jobs without webhook columns.

    Resetting schema_version below 4 replays migrations 4-9, so any column
    those later migrations add (not just migration 4's) must be dropped too,
    or the replayed ALTER TABLE ADD COLUMN fails as a duplicate.
    """
    db = tmp_path / "v3.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE conversion_jobs DROP COLUMN completed_path")
    conn.execute("ALTER TABLE conversion_jobs DROP COLUMN originals_deleted")
    conn.execute("ALTER TABLE books DROP COLUMN monitored")
    conn.execute("DROP INDEX idx_books_asin")
    conn.execute("ALTER TABLE books DROP COLUMN asin")
    conn.execute("ALTER TABLE books DROP COLUMN quality_profile")
    conn.execute("DELETE FROM schema_version WHERE version >= 4")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversion_jobs)")}
    book_cols = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
    conn.close()
    assert "completed_path" in cols
    assert "originals_deleted" in cols
    assert "monitored" in book_cols
    assert "asin" in book_cols
    assert "quality_profile" in book_cols


def test_v6_db_upgrades_to_v7(tmp_path: Path) -> None:
    """A v6 (prod-shaped) DB gains the asin column + index on upgrade.

    Resetting to version 6 replays migrations 7-9, so the quality_profile
    column migration 8 adds must be dropped too, or its ALTER TABLE ADD
    COLUMN fails as a duplicate.
    """
    db = tmp_path / "v6.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP INDEX idx_books_asin")
    conn.execute("ALTER TABLE books DROP COLUMN asin")
    conn.execute("ALTER TABLE books DROP COLUMN quality_profile")
    conn.execute("DELETE FROM schema_version WHERE version >= 7")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION == 11

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(books)")}
    conn.close()
    assert "asin" in cols
    assert "idx_books_asin" in indexes


def test_v7_db_upgrades_to_v8(tmp_path: Path) -> None:
    """A v7 (prod-shaped) DB gains the quality_profile column on upgrade."""
    db = tmp_path / "v7.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE books DROP COLUMN quality_profile")
    conn.execute("DELETE FROM schema_version WHERE version >= 8")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION == 11

    conn = sqlite3.connect(db)
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(books)")}
    conn.close()
    assert "quality_profile" in cols
    # PRAGMA table_info row shape: (cid, name, type, notnull, dflt_value, pk)
    assert cols["quality_profile"][4] == "''"


def test_v8_db_upgrades_to_v9(tmp_path: Path) -> None:
    """A v8 (prod-shaped) DB gains the sab_import_state table on upgrade."""
    db = tmp_path / "v8.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE sab_import_state")
    conn.execute("DROP TABLE wanted_search_state")
    conn.execute("DELETE FROM schema_version WHERE version >= 9")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION == 11

    conn = sqlite3.connect(db)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "sab_import_state" in tables


def test_v9_db_upgrades_to_v10(tmp_path: Path) -> None:
    """A v9 (prod-shaped) DB gains the wanted_search_state table on upgrade."""
    db = tmp_path / "v9.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE wanted_search_state")
    conn.execute("DELETE FROM schema_version WHERE version >= 10")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION == 11

    conn = sqlite3.connect(db)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "wanted_search_state" in tables


def test_v10_db_upgrades_to_v11(tmp_path: Path) -> None:
    """A v10 (prod-shaped) DB gains the tags tables on upgrade."""
    db = tmp_path / "v10.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE root_folder_tags")
    conn.execute("DROP TABLE book_tags")
    conn.execute("DROP TABLE tags")
    conn.execute("DELETE FROM schema_version WHERE version >= 11")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION == 11

    conn = sqlite3.connect(db)
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "tags" in tables
    assert "book_tags" in tables
    assert "root_folder_tags" in tables


def test_version_history_is_preserved(tmp_path: Path) -> None:
    db = tmp_path / "hist.db"
    migrate(db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    conn.close()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
