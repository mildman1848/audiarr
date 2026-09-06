"""Importer pipeline tests: scan → match → persist, dry-run semantics."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.db import get_conn, migrate
from app.library.importer import run_import
from app.providers.base import (
    BaseMetadataProvider,
    BookDetailInfo,
    BookQuickInfo,
    BrowseResponse,
    SearchResponse,
)
from app.providers.chain import ProviderChain, ProviderChainConfig


class StubProvider(BaseMetadataProvider):
    """Offline provider: returns canned hits for ASIN and keyword search."""

    provider_uid = "stub:1"
    provider_name = "Stub"

    CANNED = {
        "Der Vorleser": BookQuickInfo(
            provider_uid="stub:1",
            provider_name="Stub",
            title="Der Vorleser",
            authors=["Bernhard Schlink"],
            narrators=["Hans Korte"],
            series="",
            asin="B004UWRY6M",
            locale="de",
        ),
    }

    async def search(self, query: str, **kwargs) -> SearchResponse:
        results = [hit for title, hit in self.CANNED.items() if title in query or query in title]
        return SearchResponse(results=results, query_used=query)

    async def get_detail(self, external_id: str, **kwargs) -> BookDetailInfo | None:
        for hit in self.CANNED.values():
            if hit.asin == external_id:
                return BookDetailInfo(
                    provider_uid=hit.provider_uid,
                    provider_name=hit.provider_name,
                    provider_external_id=external_id,
                    title=hit.title,
                    authors=hit.authors,
                    narrators=hit.narrators,
                    asin=hit.asin,
                )
        return None

    async def browse(self, **kwargs) -> BrowseResponse:
        return BrowseResponse(results=list(self.CANNED.values()))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated DB: AUDIARR_CONFIG_DIR points to tmp; migrations run on it."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)

    migrate()
    with get_conn() as conn:
        yield conn


@pytest.fixture()
def chain():
    return ProviderChain(
        config=ProviderChainConfig(provider_order=["stub"]),
        provider_overrides={"stub": StubProvider()},
    )


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

def _make_tree(tmp_path: Path) -> Path:
    """Create a small audiobook tree: one German book with ASIN in name."""
    root = tmp_path / "audiobooks"
    book = root / "Bernhard Schlink - Der Vorleser [B004UWRY6M]"
    book.mkdir(parents=True)
    (book / "01 - Kapitel 1.mp3").write_bytes(b"\x00" * 1024)
    (book / "02 - Kapitel 2.mp3").write_bytes(b"\x00" * 2048)
    return root


async def test_run_import_dry_run_reports_no_writes(tmp_path, db, chain):
    root = _make_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(root),)
    ).lastrowid
    db.commit()

    summary = await run_import(
        conn=db, chain=chain, root_folder_id=folder_id, dry_run=True, locale="de"
    )

    assert summary.total_candidates == 1
    assert summary.matched == 1
    assert summary.results[0].status == "matched"
    assert summary.results[0].method == "asin"
    assert summary.results[0].matched_book_id is None
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0


async def test_run_import_persists_book_and_files(tmp_path, db, chain):
    root = _make_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(root),)
    ).lastrowid
    db.commit()

    summary = await run_import(
        conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de"
    )

    assert summary.matched == 1
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM library_files").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM import_jobs").fetchone()[0] == 1
    # Provider attribution present
    prov = db.execute("SELECT provider, provider_id FROM provider_ids").fetchone()
    assert prov["provider"] == "stub"  # provider_uid prefix
    assert prov["provider_id"] == "B004UWRY6M"


async def test_run_import_duplicate_provider_id_skips(tmp_path, db, chain):
    root = _make_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(root),)
    ).lastrowid
    db.commit()

    await run_import(conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de")
    summary = await run_import(conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de")

    assert summary.results[0].status == "skipped-duplicate"
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1


async def test_run_import_unmatched_reports_failure(tmp_path, db, chain):
    root = tmp_path / "audiobooks"
    (root / "Unknown Author - Unknown Title").mkdir(parents=True)
    (root / "Unknown Author - Unknown Title" / "track.mp3").write_bytes(b"\x00" * 10)
    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(root),)
    ).lastrowid
    db.commit()

    summary = await run_import(conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de")

    assert summary.unmatched == 1
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0  # nothing imported
    jobs = db.execute("SELECT status, error FROM import_jobs").fetchall()
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["error"] == "unmatched"
