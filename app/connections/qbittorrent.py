"""Client for a qBittorrent download client (issue #81).

Covers the connection test (``/api/v2/app/version``), submitting one torrent
URL, and listing completed torrents -- nothing else. Audiarr never deletes,
stops, or pauses a torrent, and never sends a ``savepath``: placement is
bounded by the client's own category/tag configuration.

Authentication is fail-closed and never silently downgraded:

* ``api_key`` configured -> stateless ``Authorization: Bearer`` (qBittorrent
  >= 5.2.0). A 401/403 is an auth failure; the legacy login is NOT tried.
* no ``api_key`` but ``username`` and ``password`` -> legacy cookie login
  (``POST /api/v2/auth/login``, ``SID`` cookie, ``Referer`` matching the
  host). One re-login is attempted if the session expired.
* neither -> ``QBittorrentError("not_configured")``; anonymous access is not
  assumed.

Errors are raised as ``QBittorrentError`` carrying a short reason code and a
message that is safe to show or log: it never contains the API key, the
password, the session id, or any release/magnet URL. Redirects are not
followed so credentials are never replayed to another host. The HTTP client
is injected for testability.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

log = logging.getLogger("audiarr.connections.qbittorrent")

_HASH_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_MAX_URL_LEN = 8192

_MESSAGES = {
    "not_configured": "qBittorrent has no credentials configured",
    "auth": "qBittorrent authentication failed",
    "timeout": "qBittorrent timed out",
    "unreachable": "Could not reach qBittorrent",
    "http_error": "qBittorrent returned an unexpected HTTP status",
    "malformed": "qBittorrent returned an unexpected response",
    "rejected": "qBittorrent did not accept the torrent",
}


class QBittorrentError(Exception):
    """A qBittorrent failure with a stable ``reason`` code and a safe message."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        message = _MESSAGES.get(reason, "qBittorrent request failed")
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)


def normalize_magnet(url: str | None) -> str | None:
    """Return ``url`` stripped if it is a well-formed BitTorrent magnet link."""
    if not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate or len(candidate) > _MAX_URL_LEN:
        return None
    if any(ch.isspace() or ord(ch) < 0x20 for ch in candidate):
        return None
    if not candidate.lower().startswith("magnet:?"):
        return None
    if magnet_info_hash(candidate) is None:
        return None
    return candidate


def magnet_info_hash(url: str) -> str | None:
    """Extract the lowercase hex info-hash from a magnet link, or None."""
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        return None
    for xt in query.get("xt", []):
        lowered = xt.lower()
        if not lowered.startswith("urn:btih:"):
            continue
        value = xt[len("urn:btih:"):]
        if _HASH_RE.match(value):
            return value.lower()
        if len(value) == 32:  # base32-encoded SHA-1
            try:
                return base64.b32decode(value.upper()).hex()
            except (binascii.Error, ValueError):
                return None
    return None


class QBittorrentClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.username = username or ""
        self.password = password or ""
        self._client = client
        self._timeout = timeout
        self._sid: str | None = None

    @property
    def auth_mode(self) -> str:
        if self.api_key:
            return "api_key"
        if self.username and self.password:
            return "legacy"
        return "none"

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=self.base_url, timeout=self._timeout), True

    async def _login(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            "/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            headers={"Referer": self.base_url},
        )
        if response.status_code in (401, 403):
            raise QBittorrentError("auth")
        if response.status_code != 200:
            raise QBittorrentError("http_error", f"HTTP {response.status_code}")
        # qBittorrent answers 200 "Fails." for wrong credentials.
        sid = response.cookies.get("SID")
        if response.text.strip() != "Ok." or not sid:
            raise QBittorrentError("auth")
        return sid

    async def _send(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        params: dict[str, str] | None,
        data: dict[str, str] | None,
    ) -> httpx.Response:
        mode = self.auth_mode
        if mode == "none":
            raise QBittorrentError("not_configured")
        for attempt in (1, 2):
            headers: dict[str, str] = {}
            if mode == "api_key":
                headers["Authorization"] = f"Bearer {self.api_key}"
            else:
                if self._sid is None:
                    self._sid = await self._login(client)
                headers["Cookie"] = f"SID={self._sid}"
                headers["Referer"] = self.base_url
            response = await client.request(method, path, params=params, data=data, headers=headers)
            if response.status_code in (401, 403):
                if mode == "legacy" and attempt == 1:
                    self._sid = None  # session expired; log in once more
                    continue
                raise QBittorrentError("auth")
            if response.status_code != 200:
                raise QBittorrentError("http_error", f"HTTP {response.status_code}")
            return response
        raise QBittorrentError("auth")  # pragma: no cover - loop always returns/raises

    async def _call(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> httpx.Response:
        client, owns_client = await self._get_client()
        try:
            return await self._send(client, method, path, params, data)
        except QBittorrentError as exc:
            log.warning("qBittorrent %s failed: %s", path, exc.reason)
            raise
        except httpx.TimeoutException:
            log.warning("qBittorrent %s failed: timeout", path)
            raise QBittorrentError("timeout") from None
        except (httpx.HTTPError, httpx.InvalidURL):
            log.warning("qBittorrent %s failed: unreachable", path)
            raise QBittorrentError("unreachable") from None
        finally:
            if owns_client:
                await client.aclose()

    async def version(self) -> str:
        """Return the qBittorrent version string (the connection test)."""
        response = await self._call("GET", "/api/v2/app/version")
        text = response.text.strip()
        if not text or len(text) > 64 or "<" in text:
            log.warning("qBittorrent version check: unexpected body")
            raise QBittorrentError("malformed")
        return text

    async def add_url(self, url: str, category: str, tag: str) -> None:
        """Submit one magnet / HTTP(S) torrent URL under the fixed category/tag.

        No ``savepath`` (and no ``paused``) is ever sent. Raises
        ``QBittorrentError("rejected")`` when qBittorrent refuses the torrent.
        """
        data = {"urls": url}
        if category:
            data["category"] = category
        if tag:
            data["tags"] = tag
        response = await self._call("POST", "/api/v2/torrents/add", data=data)
        if not _add_succeeded(response):
            log.warning("qBittorrent torrents/add: not accepted")
            raise QBittorrentError("rejected")
        log.debug("qBittorrent torrents/add accepted (category=%s, tag=%s)", category, tag)

    async def list_completed(self, category: str = "", tag: str = "") -> list[dict[str, Any]]:
        """Return completed torrents (``filter=completed``) in category/tag.

        The category/tag narrowing is sent to qBittorrent *and* re-checked
        here, so a server that ignores the query parameters can't widen the
        import set. Items without a valid info-hash are dropped.
        """
        params = {"filter": "completed"}
        if category:
            params["category"] = category
        if tag:
            params["tag"] = tag
        response = await self._call("GET", "/api/v2/torrents/info", params=params)
        try:
            payload = response.json()
        except ValueError:
            log.warning("qBittorrent torrents/info: response was not JSON")
            raise QBittorrentError("malformed") from None
        if not isinstance(payload, list):
            log.warning("qBittorrent torrents/info: unexpected JSON shape")
            raise QBittorrentError("malformed")

        items: list[dict[str, Any]] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            torrent_hash = raw.get("hash")
            if not isinstance(torrent_hash, str) or not _HASH_RE.match(torrent_hash):
                continue
            tags = [t.strip() for t in str(raw.get("tags") or "").split(",") if t.strip()]
            if category and (raw.get("category") or "") != category:
                continue
            if tag and tag not in tags:
                continue
            progress = raw.get("progress")
            if isinstance(progress, int | float) and progress < 1:
                continue
            items.append(
                {
                    "hash": torrent_hash.lower(),
                    "name": str(raw.get("name") or ""),
                    "category": str(raw.get("category") or ""),
                    "tags": tags,
                    "state": str(raw.get("state") or ""),
                    "progress": progress,
                    "save_path": str(raw.get("save_path") or ""),
                    "content_path": str(raw.get("content_path") or ""),
                }
            )
        return items


def _add_succeeded(response: httpx.Response) -> bool:
    """Interpret ``torrents/add``: plain ``Ok.``/``Fails.`` text, or the JSON
    count summary newer releases may return."""
    text = response.text.strip()
    if text == "Ok.":
        return True
    try:
        body = response.json()
    except ValueError:
        return False
    if isinstance(body, dict):
        failed = body.get("failure_count") or 0
        ok = (body.get("success_count") or 0) + (body.get("pending_count") or 0)
        return isinstance(failed, int) and isinstance(ok, int) and ok > 0 and failed == 0
    return False
