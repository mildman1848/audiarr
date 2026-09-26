"""Client for a Liberatarr instance (Servarr-style web UI for Libation).

Liberatarr exposes a user's purchased Audible library so Audiarr can offer
purchased-but-missing audiobooks as monitored Wanted entries (issue #64).
This integration is strictly read-only: Audiarr only ever calls Liberatarr's
``GET /health`` and ``GET /api/library`` endpoints. The token, when set, is
sent as ``Authorization: Bearer <token>``. The HTTP client is injected for
testability.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("audiarr.connections.liberatarr")


class LiberatarrError(Exception):
    """Raised when Liberatarr cannot be reached or returns something unusable."""


class LiberatarrClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = client

    def _headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=self.base_url, timeout=10.0), True

    async def health(self) -> dict:
        """Probe ``GET /health``. Never raises; returns ``{"ok", "message"}``."""
        client, owns_client = await self._get_client()
        try:
            response = await client.get("/health", headers=self._headers())
            if response.status_code != 200:
                log.warning("Liberatarr health check: HTTP %s", response.status_code)
                return {
                    "ok": False,
                    "message": f"Liberatarr returned HTTP {response.status_code}",
                }
            return {"ok": True, "message": "Liberatarr is reachable"}
        except httpx.HTTPError as exc:
            log.warning("Liberatarr health check failed: %s", exc)
            return {"ok": False, "message": f"Could not reach Liberatarr: {exc}"}
        finally:
            if owns_client:
                await client.aclose()

    async def library(self) -> list[dict]:
        """Fetch and normalize ``GET /api/library`` rows.

        The real Liberatarr API wraps rows as ``{"books": [...]}``; a bare
        list is also accepted defensively in case of an older/other shape.

        Raises ``LiberatarrError`` on transport failure, a non-200 status, or
        an unparseable/unexpected response so the caller (an API route) can
        turn it into a 502 instead of crashing the sync.
        """
        client, owns_client = await self._get_client()
        try:
            try:
                response = await client.get("/api/library", headers=self._headers())
            except httpx.HTTPError as exc:
                raise LiberatarrError(f"Could not reach Liberatarr: {exc}") from exc
            if response.status_code != 200:
                raise LiberatarrError(f"Liberatarr returned HTTP {response.status_code}")
            try:
                data = response.json()
            except ValueError as exc:
                raise LiberatarrError("Liberatarr returned invalid JSON") from exc
            if isinstance(data, dict):
                book_rows = data.get("books")
            elif isinstance(data, list):
                book_rows = data
            else:
                book_rows = None
            if not isinstance(book_rows, list):
                raise LiberatarrError("Liberatarr returned an unexpected JSON shape")
            rows = [_normalize_row(row) for row in book_rows if isinstance(row, dict)]
            log.debug("Liberatarr library fetch -> %d row(s)", len(rows))
            return rows
        finally:
            if owns_client:
                await client.aclose()


def _normalize_names(value: Any) -> list[str]:
    """Normalize an Authors/Narrators field: a list of ``{Name}``/strings, a
    bare string, or a missing/None value -- upstream shape may vary."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    names: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("Name") or item.get("name")
            if name:
                names.append(str(name))
        elif isinstance(item, str) and item:
            names.append(item)
    return names


def _normalize_row(row: dict) -> dict:
    """Normalize one Liberatarr library row.

    The real Liberatarr API (``_row_to_book`` in its ``ldb.py``) reports the
    ASIN as ``product_id``, the language as ``language``, and ``status`` as
    an integer enum (0=Not Liberated, 1=Liberated, 2=Error, 0x1000=Partial)
    alongside a human-readable ``status_text``. Older/defensive field names
    (``Asin``/``asin``, ``Locale``/``locale``, ``Status``) are still accepted
    as fallbacks so a shape drift never crashes the sync -- every field
    falls back to an empty value.
    """
    status_value = row.get("status")
    if status_value is None:
        status_value = row.get("Status")
    if status_value is None:
        status_value = row.get("status_text", "")
    return {
        "asin": str(row.get("product_id") or row.get("Asin") or row.get("asin") or "").strip(),
        "title": str(row.get("Title") or row.get("title") or "").strip(),
        "authors": _normalize_names(row.get("Authors") or row.get("authors")),
        "narrators": _normalize_names(row.get("Narrators") or row.get("narrators")),
        "status": status_value,
        "locale": str(row.get("language") or row.get("Locale") or row.get("locale") or "").strip(),
    }


def is_not_liberated(status: Any) -> bool:
    """True when a Liberatarr status means "purchased but not yet liberated
    (downloaded) locally".

    Accepts the real integer enum (``0`` means Not Liberated; any other int,
    including the Error/Partial values, means False), int-like strings
    (e.g. ``"0"``), and human-readable ``status_text``/legacy status tokens
    -- case-insensitive and tolerant of separators (e.g. "Not Liberated",
    "not_liberated"). Unknown/unparseable values are treated as False so an
    error or partial row is never synced as wanted.
    """
    if isinstance(status, bool):
        return False
    if isinstance(status, int):
        return status == 0
    text = str(status or "").strip()
    if not text:
        return False
    if text.lstrip("+-").isdigit():
        return int(text) == 0
    normalized = text.lower().replace(" ", "").replace("_", "").replace("-", "")
    return "notliberated" in normalized


async def test_connection(settings: Any, client: httpx.AsyncClient | None = None) -> dict:
    """Probe Liberatarr's ``/health`` endpoint for the given settings."""
    return await LiberatarrClient(
        base_url=settings.base_url, token=settings.token, client=client
    ).health()


async def fetch_library(settings: Any, client: httpx.AsyncClient | None = None) -> list[dict]:
    """Fetch the normalized Liberatarr library for the given settings.

    Raises ``LiberatarrError`` on any upstream failure.
    """
    return await LiberatarrClient(
        base_url=settings.base_url, token=settings.token, client=client
    ).library()
