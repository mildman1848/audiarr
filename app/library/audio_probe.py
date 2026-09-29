"""ffprobe-based read-only audio metadata extraction (#71).

Runs the ``ffprobe`` binary against an already-imported audio file to
recover duration, bitrate, codec, container, and chapter data for the file
table / book detail UI. This module never writes to the probed file and
never raises -- a missing binary, an invalid file, a timeout, or malformed
ffprobe output all resolve to a non-"ok" ``probe_status`` so callers (the
importer) can persist a best-effort result without ever blocking or failing
an import because of it.

No embedded conversion: this is enrichment only, alongside the existing
external m4b-convertarr/command conversion backend (see app/conversion).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("audiarr.library.audio_probe")

FFPROBE_BIN = "ffprobe"
PROBE_TIMEOUT_SECONDS = 30


@dataclass
class ProbedChapter:
    index: int
    title: str
    start_seconds: float
    end_seconds: float | None


@dataclass
class AudioProbeResult:
    """Normalized ffprobe result. ``probe_status``:

    - ``ok``          -- ffprobe ran and produced parseable output.
    - ``error``        -- ffprobe ran but failed, timed out, or returned
                          output that could not be parsed.
    - ``unavailable``  -- the ffprobe binary itself could not be found.
    """

    probe_status: str
    probe_error: str = ""
    duration_seconds: int | None = None
    bitrate_kbps: int | None = None
    codec: str | None = None
    container: str | None = None
    chapter_count: int | None = None
    chapters: list[ProbedChapter] = field(default_factory=list)


async def probe_file(path: str) -> AudioProbeResult:
    """Read-only ffprobe pass over ``path``. Never raises."""
    try:
        return await _run_probe(path)
    except Exception as exc:  # noqa: BLE001 -- probing must never break an import
        log.warning("unexpected ffprobe failure for %s: %s", path, exc, exc_info=True)
        return AudioProbeResult(probe_status="error", probe_error=str(exc))


async def _run_probe(path: str) -> AudioProbeResult:
    cmd = [
        FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_chapters",
        "-show_streams",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        log.warning("ffprobe binary not found; skipping audio metadata probe for %s", path)
        return AudioProbeResult(probe_status="unavailable", probe_error="ffprobe binary not found")
    except OSError as exc:
        log.warning("failed to launch ffprobe for %s: %s", path, exc)
        return AudioProbeResult(probe_status="error", probe_error=str(exc))

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        log.warning("ffprobe timed out probing %s", path)
        return AudioProbeResult(probe_status="error", probe_error="ffprobe timed out")

    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", "replace").strip()[:300]
        log.debug("ffprobe exited %s for %s: %s", proc.returncode, path, detail)
        return AudioProbeResult(
            probe_status="error", probe_error=detail or f"ffprobe exited {proc.returncode}"
        )

    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("failed to parse ffprobe output for %s: %s", path, exc)
        return AudioProbeResult(probe_status="error", probe_error=f"invalid ffprobe output: {exc}")

    result = _parse_probe_json(data)
    log.debug(
        "probed %s: duration=%s bitrate=%s codec=%s chapters=%d",
        path, result.duration_seconds, result.bitrate_kbps, result.codec, result.chapter_count or 0,
    )
    return result


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_probe_json(data: dict[str, Any]) -> AudioProbeResult:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_seconds = _to_int(fmt.get("duration"))

    bitrate_raw = fmt.get("bit_rate") or (audio_stream or {}).get("bit_rate")
    bitrate_bps = _to_int(bitrate_raw)
    bitrate_kbps = bitrate_bps // 1000 if bitrate_bps is not None else None

    codec = (audio_stream or {}).get("codec_name") or None
    # format_name is a comma-separated list of aliases (e.g. "mov,mp4,m4a,
    # 3gp,3g2,mj2"); the first entry is ffprobe's primary name for the
    # container and is what's worth showing in a dense file table.
    format_name = fmt.get("format_name") or ""
    container = format_name.split(",")[0] or None

    chapters: list[ProbedChapter] = []
    for i, chapter in enumerate(data.get("chapters") or []):
        start = _to_float(chapter.get("start_time")) or 0.0
        end = _to_float(chapter.get("end_time"))
        title = ((chapter.get("tags") or {}).get("title") or "").strip()
        chapters.append(ProbedChapter(index=i, title=title, start_seconds=start, end_seconds=end))

    return AudioProbeResult(
        probe_status="ok",
        duration_seconds=duration_seconds,
        bitrate_kbps=bitrate_kbps,
        codec=codec,
        container=container,
        chapter_count=len(chapters),
        chapters=chapters,
    )
