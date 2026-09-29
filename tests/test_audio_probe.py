"""Tests for app.library.audio_probe: ffprobe wrapper (#71).

Runs the real ffprobe/ffmpeg binaries against a tiny generated fixture
rather than mocking ffprobe's JSON shape, matching this project's existing
precedent for testing external tools (see tests/test_conversion.py's
CommandBackend tests, which run real shell commands).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.library.audio_probe import probe_file

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

pytestmark = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg/ffprobe not installed")


def _make_chaptered_m4b(tmp_path: Path) -> Path:
    """Generate a tiny 2-second M4B with two 1-second chapters via ffmpeg."""
    meta = tmp_path / "chapters.txt"
    meta.write_text(
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=Chapter One\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=2000\ntitle=Chapter Two\n",
        encoding="utf-8",
    )
    output = tmp_path / "book.m4b"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-i", str(meta),
            "-map_metadata", "1",
            "-c:a", "aac",
            "-f", "mp4",
            str(output),
        ],
        check=True,
    )
    return output


async def test_probe_file_reads_chaptered_m4b(tmp_path):
    audio = _make_chaptered_m4b(tmp_path)

    result = await probe_file(str(audio))

    assert result.probe_status == "ok"
    assert result.probe_error == ""
    assert result.duration_seconds == 2
    assert result.codec == "aac"
    assert result.container == "mov"
    assert result.bitrate_kbps and result.bitrate_kbps > 0
    assert result.chapter_count == 2
    assert [c.title for c in result.chapters] == ["Chapter One", "Chapter Two"]
    assert result.chapters[0].index == 0
    assert result.chapters[0].start_seconds == 0.0
    assert result.chapters[0].end_seconds == 1.0
    assert result.chapters[1].start_seconds == 1.0
    assert result.chapters[1].end_seconds == 2.0


async def test_probe_file_missing_path_is_non_fatal(tmp_path):
    result = await probe_file(str(tmp_path / "does-not-exist.m4b"))

    assert result.probe_status == "error"
    assert result.probe_error
    assert result.duration_seconds is None
    assert result.chapters == []


async def test_probe_file_invalid_audio_is_non_fatal(tmp_path):
    junk = tmp_path / "not-audio.mp3"
    junk.write_bytes(b"\x00" * 128)

    result = await probe_file(str(junk))

    assert result.probe_status == "error"
    assert result.probe_error
    assert result.duration_seconds is None
    assert result.chapter_count is None
    assert result.chapters == []


async def test_probe_file_reports_unavailable_when_binary_missing(tmp_path, monkeypatch):
    from app.library import audio_probe

    monkeypatch.setattr(audio_probe, "FFPROBE_BIN", "ffprobe-binary-does-not-exist")

    result = await probe_file(str(tmp_path / "whatever.mp3"))

    assert result.probe_status == "unavailable"
    assert "not found" in result.probe_error
