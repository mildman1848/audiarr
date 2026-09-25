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


async def test_run_import_dispatches_import_event_on_match(tmp_path, db, chain, monkeypatch):
    """Issue #28: a matched (non-dry-run) import fires a best-effort
    `import` Connect event carrying book_id/title/source_path."""
    root = _make_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(root),)
    ).lastrowid
    db.commit()

    calls: list[tuple[str, dict]] = []

    async def fake_dispatch(event, payload):
        calls.append((event, payload))

    monkeypatch.setattr("app.library.importer.dispatch_event", fake_dispatch)

    summary = await run_import(conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de")

    assert summary.matched == 1
    assert len(calls) == 1
    event, payload = calls[0]
    assert event == "import"
    assert payload["title"] == "Der Vorleser"
    assert payload["status"] == "matched"
    assert payload["source_path"] == str(root / "Bernhard Schlink - Der Vorleser [B004UWRY6M]")


async def test_run_import_dry_run_does_not_dispatch(tmp_path, db, chain, monkeypatch):
    root = _make_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path) VALUES (?)", (str(root),)
    ).lastrowid
    db.commit()

    calls: list[tuple[str, dict]] = []

    async def fake_dispatch(event, payload):
        calls.append((event, payload))

    monkeypatch.setattr("app.library.importer.dispatch_event", fake_dispatch)

    await run_import(conn=db, chain=chain, root_folder_id=folder_id, dry_run=True, locale="de")

    assert calls == []


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


# ---------------------------------------------------------------------------
# Import strategies (#30): move/copy/hardlink materialization on import
# ---------------------------------------------------------------------------


def _make_nested_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Build a root folder with the book's audio files loose at the root itself.

    scan_folder groups top-level loose audio files (no book subfolder) as one
    candidate for the root itself (see scan_folder's docstring), whose
    candidate_folder_name is the root's own directory name. resolve_target_path
    then lands files under root/<root name>/file -- a genuinely different
    final path from the loose source files directly in root -- letting these
    tests exercise real copy/move/hardlink placement (as opposed to the
    "already in place" no-op case covered by tests/test_import_strategy.py).
    """
    root = tmp_path / "Der Vorleser [B004UWRY6M]"
    root.mkdir(parents=True)
    (root / "01 - Kapitel 1.mp3").write_bytes(b"\x00" * 1024)
    (root / "02 - Kapitel 2.mp3").write_bytes(b"\x00" * 2048)
    return root, root


async def test_run_import_copy_strategy_persists_final_absolute_paths(tmp_path, db, chain):
    root, source_book = _make_nested_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path, import_strategy) VALUES (?, 'copy')",
        (str(root),),
    ).lastrowid
    db.commit()

    summary = await run_import(
        conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de"
    )

    assert summary.matched == 1
    paths = [
        r["path"] for r in db.execute("SELECT path FROM library_files").fetchall()
    ]
    assert len(paths) == 2
    for p in paths:
        assert Path(p).is_absolute()
        assert Path(p).exists()
        # Flattened one level up from the nested "Author/Book" source layout.
        assert Path(p).parent == root / "Der Vorleser [B004UWRY6M]"
    # Source files are untouched by a copy strategy.
    assert (source_book / "01 - Kapitel 1.mp3").exists()
    assert (source_book / "02 - Kapitel 2.mp3").exists()


async def test_run_import_hardlink_falls_back_to_copy_on_exdev(tmp_path, db, chain, monkeypatch):
    root, source_book = _make_nested_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path, import_strategy) VALUES (?, 'hardlink')",
        (str(root),),
    ).lastrowid
    db.commit()

    import os

    def fake_link(source, target):
        raise OSError(getattr(os, "EXDEV", 18), "Invalid cross-device link")

    monkeypatch.setattr(os, "link", fake_link)

    summary = await run_import(
        conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de"
    )

    assert summary.matched == 1
    paths = [
        r["path"] for r in db.execute("SELECT path FROM library_files").fetchall()
    ]
    assert len(paths) == 2
    for p in paths:
        assert Path(p).exists()
        assert Path(p).parent == root / "Der Vorleser [B004UWRY6M]"
    # Source untouched -- copy fallback never deletes the original.
    assert (source_book / "01 - Kapitel 1.mp3").exists()
    assert (source_book / "02 - Kapitel 2.mp3").exists()


async def test_run_import_space_check_failure_aborts_candidate_as_error(
    tmp_path, db, chain, monkeypatch
):
    root, source_book = _make_nested_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path, import_strategy) VALUES (?, 'copy')",
        (str(root),),
    ).lastrowid
    db.commit()

    import shutil as shutil_module

    class FakeUsage:
        free = 1

    monkeypatch.setattr(shutil_module, "disk_usage", lambda path: FakeUsage())

    summary = await run_import(
        conn=db, chain=chain, root_folder_id=folder_id, dry_run=False, locale="de"
    )

    assert summary.errors == 1
    assert summary.matched == 0
    assert summary.results[0].status == "error"
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM library_files").fetchone()[0] == 0
    assert not (root / "Der Vorleser [B004UWRY6M]").exists()
    # Source untouched.
    assert (source_book / "01 - Kapitel 1.mp3").exists()
    jobs = db.execute("SELECT status, error FROM import_jobs").fetchall()
    assert jobs[0]["status"] == "failed"
    assert jobs[0]["error"] == "error"


async def test_run_import_dry_run_with_move_strategy_makes_no_fs_writes(
    tmp_path, db, chain
):
    root, source_book = _make_nested_tree(tmp_path)
    folder_id = db.execute(
        "INSERT INTO root_folders (path, import_strategy) VALUES (?, 'move')",
        (str(root),),
    ).lastrowid
    db.commit()

    summary = await run_import(
        conn=db, chain=chain, root_folder_id=folder_id, dry_run=True, locale="de"
    )

    assert summary.matched == 1
    # A "move" strategy dry-run must never touch the source or write a target.
    assert (source_book / "01 - Kapitel 1.mp3").exists()
    assert (source_book / "02 - Kapitel 2.mp3").exists()
    assert not (root / "Der Vorleser [B004UWRY6M]").exists()
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM library_files").fetchone()[0] == 0


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
