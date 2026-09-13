"""Wanted/Missing API tests: monitored books with zero library files."""

from __future__ import annotations

from app.db import get_conn


def _create_book(app_client, **overrides) -> dict:
    payload = {
        "title": "Der Vorleser",
        "authors": ["Bernhard Schlink"],
        "language": "de",
        "provider": "audible",
        "provider_id": "B004UWRY6M",
        "locale": "de",
    }
    payload.update(overrides)
    resp = app_client.post("/api/v1/library/books", json=payload)
    assert resp.status_code == 201
    return resp.json()


def test_wanted_missing_empty_when_no_books(app_client):
    resp = app_client.get("/api/v1/wanted/missing")
    assert resp.status_code == 200
    assert resp.json() == []


def test_wanted_missing_includes_monitored_book_without_files(app_client):
    book = _create_book(app_client)

    resp = app_client.get("/api/v1/wanted/missing")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    row = data[0]
    assert row["id"] == book["id"]
    assert row["title"] == "Der Vorleser"
    assert row["authors"] == ["Bernhard Schlink"]
    assert row["monitored"] is True
    assert row["reason"] == "missingFiles"


def test_wanted_missing_excludes_unmonitored_books(app_client):
    _create_book(app_client, monitored=False)

    resp = app_client.get("/api/v1/wanted/missing")
    assert resp.status_code == 200
    assert resp.json() == []


def test_wanted_missing_excludes_books_with_library_files(app_client):
    book = _create_book(app_client)

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO editions (book_id, format, locale) VALUES (?, 'm4b', 'de')",
            (book["id"],),
        )
        edition_id = cur.lastrowid
        conn.execute(
            "INSERT INTO library_files (edition_id, path, size_bytes, format) "
            "VALUES (?, ?, ?, 'm4b')",
            (edition_id, "/data/book/part1.m4b", 1000),
        )

    resp = app_client.get("/api/v1/wanted/missing")
    assert resp.status_code == 200
    assert resp.json() == []
