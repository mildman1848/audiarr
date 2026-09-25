"""Unit tests for app.library.import_strategy: move/copy/hardlink placement (#30)."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from app.library.import_strategy import (
    ImportStrategyError,
    InsufficientSpaceError,
    PathTraversalError,
    check_free_space,
    place_candidate_files,
    resolve_target_path,
)


def _make_source(tmp_path: Path, name: str = "book.mp3", data: bytes = b"\x00" * 1024) -> Path:
    src_dir = tmp_path / "incoming"
    src_dir.mkdir(exist_ok=True)
    src = src_dir / name
    src.write_bytes(data)
    return src


# -- resolve_target_path -------------------------------------------------------


def test_resolve_target_path_lands_under_root_and_candidate_folder(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    target = resolve_target_path(str(root), "Some Book [ASIN123]", "file.mp3")
    assert target == (root / "Some Book [ASIN123]" / "file.mp3").resolve()


def test_resolve_target_path_rejects_traversal_outside_root(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    with pytest.raises(PathTraversalError):
        resolve_target_path(str(root), "../../etc", "passwd")


# -- check_free_space -----------------------------------------------------------


def test_check_free_space_passes_when_enough_room(tmp_path):
    check_free_space(tmp_path, required_bytes=1)  # should not raise


def test_check_free_space_raises_when_insufficient(tmp_path, monkeypatch):
    import shutil as shutil_module

    class FakeUsage:
        free = 10

    monkeypatch.setattr(shutil_module, "disk_usage", lambda path: FakeUsage())
    with pytest.raises(InsufficientSpaceError):
        check_free_space(tmp_path, required_bytes=10**9)


# -- place_candidate_files: copy -------------------------------------------------


def test_copy_strategy_persists_new_file_and_keeps_source(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    src = _make_source(tmp_path)

    placed = place_candidate_files(
        [(str(src), src.name, src.stat().st_size, "mp3")],
        str(root),
        "My Book",
        "copy",
    )

    assert len(placed) == 1
    final = Path(placed[0].final_path)
    assert final.exists()
    assert final == root / "My Book" / src.name
    assert placed[0].strategy_used == "copy"
    assert src.exists()  # copy never deletes the source


def test_copy_strategy_source_already_in_root_is_unchanged(tmp_path):
    root = tmp_path / "library"
    candidate_dir = root / "My Book"
    candidate_dir.mkdir(parents=True)
    src = candidate_dir / "file.mp3"
    src.write_bytes(b"\x00" * 10)

    placed = place_candidate_files(
        [(str(src), src.name, 10, "mp3")], str(root), "My Book", "copy"
    )
    assert placed[0].strategy_used == "unchanged"
    assert placed[0].final_path == str(src.resolve())


# -- place_candidate_files: move -------------------------------------------------


def test_move_strategy_relocates_and_removes_source(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    src = _make_source(tmp_path)

    placed = place_candidate_files(
        [(str(src), src.name, src.stat().st_size, "mp3")],
        str(root),
        "My Book",
        "move",
    )

    final = Path(placed[0].final_path)
    assert final.exists()
    assert not src.exists()
    assert placed[0].strategy_used == "move"


# -- place_candidate_files: hardlink ---------------------------------------------


def test_hardlink_strategy_links_file(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    src = _make_source(tmp_path)

    placed = place_candidate_files(
        [(str(src), src.name, src.stat().st_size, "mp3")],
        str(root),
        "My Book",
        "hardlink",
    )

    final = Path(placed[0].final_path)
    assert final.exists()
    assert src.exists()
    assert placed[0].strategy_used == "hardlink"
    assert final.stat().st_ino == src.stat().st_ino


def test_hardlink_strategy_falls_back_to_copy_on_exdev(tmp_path, monkeypatch, caplog):
    root = tmp_path / "library"
    root.mkdir()
    src = _make_source(tmp_path)

    def fake_link(source, target):
        raise OSError(getattr(os, "EXDEV", 18), "Invalid cross-device link")

    monkeypatch.setattr(os, "link", fake_link)

    with caplog.at_level("WARNING"):
        placed = place_candidate_files(
            [(str(src), src.name, src.stat().st_size, "mp3")],
            str(root),
            "My Book",
            "hardlink",
        )

    final = Path(placed[0].final_path)
    assert final.exists()
    assert src.exists()
    assert placed[0].strategy_used == "copy"
    assert any("falling back to copy" in r.message for r in caplog.records)


def test_hardlink_strategy_falls_back_to_copy_on_eperm(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    src = _make_source(tmp_path)

    def fake_link(source, target):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", fake_link)

    placed = place_candidate_files(
        [(str(src), src.name, src.stat().st_size, "mp3")],
        str(root),
        "My Book",
        "hardlink",
    )
    assert placed[0].strategy_used == "copy"
    assert Path(placed[0].final_path).exists()


# -- free space guard -------------------------------------------------------------


def test_copy_strategy_raises_and_does_not_write_when_space_insufficient(tmp_path, monkeypatch):
    import shutil as shutil_module

    root = tmp_path / "library"
    root.mkdir()
    src = _make_source(tmp_path)

    class FakeUsage:
        free = 1  # far less than the file size

    monkeypatch.setattr(shutil_module, "disk_usage", lambda path: FakeUsage())

    with pytest.raises(ImportStrategyError):
        place_candidate_files(
            [(str(src), src.name, src.stat().st_size, "mp3")],
            str(root),
            "My Book",
            "copy",
        )

    assert not (root / "My Book" / src.name).exists()
    assert src.exists()  # untouched


# -- rollback on partial failure ---------------------------------------------------


def test_partial_failure_rolls_back_earlier_placements(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    src1 = _make_source(tmp_path, "a.mp3")
    # Pre-create the target for the second file so placement fails on it.
    conflict_dir = root / "My Book"
    conflict_dir.mkdir(parents=True)
    (conflict_dir / "b.mp3").write_bytes(b"already there")
    src2 = _make_source(tmp_path, "b.mp3", data=b"\x00" * 5)

    with pytest.raises(ImportStrategyError):
        place_candidate_files(
            [
                (str(src1), "a.mp3", src1.stat().st_size, "mp3"),
                (str(src2), "b.mp3", src2.stat().st_size, "mp3"),
            ],
            str(root),
            "My Book",
            "copy",
        )

    # First file's copy must have been rolled back.
    assert not (conflict_dir / "a.mp3").exists()
    # Source untouched.
    assert src1.exists()
