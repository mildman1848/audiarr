"""Automatic config backup service tests (issue #32, phase 5)."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest

from app.backup_service import BackupError, _prune_backups, create_backup, list_backups


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Isolated AUDIARR_CONFIG_DIR with a real (empty) DB + settings.json."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module
    from app.db import init_db

    init_db(config_module.get_db_path())
    config_module.save_settings(config_module.load_settings())
    return config_module


def _set_backup_settings(folder: Path, retention_copies: int = 7) -> None:
    from app.config import load_settings, save_settings

    settings = load_settings()
    settings.backup.folder = str(folder)
    settings.backup.retention_copies = retention_copies
    save_settings(settings)


def test_create_backup_writes_zip_with_manifest(tmp_path, isolated_config):
    backup_dir = tmp_path / "backups"
    _set_backup_settings(backup_dir)

    result = create_backup(reason="manual")

    backup_path = Path(result["path"])
    assert backup_path.exists()
    assert backup_path.parent == backup_dir
    assert oct(backup_path.stat().st_mode & 0o777) == oct(0o600)
    assert result["reason"] == "manual"
    assert result["pruned"] == []
    assert {f["name"] for f in result["files"]} == {"audiarr.db", "settings.json"}

    with zipfile.ZipFile(backup_path) as zf:
        assert set(zf.namelist()) == {"audiarr.db", "settings.json", "manifest.json"}
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["files"] == result["files"]
        assert manifest["created_at"] == result["created_at"]

        for entry in manifest["files"]:
            extracted = zf.read(entry["name"])
            assert hashlib.sha256(extracted).hexdigest() == entry["sha256"]
            assert len(extracted) == entry["size_bytes"]


def test_create_backup_does_not_overwrite_same_second_existing_archive(tmp_path, isolated_config):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _set_backup_settings(backup_dir, retention_copies=0)

    first = create_backup(reason="manual")
    first_path = Path(first["path"])
    first_path.write_bytes(b"sentinel")

    second = create_backup(reason="manual")

    assert Path(second["path"]) != first_path
    assert first_path.read_bytes() == b"sentinel"


def test_create_backup_triggers_rotation(tmp_path, isolated_config):
    """Pre-seed two older backups; retention=2 must prune exactly one on a new run."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True)
    for name in ("audiarr-backup-20200101-000000.zip", "audiarr-backup-20200102-000000.zip"):
        (backup_dir / name).write_bytes(b"x")

    _set_backup_settings(backup_dir, retention_copies=2)

    result = create_backup()

    remaining = sorted(p.name for p in backup_dir.glob("audiarr-backup-*.zip"))
    assert len(remaining) == 2
    assert Path(result["path"]).name in remaining
    assert len(result["pruned"]) == 1
    assert "audiarr-backup-20200101-000000.zip" in result["pruned"][0]


def test_create_backup_raises_backuperror_on_unwritable_folder(tmp_path, isolated_config):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    os.chmod(backup_dir, 0o500)
    _set_backup_settings(backup_dir)

    try:
        with pytest.raises(BackupError):
            create_backup()
    finally:
        os.chmod(backup_dir, 0o700)


def test_prune_backups_keeps_newest_n(tmp_path):
    names = [
        "audiarr-backup-20260101-000000.zip",
        "audiarr-backup-20260102-000000.zip",
        "audiarr-backup-20260103-000000.zip",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"x")

    pruned = _prune_backups(tmp_path, keep=2)

    assert pruned == [str(tmp_path / names[0])]
    remaining = {p.name for p in tmp_path.glob("audiarr-backup-*.zip")}
    assert remaining == {names[1], names[2]}


def test_prune_backups_disabled_when_keep_zero(tmp_path):
    for i in range(3):
        (tmp_path / f"audiarr-backup-2026010{i}-000000.zip").write_bytes(b"x")

    pruned = _prune_backups(tmp_path, keep=0)

    assert pruned == []
    assert len(list(tmp_path.glob("audiarr-backup-*.zip"))) == 3


def test_list_backups_newest_first_skips_unparseable(tmp_path, isolated_config):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True)
    _set_backup_settings(backup_dir)

    older = backup_dir / "audiarr-backup-20200101-000000.zip"
    newer = backup_dir / "audiarr-backup-20250101-120000.zip"
    older.write_bytes(b"x")
    newer.write_bytes(b"xx")
    (backup_dir / "audiarr-backup-not-a-date.zip").write_bytes(b"bad")

    backups = list_backups()

    assert [b["name"] for b in backups] == [newer.name, older.name]
    assert backups[0]["size_bytes"] == 2


def test_list_backups_empty_when_folder_missing(tmp_path, isolated_config):
    _set_backup_settings(tmp_path / "does-not-exist")

    assert list_backups() == []
