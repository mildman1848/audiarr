"""qBittorrent completed-download import (issue #81).

Offline: the qBittorrent client is a fake, the provider chain a stub, and the
real import pipeline (importer / scanner / import strategy) runs on tmp files.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path

import pytest

from app.connections.qbittorrent import QBittorrentError
from app.db import get_conn, migrate
from app.models.settings import DownloadClient, RemotePathMapping
from app.providers.chain import ProviderChain, ProviderChainConfig
from app.sab_auto_import import SabAutoImportScheduler
from tests.test_sab_auto_import import StubProvider, StubSabClient

HASH = "a" * 40
HASH2 = "b" * 40


class FakeQb:
    """Drop-in QBittorrentClient returning canned completed torrents."""

    ITEMS: list[dict] = []
    RAISE: Exception | None = None
    CALLS: list[dict] = []

    def __init__(self, *, base_url="", api_key=None, username=None, password=None, client=None):
        FakeQb.CALLS.append({"base_url": base_url, "api_key": api_key})

    async def list_completed(self, category="", tag=""):
        FakeQb.CALLS[-1].update(category=category, tag=tag)
        if FakeQb.RAISE is not None:
            raise FakeQb.RAISE
        return FakeQb.ITEMS

    # Safety net: the importer must never call anything destructive.
    def __getattr__(self, name):
        raise AssertionError(f"unexpected qBittorrent call: {name}")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    importlib.reload(config_module)
    migrate()
    FakeQb.ITEMS, FakeQb.RAISE, FakeQb.CALLS = [], None, []
    with get_conn() as conn:
        for base in ("downloads", "local-complete"):
            conn.execute("INSERT INTO root_folders (path) VALUES (?)", (str(tmp_path / base),))
        conn.commit()
        yield conn


@pytest.fixture()
def qb(monkeypatch):
    client = DownloadClient(
        name="qBittorrent", type="qbittorrent", url="http://qb.local", api_key="qb-secret",
        category="audiobooks", tag="audiarr", enabled=True,
    )
    monkeypatch.setattr("app.sab_auto_import._enabled_qbittorrent", lambda: client)
    monkeypatch.setattr("app.sab_auto_import.QBittorrentClient", FakeQb)
    return client


@pytest.fixture()
def stub_chain(monkeypatch):
    chain = ProviderChain(
        config=ProviderChainConfig(provider_order=["stub"]),
        provider_overrides={"stub": StubProvider()},
    )
    monkeypatch.setattr("app.sab_auto_import.build_provider_chain", lambda: chain)
    return chain


def _book_folder(tmp_path: Path, base: str = "downloads") -> Path:
    book = tmp_path / base / "Bernhard Schlink - Der Vorleser [B004UWRY6M]"
    book.mkdir(parents=True)
    (book / "01 - Kapitel 1.mp3").write_bytes(b"\x00" * 1024)
    return book


def _item(content_path, **overrides) -> dict:
    item = {
        "hash": HASH, "name": "Der Vorleser", "category": "audiobooks", "tags": ["audiarr"],
        "state": "stalledUP", "progress": 1, "save_path": str(Path(str(content_path)).parent),
        "content_path": str(content_path),
    }
    item.update(overrides)
    return item


def _state(db, item_id=HASH):
    return db.execute(
        "SELECT status, reason, folder_path, book_id FROM download_client_import_state "
        "WHERE client = 'qbittorrent' AND item_id = ?", (item_id,),
    ).fetchone()


def _assert_retry(row, reason_part):
    assert row["status"] == "failed" and row["reason"].startswith("retry: ")
    assert reason_part in row["reason"]


async def test_completed_torrent_imports_once_via_shared_importer(tmp_path, db, stub_chain, qb):
    folder = _book_folder(tmp_path)
    FakeQb.ITEMS = [_item(folder)]

    scheduler = SabAutoImportScheduler()
    assert await scheduler.run_once() is True

    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    state = _state(db)
    assert state["status"] == "imported" and state["book_id"] is not None
    # Completion query was narrowed to the client's fixed category and tag.
    assert FakeQb.CALLS[0]["category"] == "audiobooks" and FakeQb.CALLS[0]["tag"] == "audiarr"

    # Same hash on the next tick imports nothing again.
    await scheduler.run_once()
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM download_client_import_state").fetchone()[0] == 1
    assert folder.is_dir() and (folder / "01 - Kapitel 1.mp3").exists()  # source untouched


async def test_import_reuses_shared_importer_entry_points(tmp_path, db, stub_chain, qb, monkeypatch):
    folder = _book_folder(tmp_path)
    FakeQb.ITEMS = [_item(folder)]
    calls = []
    import app.sab_auto_import as mod

    real = mod.import_single_folder

    async def spy(conn, chain, folder_path, asin, locale):
        calls.append((folder_path, asin))
        return await real(conn, chain, folder_path, asin, locale)

    monkeypatch.setattr(mod, "import_single_folder", spy)
    await SabAutoImportScheduler().run_once()
    assert calls == [(str(folder), "B004UWRY6M")]


async def test_hash_dedupe_does_not_collide_with_sab_ids(tmp_path, db, stub_chain, qb):
    folder = _book_folder(tmp_path)
    db.execute("INSERT INTO sab_import_state (nzo_key, status) VALUES (?, 'imported')", (HASH,))
    db.commit()
    FakeQb.ITEMS = [_item(folder)]
    await SabAutoImportScheduler().run_once()
    assert _state(db)["status"] == "imported"  # a SAB row with the same key did not suppress it
    assert db.execute("SELECT COUNT(*) FROM sab_import_state").fetchone()[0] == 1


async def test_remote_path_mapping_applied_before_filesystem_probe(
    tmp_path, db, stub_chain, qb, monkeypatch
):
    local = _book_folder(tmp_path, "local-complete")
    remote = "/qb/complete/" + local.name
    FakeQb.ITEMS = [_item(remote)]
    monkeypatch.setattr(
        "app.sab_auto_import.load_settings",
        _settings_with_mapping("/qb/complete", str(tmp_path / "local-complete")),
    )
    probed: list[str] = []
    real_is_dir = Path.is_dir
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_dir", lambda self: (probed.append(str(self)), real_is_dir(self))[1])
    monkeypatch.setattr(Path, "is_file", lambda self: (probed.append(str(self)), real_is_file(self))[1])

    await SabAutoImportScheduler().run_once()

    assert not any(p.startswith("/qb/") for p in probed)  # the remote path was never probed
    assert _state(db)["status"] == "imported"
    assert _state(db)["folder_path"] == str(local)


def _settings_with_mapping(remote: str, local: str):
    from app.config import load_settings as real_load

    def load():
        settings = real_load()
        settings.remote_path_mappings = [RemotePathMapping(id="m", remote_path=remote, local_path=local)]
        return settings

    return load


async def test_unmapped_remote_path_is_skipped_not_probed_as_local(tmp_path, db, stub_chain, qb):
    FakeQb.ITEMS = [_item("/qb/complete/Nowhere")]
    await SabAutoImportScheduler().run_once()
    _assert_retry(_state(db), "root folder")


async def test_single_file_torrent_is_skipped_with_reason(tmp_path, db, stub_chain, qb):
    shared = tmp_path / "downloads"
    shared.mkdir()
    single = shared / "Der Vorleser [B004UWRY6M].m4b"
    single.write_bytes(b"\x00" * 64)
    FakeQb.ITEMS = [_item(single)]
    await SabAutoImportScheduler().run_once()

    state = _state(db)
    assert state["status"] == "skipped" and "single-file" in state["reason"]
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
    assert single.exists()


@pytest.mark.parametrize("content_path", ["relative/path", "/downloads/../etc"])
async def test_missing_or_unsafe_content_path_is_recorded_skip(
    tmp_path, db, stub_chain, qb, content_path
):
    FakeQb.ITEMS = [_item(content_path)]
    await SabAutoImportScheduler().run_once()
    assert _state(db)["status"] == "skipped"


async def test_missing_content_path_is_retryable(tmp_path, db, stub_chain, qb):
    folder = _book_folder(tmp_path)
    FakeQb.ITEMS = [_item("")]
    scheduler = SabAutoImportScheduler()

    await scheduler.run_once()
    _assert_retry(_state(db), "no content path")

    FakeQb.ITEMS = [_item(folder)]
    await scheduler.run_once()
    assert _state(db)["status"] == "imported"


async def test_failures_are_contained_per_item(tmp_path, db, stub_chain, qb, monkeypatch):
    good = _book_folder(tmp_path)
    FakeQb.ITEMS = [_item(tmp_path / "gone", hash=HASH2), _item(good)]
    import app.sab_auto_import as mod

    real = mod._import_folder
    boom = {"n": 0}

    async def flaky(conn, chain, folder_path, name, locale):
        boom["n"] += 1
        if boom["n"] == 1:
            raise RuntimeError("importer exploded")
        return await real(conn, chain, folder_path, name, locale)

    # Item 1 is skipped before import (missing path); item 2's import raises.
    # Both are recorded and the tick still completes.
    monkeypatch.setattr(mod, "_import_folder", flaky)
    await SabAutoImportScheduler().run_once()

    _assert_retry(_state(db, HASH2), "root folder")
    _assert_retry(_state(db, HASH), "unexpected error during import")


async def test_listing_failure_does_not_abort_tick_or_block_sab(
    tmp_path, db, stub_chain, qb, monkeypatch, caplog
):
    FakeQb.RAISE = QBittorrentError("auth")
    folder = _book_folder(tmp_path)
    from app.connections.sabnzbd import SABnzbdClient  # noqa: F401

    sab = DownloadClient(
        name="SAB", type="sabnzbd", url="http://sab.local", category="audiobooks", enabled=True
    )
    monkeypatch.setattr("app.sab_auto_import._enabled_sabnzbd", lambda: sab)
    monkeypatch.setattr("app.sab_auto_import.SABnzbdClient", StubSabClient)
    StubSabClient.HISTORY = [{
        "nzo_id": "nzo_1", "name": folder.name, "status": "Completed", "category": "audiobooks",
        "storage": str(folder),
    }]
    caplog.set_level(logging.DEBUG)

    assert await SabAutoImportScheduler().run_once() is True
    sab_row = db.execute("SELECT status FROM sab_import_state WHERE nzo_key='nzo_1'").fetchone()
    assert sab_row["status"] == "imported"
    assert db.execute("SELECT COUNT(*) FROM download_client_import_state").fetchone()[0] == 0
    assert "qb-secret" not in caplog.text


async def test_unexpected_client_exception_is_contained(tmp_path, db, stub_chain, qb):
    FakeQb.RAISE = RuntimeError("boom")
    assert await SabAutoImportScheduler().run_once() is True


async def test_no_qbittorrent_configured_is_noop(tmp_path, db, stub_chain, monkeypatch):
    monkeypatch.setattr("app.sab_auto_import._enabled_qbittorrent", lambda: None)
    monkeypatch.setattr("app.sab_auto_import.QBittorrentClient", FakeQb)
    assert await SabAutoImportScheduler().run_once() is True
    assert FakeQb.CALLS == []


async def test_importer_only_reads_never_mutates_the_torrent(tmp_path, db, stub_chain, qb):
    """FakeQb raises on any attribute other than list_completed, so a delete/
    pause/stop call anywhere in the import path fails this test."""
    folder = _book_folder(tmp_path)
    FakeQb.ITEMS = [_item(folder)]
    await SabAutoImportScheduler().run_once()
    assert _state(db)["status"] == "imported"


def _forbid_probes(monkeypatch):
    """Fail the test on any scanner, importer or is_dir/is_file probe."""
    import app.sab_auto_import as mod

    def boom(*a, **k):
        raise AssertionError("filesystem/import probe before containment check")

    for name in ("scan_folder", "import_single_folder", "run_import", "_import_folder"):
        monkeypatch.setattr(mod, name, boom)
    real_is_dir, real_is_file = Path.is_dir, Path.is_file

    def guarded(real):
        def check(self, *a, **k):
            # Unrelated config code may probe its own dirs; content paths must not be probed.
            if "elsewhere" in str(self) or self.parent.name == "downloads" or self.name == "downloads":
                boom()
            return real(self, *a, **k)
        return check

    monkeypatch.setattr(Path, "is_dir", guarded(real_is_dir))
    monkeypatch.setattr(Path, "is_file", guarded(real_is_file))


async def test_asin_folder_outside_root_folders_is_retryable_without_probe(
    tmp_path, db, stub_chain, qb, monkeypatch
):
    outside = _book_folder(tmp_path, "elsewhere")  # ASIN-bearing, in no root folder
    FakeQb.ITEMS = [_item(outside)]
    _forbid_probes(monkeypatch)
    await SabAutoImportScheduler().run_once()
    _assert_retry(_state(db), "root folder")
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0


async def test_symlink_escape_from_root_folder_is_skipped_without_probe(
    tmp_path, db, stub_chain, qb, monkeypatch
):
    outside = _book_folder(tmp_path, "elsewhere")
    link = tmp_path / "downloads" / "innocent"
    link.parent.mkdir(exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    FakeQb.ITEMS = [_item(link)]
    _forbid_probes(monkeypatch)
    await SabAutoImportScheduler().run_once()
    _assert_retry(_state(db), "root folder")
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0


async def test_root_folder_itself_is_not_importable(tmp_path, db, stub_chain, qb, monkeypatch):
    (tmp_path / "downloads").mkdir()
    FakeQb.ITEMS = [_item(tmp_path / "downloads")]
    _forbid_probes(monkeypatch)
    await SabAutoImportScheduler().run_once()
    _assert_retry(_state(db), "root folder")


@pytest.mark.parametrize("category,tag", [("audiobooks", ""), ("audiobooks", "  "), ("", "audiarr")])
async def test_blank_tag_or_category_fails_closed(tmp_path, db, stub_chain, qb, category, tag):
    qb.category, qb.tag = category, tag
    FakeQb.ITEMS = [_item(_book_folder(tmp_path))]
    assert await SabAutoImportScheduler().run_once() is True
    assert all("tag" not in c for c in FakeQb.CALLS)  # list_completed never called
    assert db.execute("SELECT COUNT(*) FROM download_client_import_state").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0


# --- retryable states -------------------------------------------------------


def _row_count(db):
    return db.execute("SELECT COUNT(*) FROM download_client_import_state").fetchone()[0]


async def test_not_yet_mounted_path_is_retried_and_imported_once_it_appears(
    tmp_path, db, stub_chain, qb
):
    folder = tmp_path / "downloads" / "Bernhard Schlink - Der Vorleser [B004UWRY6M]"
    FakeQb.ITEMS = [_item(folder)]
    scheduler = SabAutoImportScheduler()

    await scheduler.run_once()
    await scheduler.run_once()  # still missing: same single row, no duplicates
    _assert_retry(_state(db), "path not found")
    assert _row_count(db) == 1

    _book_folder(tmp_path)  # the volume shows up
    await scheduler.run_once()
    assert _state(db)["status"] == "imported" and _row_count(db) == 1
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1

    await scheduler.run_once()  # imported stays deduped
    assert _row_count(db) == 1 and db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1


async def test_transient_error_is_retried_on_next_tick(tmp_path, db, stub_chain, qb, monkeypatch):
    folder = _book_folder(tmp_path)
    FakeQb.ITEMS = [_item(folder)]
    import app.sab_auto_import as mod

    real = mod._import_folder
    calls = {"n": 0}

    async def flaky(conn, chain, folder_path, name, locale):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("provider timeout")
        return await real(conn, chain, folder_path, name, locale)

    monkeypatch.setattr(mod, "_import_folder", flaky)
    scheduler = SabAutoImportScheduler()
    await scheduler.run_once()
    _assert_retry(_state(db), "unexpected error")
    await scheduler.run_once()
    assert _state(db)["status"] == "imported" and _row_count(db) == 1


async def test_single_file_and_unsafe_path_stay_terminal(tmp_path, db, stub_chain, qb, monkeypatch):
    shared = tmp_path / "downloads"
    shared.mkdir()
    single = shared / "Der Vorleser [B004UWRY6M].m4b"
    single.write_bytes(b"\x00" * 64)
    FakeQb.ITEMS = [_item(single), _item("relative/path", hash=HASH2)]
    scheduler = SabAutoImportScheduler()
    await scheduler.run_once()

    import app.sab_auto_import as mod

    def boom(*a, **k):
        raise AssertionError("terminal item must not be reprocessed")

    monkeypatch.setattr(mod.SabAutoImportScheduler, "_process_qb_item", boom)
    await scheduler.run_once()
    assert _state(db)["status"] == "skipped" and _state(db, HASH2)["status"] == "skipped"


# --- symlinked root folder ---------------------------------------------------


@pytest.mark.parametrize("via_link", [True, False])
async def test_symlinked_root_folder_matches_resolved_download_path(
    tmp_path, db, stub_chain, qb, via_link
):
    real_root = tmp_path / "real-downloads"
    real_root.mkdir()
    link_root = tmp_path / "downloads"  # configured root row points here
    link_root.symlink_to(real_root, target_is_directory=True)
    book = real_root / "Bernhard Schlink - Der Vorleser [B004UWRY6M]"
    book.mkdir()
    (book / "01 - Kapitel 1.mp3").write_bytes(b"\x00" * 1024)
    reported = link_root / book.name if via_link else book
    FakeQb.ITEMS = [_item(reported)]

    await SabAutoImportScheduler().run_once()
    assert _state(db)["status"] == "imported"


async def test_symlinked_root_folder_without_asin_uses_root_import(tmp_path, db, stub_chain, qb):
    real_root = tmp_path / "real-downloads"
    book = real_root / "Bernhard Schlink - Der Vorleser"
    book.mkdir(parents=True)
    (book / "01 - Kapitel 1.mp3").write_bytes(b"\x00" * 1024)
    (tmp_path / "downloads").symlink_to(real_root, target_is_directory=True)
    FakeQb.ITEMS = [_item(tmp_path / "downloads" / book.name)]

    await SabAutoImportScheduler().run_once()
    state = _state(db)
    assert "no configured root folder" not in state["reason"]
    assert "not found by scan" not in state["reason"]


async def test_traversal_and_symlink_escape_are_still_rejected_with_symlink_root(
    tmp_path, db, stub_chain, qb, monkeypatch
):
    real_root = tmp_path / "real-downloads"
    real_root.mkdir()
    (tmp_path / "downloads").symlink_to(real_root, target_is_directory=True)
    outside = _book_folder(tmp_path, "elsewhere")
    (real_root / "innocent").symlink_to(outside, target_is_directory=True)
    FakeQb.ITEMS = [
        _item(tmp_path / "downloads" / "innocent"),
        _item(tmp_path / "downloads" / ".." / "elsewhere" / outside.name, hash=HASH2),
    ]
    _forbid_probes(monkeypatch)
    await SabAutoImportScheduler().run_once()
    assert _state(db)["status"] == "failed" and _state(db)["reason"].startswith("retry: ")
    assert _state(db, HASH2)["status"] == "skipped"  # ".." is never accepted
    assert db.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
