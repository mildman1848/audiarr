"""Migration runner tests: explicit, ordered, idempotent."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import SCHEMA_VERSION, migrate


def test_fresh_db_reaches_latest_schema(tmp_path: Path) -> None:
    db = tmp_path / "fresh.db"
    version = migrate(db)
    assert version == SCHEMA_VERSION == 3

    conn = sqlite3.connect(db)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    expected = {
        "schema_version", "root_folders", "authors", "narrators", "series",
        "books", "book_authors", "book_narrators", "editions", "provider_ids",
        "library_files", "import_jobs", "conversion_jobs",
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
    assert [r[0] for r in rows] == [1, 2, 3]


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


def test_v2_db_upgrades_to_v3(tmp_path: Path) -> None:
    """Simulate a v2 database: full v1+v2 migrations already applied."""
    db = tmp_path / "v2.db"
    migrate(db)  # reaches v3 on a fresh file; reset to v2 state instead:
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE conversion_jobs")
    conn.execute("DELETE FROM schema_version WHERE version = 3")
    conn.commit()
    conn.close()

    version = migrate(db)
    assert version == 3

    conn = sqlite3.connect(db)
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "conversion_jobs" in tables


def test_version_history_is_preserved(tmp_path: Path) -> None:
    db = tmp_path / "hist.db"
    migrate(db)
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    conn.close()
    assert [r[0] for r in rows] == [1, 2, 3]
