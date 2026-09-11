"""Import Problems workflow tests: unmatched list, manual match, ignores.

Offline only — no real providers touched. Chain endpoints are exercised
either directly (run_import) or through the HTTP API (app_client), with
the provider chain swapped in via ``ProviderChain(provider_overrides=...)``
and (for the HTTP tests) monkeypatching ``routes_import._chain``.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import app.api.routes_import as routes_import
from app.db import get_conn, migrate
from app.library import BookCreate, create_book
from app.library.importer import run_import
from app.providers.base import (
    BaseMetadataProvider,
    BookDetailInfo,
    BrowseResponse,
    SearchResponse,
)
from app.providers.chain import ProviderChain, ProviderChainConfig


class StubProvider(BaseMetadataProvider):
    """Offline provider: resolves a single canned ASIN, never searches."""

    provider_name = "Stub"

    def __init__(self, detail: BookDetailInfo | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.detail = detail

    async def search(self, query: str, **kwargs) -> SearchResponse:
        return SearchResponse(results=[], query_used=query)

    async def get_detail(self, external_id: str, **kwargs) -> BookDetailInfo | None:
        if self.detail is not None and self.detail.asin == external_id:
            return self.detail
        return None

    async def browse(self, **kwargs) -> BrowseResponse:
        return BrowseResponse(results=[])


def _chain_for(detail: BookDetailInfo | None) -> ProviderChain:
    return ProviderChain(
        config=ProviderChainConfig(provider_order=["stub"]),
        provider_overrides={"stub": StubProvider(detail=detail)},
    )


def _make_folder(tmp_path: Path, name: str, files: list[str]) -> Path:
    root = tmp_path / "audiobooks"
    folder = root / name
    folder.mkdir(parents=True)
    for i, fname in enumerate(files):
        (folder / fname).write_bytes(b"\x00" * (1024 * (i + 1)))
    return folder


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated DB for direct importer-level tests (no HTTP)."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    migrate()
    with get_conn() as conn:
        yield conn


# ---------------------------------------------------------------------------
# 1. run_import: ignored candidates
# ---------------------------------------------------------------------------


async def test_run_import_marks_ignored_and_excludes_from_unmatched(tmp_path, db):
    folder = tmp_path / "audiobooks" / "Unknown Author - Unknown Title"
    folder.mkdir(parents=True)
    (folder / "track.mp3").write_bytes(b"\x00" * 10)

    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(folder.parent),)
    ).lastrowid
    db.execute("INSERT INTO import_ignores (path) VALUES (?)", (str(folder),))
    db.commit()

    summary = await run_import(
        conn=db, chain=_chain_for(None), root_folder_id=folder_id, dry_run=False, locale="de"
    )

    assert summary.total_candidates == 1
    assert summary.unmatched == 0
    assert summary.ignored == 1
    assert summary.results[0].status == "ignored"
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 2. GET /unmatched
# ---------------------------------------------------------------------------


def test_unmatched_endpoint(app_client):
    with get_conn() as conn:
        # A: single failed/unmatched job -> must appear.
        conn.execute(
            "INSERT INTO import_jobs (source_path, status, error) VALUES (?, 'failed', 'unmatched')",
            ("/audiobooks/Unknown Author - Unknown Title",),
        )
        # B: failed/unmatched, then later completed -> latest wins, must vanish.
        conn.execute(
            "INSERT INTO import_jobs (source_path, status, error) VALUES (?, 'failed', 'unmatched')",
            ("/audiobooks/Retried Book",),
        )
        conn.execute(
            "INSERT INTO import_jobs (source_path, status, error) VALUES (?, 'completed', NULL)",
            ("/audiobooks/Retried Book",),
        )
        # C: failed/unmatched but explicitly ignored -> excluded.
        conn.execute(
            "INSERT INTO import_jobs (source_path, status, error) VALUES (?, 'failed', 'unmatched')",
            ("/audiobooks/Ignored Book",),
        )
        conn.execute(
            "INSERT INTO import_ignores (path) VALUES (?)", ("/audiobooks/Ignored Book",)
        )

    response = app_client.get("/api/v1/import/unmatched")
    assert response.status_code == 200
    body = response.json()
    paths = {row["folder_path"] for row in body}
    assert paths == {"/audiobooks/Unknown Author - Unknown Title"}

    row = next(r for r in body if r["folder_path"] == "/audiobooks/Unknown Author - Unknown Title")
    assert row["guessed_title"] == "Unknown Title"
    assert row["guessed_author"] == "Unknown Author"
    assert row["exists"] is False


# ---------------------------------------------------------------------------
# 3-5. POST /match
# ---------------------------------------------------------------------------


def test_match_endpoint_happy_path(app_client, tmp_path, monkeypatch):
    folder = _make_folder(tmp_path, "Some Random Folder Name", ["01 - track.mp3"])
    detail = BookDetailInfo(
        provider_uid="stub:1",
        provider_name="Stub",
        provider_external_id="B0TESTBOOK1",
        title="Der Vorleser",
        authors=["Bernhard Schlink"],
        narrators=["Hans Korte"],
        asin="B0TESTBOOK1",
    )
    monkeypatch.setattr(routes_import, "_chain", lambda: _chain_for(detail))

    response = app_client.post(
        "/api/v1/import/match",
        json={"folder_path": str(folder), "asin": "B0TESTBOOK1", "locale": "de"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "matched"
    assert body["asin"] == "B0TESTBOOK1"
    assert body["title"] == "Der Vorleser"
    assert body["book_id"] is not None

    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM library_files").fetchone()[0] == 1
        job = conn.execute(
            "SELECT status FROM import_jobs WHERE source_path = ?", (str(folder),)
        ).fetchone()
        assert job["status"] == "completed"


def test_match_endpoint_duplicate_provider_id(app_client, tmp_path, monkeypatch):
    folder = _make_folder(tmp_path, "Duplicate Folder", ["01 - track.mp3"])
    with get_conn() as conn:
        existing_book_id = create_book(
            conn,
            BookCreate(
                title="Already Imported",
                authors=["Someone"],
                provider="stub",
                provider_id="B0DUPTEST1",
                locale="de",
            ),
        )

    detail = BookDetailInfo(
        provider_uid="stub:1",
        provider_name="Stub",
        provider_external_id="B0DUPTEST1",
        title="Already Imported",
        authors=["Someone"],
        asin="B0DUPTEST1",
    )
    monkeypatch.setattr(routes_import, "_chain", lambda: _chain_for(detail))

    response = app_client.post(
        "/api/v1/import/match",
        json={"folder_path": str(folder), "asin": "B0DUPTEST1", "locale": "de"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "skipped-duplicate"
    assert body["book_id"] == existing_book_id

    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1  # no new book


def test_match_endpoint_folder_gone_returns_404(app_client, tmp_path, monkeypatch):
    missing = tmp_path / "audiobooks" / "Does Not Exist"
    monkeypatch.setattr(routes_import, "_chain", lambda: _chain_for(None))

    response = app_client.post(
        "/api/v1/import/match",
        json={"folder_path": str(missing), "asin": "B0MISSING1", "locale": "de"},
    )
    assert response.status_code == 404
    assert "detail" in response.json()


# ---------------------------------------------------------------------------
# 6. ignore / unignore
# ---------------------------------------------------------------------------


def test_ignore_unignore_and_list(app_client):
    path = "/audiobooks/Some Folder"

    r1 = app_client.post("/api/v1/import/ignore", json={"path": path, "note": "not a book"})
    assert r1.status_code == 204

    r2 = app_client.post("/api/v1/import/ignore", json={"path": path})  # idempotent
    assert r2.status_code == 204

    listing = app_client.get("/api/v1/import/ignores").json()
    assert len(listing) == 1
    assert listing[0]["path"] == path
    assert listing[0]["note"] == "not a book"

    r3 = app_client.post("/api/v1/import/unignore", json={"path": path})
    assert r3.status_code == 204

    assert app_client.get("/api/v1/import/ignores").json() == []
