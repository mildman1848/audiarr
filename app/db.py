"""SQLite bootstrap and explicit migration runner.

Migrations are plain SQL files in ``app/db_migrations/`` named
``NNN_description.sql`` where NNN is the target schema version. The
runner applies them in order, each inside its own transaction, and
updates the ``schema_version`` table — explicit, idempotent, and
testable (see tests/test_db_migrations.py).

Connections use WAL and enforced foreign keys. A module-level path
override lets tests point the DB at a tmp_path without env vars.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

log = logging.getLogger("audiarr.db")

SCHEMA_VERSION = 2  # keep in sync with the highest migration file

# Test hook: when set, init_db/migrate use this path instead of config.
_db_path_override: Path | None = None

_MIGRATION_NAME = re.compile(r"^(\d{3})_.+\.sql$")


def set_db_path_override(path: Path | None) -> None:
    """Point the DB at a specific file (used by tests)."""
    global _db_path_override
    _db_path_override = path


def get_db_path() -> Path:
    if _db_path_override is not None:
        return _db_path_override
    from app.config import get_config_dir

    return get_config_dir() / "audiarr.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migration_files() -> list[tuple[int, str]]:
    """Return (target_version, filename) pairs sorted by version."""
    found: list[tuple[int, str]] = []
    for traversable in resources.files("app.db_migrations").iterdir():
        name = traversable.name
        match = _MIGRATION_NAME.match(name)
        if match:
            found.append((int(match.group(1)), name))
    return sorted(found)


def current_version(conn: sqlite3.Connection) -> int:
    """Return the schema version recorded in the DB (0 if fresh)."""
    try:
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        # Fresh DB: schema_version table does not exist yet.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"] or 0)


def migrate(db_path: Path | None = None) -> int:
    """Apply pending migrations; return the resulting schema version."""
    path = db_path or get_db_path()
    conn = _connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        version = current_version(conn)

        for target, name in _migration_files():
            if target <= version:
                continue
            sql = (
                resources.files("app.db_migrations").joinpath(name).read_text("utf-8")
            )
            with conn:  # one transaction per migration
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
            log.info("Applied migration %s -> schema v%s", name, target)
            version = target

        if version < SCHEMA_VERSION:
            log.warning(
                "No migration file for schema v%s; DB stays at v%s", SCHEMA_VERSION, version
            )
        return version
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    """Compatibility wrapper used by app.main lifespan."""
    migrate(db_path)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a configured connection; commits on success, rolls back on error."""
    conn = _connect(get_db_path())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
