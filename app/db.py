"""Minimal SQLite bootstrap.

Audiarr's MVP does not yet store library data, but the schema-version
table below demonstrates the SQLite path so the library/book tables that
land in later milestones (see docs/parity-targets.md) have a migration
anchor to build on. Keeping this intentionally small avoids scaffolding an
ORM before there is real data to model.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("audiarr.db")

SCHEMA_VERSION = 1


def init_db(db_path: Path) -> None:
    """Create the database file and schema_version table if missing."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER NOT NULL"
            ")"
        )
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()
            log.info("Initialized new Audiarr database at %s (schema v%s)", db_path, SCHEMA_VERSION)
        else:
            log.info("Audiarr database at %s already at schema v%s", db_path, row[0])
    finally:
        conn.close()
