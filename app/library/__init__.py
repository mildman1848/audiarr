"""Library store: CRUD over the audiobook domain model.

All writes go through here so provider attribution and book↔author/
narrator joins stay consistent. Functions take an open connection so
callers control transaction scope (see db.get_conn()).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.db import get_conn as get_conn  # re-export for callers


@dataclass
class BookCreate:
    """Payload for creating a book with full attribution."""

    title: str
    subtitle: str = ""
    description: str = ""
    release_date: str | None = None
    language: str = ""
    publisher: str = ""
    duration_seconds: int = 0
    cover_url: str | None = None
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    series: str = ""
    series_position: float | None = None
    provider: str = ""
    provider_id: str = ""
    locale: str = ""


@dataclass
class RootFolderCreate:
    path: str
    label: str = ""


# -- root folders ------------------------------------------------------------


def create_root_folder(conn: sqlite3.Connection, data: RootFolderCreate) -> int:
    cur = conn.execute(
        "INSERT INTO root_folders (path, label) VALUES (?, ?)",
        (data.path, data.label),
    )
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def list_root_folders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, path, label, created_at, updated_at FROM root_folders ORDER BY path"
    ).fetchall()


def get_root_folder(conn: sqlite3.Connection, folder_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, path, label, created_at, updated_at FROM root_folders WHERE id = ?",
        (folder_id,),
    ).fetchone()


def delete_root_folder(conn: sqlite3.Connection, folder_id: int) -> bool:
    cur = conn.execute("DELETE FROM root_folders WHERE id = ?", (folder_id,))
    return cur.rowcount > 0


# -- helpers ------------------------------------------------------------------


def _upsert_person(
    conn: sqlite3.Connection, table: str, name: str, asin: str | None = None
) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
    if row:
        if asin:
            conn.execute(f"UPDATE {table} SET asin = ? WHERE id = ?", (asin, row["id"]))
        return int(row["id"])
    cur = conn.execute(f"INSERT INTO {table} (name, asin) VALUES (?, ?)", (name, asin))
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _upsert_series(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM series WHERE name = ?", (name,)).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO series (name) VALUES (?)", (name,))
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _add_provider_id(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    provider: str,
    provider_id: str,
    locale: str = "",
) -> None:
    if not provider or not provider_id:
        return
    conn.execute(
        "INSERT OR IGNORE INTO provider_ids (entity_type, entity_id, provider, provider_id, locale) "
        "VALUES (?, ?, ?, ?, ?)",
        (entity_type, entity_id, provider, provider_id, locale),
    )


# -- books --------------------------------------------------------------------


def create_book(conn: sqlite3.Connection, data: BookCreate) -> int:
    """Insert a book with authors/narrators/series and provider attribution."""
    series_id = _upsert_series(conn, data.series) if data.series else None

    cur = conn.execute(
        """INSERT INTO books
           (title, subtitle, description, release_date, language, publisher,
            duration_seconds, cover_url, series_id, series_position)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.title,
            data.subtitle,
            data.description,
            data.release_date,
            data.language,
            data.publisher,
            data.duration_seconds,
            data.cover_url,
            series_id,
            data.series_position,
        ),
    )
    assert cur.lastrowid is not None
    book_id = int(cur.lastrowid)

    for pos, author in enumerate(data.authors):
        author_id = _upsert_person(conn, "authors", author)
        conn.execute(
            "INSERT OR IGNORE INTO book_authors (book_id, author_id, position) VALUES (?, ?, ?)",
            (book_id, author_id, pos),
        )
        _add_provider_id(conn, "author", author_id, data.provider, "", data.locale)

    for pos, narrator in enumerate(data.narrators):
        narrator_id = _upsert_person(conn, "narrators", narrator)
        conn.execute(
            "INSERT OR IGNORE INTO book_narrators (book_id, narrator_id, position) VALUES (?, ?, ?)",
            (book_id, narrator_id, pos),
        )

    _add_provider_id(
        conn, "book", book_id, data.provider, data.provider_id, data.locale
    )
    return book_id


def get_book(conn: sqlite3.Connection, book_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT b.*, s.name AS series_name,
                  (SELECT GROUP_CONCAT(a.name, ', ')
                     FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id ORDER BY ba.position) AS authors,
                  (SELECT GROUP_CONCAT(n.name, ', ')
                     FROM book_narrators bn JOIN narrators n ON n.id = bn.narrator_id
                    WHERE bn.book_id = b.id ORDER BY bn.position) AS narrators
             FROM books b LEFT JOIN series s ON s.id = b.series_id
            WHERE b.id = ?""",
        (book_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_books(conn: sqlite3.Connection, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT b.id, b.title, b.subtitle, b.description, b.language,
                  b.duration_seconds, b.cover_url, b.release_date, b.publisher,
                  s.name AS series_name, b.series_position,
                  (SELECT GROUP_CONCAT(a.name, ', ')
                     FROM book_authors ba JOIN authors a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id ORDER BY ba.position) AS authors,
                  (SELECT GROUP_CONCAT(n.name, ', ')
                     FROM book_narrators bn JOIN narrators n ON n.id = bn.narrator_id
                    WHERE bn.book_id = b.id ORDER BY bn.position) AS narrators
             FROM books b LEFT JOIN series s ON s.id = b.series_id
            ORDER BY b.title LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def update_book(
    conn: sqlite3.Connection, book_id: int, updates: dict[str, Any]
) -> bool:
    allowed = {
        "title", "subtitle", "description", "release_date", "language",
        "publisher", "duration_seconds", "cover_url", "series_position",
    }
    fields = [k for k in updates if k in allowed and updates[k] is not None]
    if not fields:
        return False
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = [updates[k] for k in fields]
    values.append(book_id)
    cur = conn.execute(f"UPDATE books SET {sets}, updated_at = datetime('now') WHERE id = ?", values)
    return cur.rowcount > 0


def delete_book(conn: sqlite3.Connection, book_id: int) -> bool:
    cur = conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    return cur.rowcount > 0


def get_provider_ids(
    conn: sqlite3.Connection, entity_type: str, entity_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT provider, provider_id, locale FROM provider_ids "
        "WHERE entity_type = ? AND entity_id = ? ORDER BY provider, locale",
        (entity_type, entity_id),
    ).fetchall()
    return [dict(r) for r in rows]


def provider_id_exists(
    conn: sqlite3.Connection, provider: str, provider_id: str, locale: str = ""
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM provider_ids WHERE provider = ? AND provider_id = ? AND locale = ?",
        (provider, provider_id, locale),
    ).fetchone()
    return row is not None


def find_book_by_provider_id(
    conn: sqlite3.Connection, provider: str, provider_id: str, locale: str = ""
) -> int | None:
    row = conn.execute(
        "SELECT entity_id FROM provider_ids "
        "WHERE entity_type = 'book' AND provider = ? AND provider_id = ? AND locale = ?",
        (provider, provider_id, locale),
    ).fetchone()
    return int(row["entity_id"]) if row else None
