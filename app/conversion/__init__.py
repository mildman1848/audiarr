"""Conversion backend clients: enqueue MP3 -> M4B conversions.

Two modes, selected by ``ConversionSettings.backend``:

- ``m4b-convertarr`` — POST the folder path to a running m4b-convertarr
  container (``POST /api/convert``, bearer auth when an API key is set).
  The converter owns file movement; Audiarr only records state.
- ``command`` — render ``command_template`` with ``{{source}}`` /
  ``{{output}}`` and run it locally (ffmpeg wrappers etc.).

Safety: this module never deletes originals. Deletion is a separate,
explicit setting handled by the conversion worker (if ever enabled).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.models.settings import ConversionSettings

log = logging.getLogger("audiarr.conversion")


@dataclass
class ConversionResult:
    ok: bool
    detail: str = ""
    output_path: str = ""


class ConversionBackend:
    """Common interface for conversion backends."""

    async def convert(self, source_path: str, output_path: str = "") -> ConversionResult:
        raise NotImplementedError


class M4BConvertarrClient(ConversionBackend):
    """HTTP client for a running m4b-convertarr container."""

    def __init__(self, settings: ConversionSettings) -> None:
        self.base_url = settings.base_url.rstrip("/")
        self.api_key = settings.api_key

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/health")
                return resp.status_code == 200
        except httpx.HTTPError as exc:
            log.debug("convertarr health check failed: %s", exc)
            return False

    async def convert(self, source_path: str, output_path: str = "") -> ConversionResult:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload: dict[str, Any] = {"path": source_path}
        if output_path:
            payload["output"] = output_path
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.base_url}/api/convert", json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            return ConversionResult(ok=False, detail=f"backend unreachable: {exc}")
        if resp.status_code in (200, 201, 202):
            return ConversionResult(ok=True, detail=resp.text[:500], output_path=output_path)
        return ConversionResult(
            ok=False, detail=f"backend returned {resp.status_code}: {resp.text[:300]}"
        )


class CommandBackend(ConversionBackend):
    """Run a local command with {{source}}/{{output}} substitution."""

    def __init__(self, settings: ConversionSettings) -> None:
        self.template = settings.command_template

    async def convert(self, source_path: str, output_path: str = "") -> ConversionResult:
        if not self.template:
            return ConversionResult(ok=False, detail="command_template is empty")
        cmd = self.template.replace("{{source}}", source_path).replace(
            "{{output}}", output_path
        )
        log.debug("command backend running: %s", cmd)
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
        except OSError as exc:
            return ConversionResult(ok=False, detail=f"failed to run command: {exc}")
        if proc.returncode == 0:
            return ConversionResult(ok=True, output_path=output_path)
        return ConversionResult(
            ok=False,
            detail=f"command exited {proc.returncode}: {(stdout or b'')[:300]!r}",
        )


def build_backend(settings: ConversionSettings) -> ConversionBackend | None:
    """Return the configured backend, or None when conversion is disabled."""
    if settings.backend == "m4b-convertarr":
        return M4BConvertarrClient(settings)
    if settings.backend == "command":
        return CommandBackend(settings)
    return None
