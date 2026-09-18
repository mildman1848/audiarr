"""Importer pipeline tests: scan → match → persist, dry-run semantics."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from app.config import load_settings, save_settings
from app.db import get_conn, migrate
from app.library import BookCreate, create_book
from app.library.importer import _maybe_enqueue_conversion, run_import
from app.library.scanner import BookCandidate
from app.models.settings import QualityProfile
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


# ---------------------------------------------------------------------------
# Quality-profile-driven conversion enqueue (#17)
# ---------------------------------------------------------------------------


async def test_default_profile_enqueues_mp3_source_for_conversion(tmp_path, db, chain, monkeypatch):
    """The mp3 tree's candidate.dominant_format is "mp3"; the default quality
    profile's top tier targets m4b, so should_convert_candidate says yes."""
    root = _make_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(root),)
    ).lastrowid
    db.commit()

    calls: list[tuple[int, str]] = []

    async def fake_enqueue(conn, book_id, source_path, output_path=""):
        calls.append((book_id, source_path))
        return 1

    monkeypatch.setattr("app.conversion.worker.enqueue_for_book", fake_enqueue)

    summary = await run_import(conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de")

    assert summary.matched == 1
    assert len(calls) == 1
    assert calls[0][1] == str(root / "Bernhard Schlink - Der Vorleser [B004UWRY6M]")


async def test_profile_not_targeting_m4b_skips_conversion_enqueue(tmp_path, db, chain, monkeypatch):
    """A quality profile whose top tier is an mp3 tier (not m4b) should never
    offer an mp3 source to the conversion backend."""
    root = _make_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(root),)
    ).lastrowid
    db.commit()

    settings = load_settings()
    settings.quality_profiles = [
        QualityProfile(
            name="MP3 shop",
            allowed_formats=["mp3"],
            cutoff_format="mp3",
            quality_ids=["mp3-320"],
            cutoff_quality_id="mp3-320",
        )
    ]
    save_settings(settings)

    calls: list[tuple[int, str]] = []

    async def fake_enqueue(conn, book_id, source_path, output_path=""):
        calls.append((book_id, source_path))
        return 1

    monkeypatch.setattr("app.conversion.worker.enqueue_for_book", fake_enqueue)

    summary = await run_import(conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de")

    assert summary.matched == 1
    assert calls == []


async def test_book_quality_profile_overrides_default_for_conversion_enqueue(db, monkeypatch):
    """Per-book quality_profile (#20) decides the conversion outcome: a book
    left on the default profile (targets m4b) enqueues an mp3 source, while a
    book pinned to a profile that doesn't target m4b does not."""
    settings = load_settings()
    settings.quality_profiles = [
        QualityProfile(name="Standard"),
        QualityProfile(
            name="MP3 shop",
            allowed_formats=["mp3"],
            cutoff_format="mp3",
            quality_ids=["mp3-320"],
            cutoff_quality_id="mp3-320",
        ),
    ]
    save_settings(settings)

    default_book_id = create_book(db, BookCreate(title="Book A"))
    mp3_book_id = create_book(db, BookCreate(title="Book B"))
    db.execute(
        "UPDATE books SET quality_profile = ? WHERE id = ?", ("MP3 shop", mp3_book_id)
    )
    db.commit()

    calls: list[int] = []

    async def fake_enqueue(conn, book_id, source_path, output_path=""):
        calls.append(book_id)
        return 1

    monkeypatch.setattr("app.conversion.worker.enqueue_for_book", fake_enqueue)

    candidate = BookCandidate(folder_path="/x", folder_name="x", dominant_format="mp3")
    await _maybe_enqueue_conversion(db, default_book_id, candidate)
    await _maybe_enqueue_conversion(db, mp3_book_id, candidate)

    assert calls == [default_book_id]
