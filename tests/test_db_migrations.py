"""Migration runner tests: explicit, ordered, idempotent."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import SCHEMA_VERSION, migrate


def test_fresh_db_reaches_latest_schema(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    version = migrate(db)
    assert version == SCHEMA_VERSION == 5

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


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "idem.db"
    first = migrate(db)
    second = migrate(db)
    assert first == second == SCHEMA_VERSION

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    conn.close()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5]


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
    """Simulate a v3 database: conversion_jobs without webhook columns."""
    db = tmp_path / "v3.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE conversion_jobs DROP COLUMN completed_path")
    conn.execute("ALTER TABLE conversion_jobs DROP COLUMN originals_deleted")
    conn.execute("DELETE FROM schema_version WHERE version >= 4")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == SCHEMA_VERSION

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(conversion_jobs)")}
    conn.close()
    assert "completed_path" in cols
    assert "originals_deleted" in cols


def test_version_history_is_preserved(tmp_path: Path) -> None:
    db = tmp_path / "hist.db"
    migrate(db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    conn.close()
    assert [r[0] for r in rows] == [1, 2, 3, 4, 5]
