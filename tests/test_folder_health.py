"""Unit tests for app.library.folder_health.probe_root_folder (issue #31)."""

from __future__ import annotations

import os

from app.library.folder_health import probe_root_folder


def test_probe_healthy_folder(tmp_path):
    health = probe_root_folder(str(tmp_path))
    assert health.exists is True
    assert health.writable is True
    assert health.free_bytes is not None and health.free_bytes > 0
    assert health.total_bytes is not None and health.total_bytes > 0
    assert health.error is None


def test_probe_nonexistent_path(tmp_path):
    missing = tmp_path / "does-not-exist"
    health = probe_root_folder(str(missing))
    assert health.exists is False
    assert health.writable is False
    assert health.free_bytes is None
    assert health.total_bytes is None


def test_probe_non_writable_folder(tmp_path):
    folder = tmp_path / "readonly"
    folder.mkdir()
    os.chmod(folder, 0o500)
    try:
        health = probe_root_folder(str(folder))
        assert health.exists is True
        assert health.writable is False
        assert health.error is not None
    finally:
        os.chmod(folder, 0o700)
