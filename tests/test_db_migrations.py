"""Migration runner tests: explicit, ordered, idempotent."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import SCHEMA_VERSION, migrate


def test_fresh_db_reaches_latest_schema(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    version = migrate(db)
    assert version == SCHEMA_VERSION == 7

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
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6, 7]


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

    Resetting schema_version below 4 replays migrations 4-7, so any column
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


def test_v6_db_upgrades_to_v7(tmp_path: Path) -> None:
    """A v6 (prod-shaped) DB gains the asin column + index on upgrade."""
    db = tmp_path / "v6.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("DROP INDEX idx_books_asin")
    conn.execute("ALTER TABLE books DROP COLUMN asin")
    conn.execute("DELETE FROM schema_version WHERE version >= 7")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION == 7

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(books)")}
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(books)")}
    conn.close()
    assert "asin" in cols
    assert "idx_books_asin" in indexes


def test_version_history_is_preserved(tmp_path: Path) -> None:
    db = tmp_path / "hist.db"
    migrate(db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    conn.close()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5, 6, 7]
