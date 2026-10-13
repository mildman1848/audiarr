"""Client for a SABnzbd download client.

This client covers the health/version check used by the Settings "Download
Clients" section (so users can verify their SABnzbd URL and API key) plus
the grab/queue/history workflow used by the Arr-core loop.

SABnzbd exposes a single ``/api`` endpoint driven by a ``mode`` query
parameter. The version probe is unauthenticated-friendly on some setups
but normally needs the API key, so we always send it when present. The
API key can also come from ``SABNZBD_API_KEY`` / ``FILE__SABNZBD_API_KEY``
per LSIO convention (see app/secrets_util.py), but only for the destination
named by the non-secret ``SABNZBD_URL`` setting (see
``resolve_sabnzbd_api_key``). The HTTP client is injected for testability.
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.sabnzbd")

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Raw C0/C1 control characters, checked before parsing because URL parsers
# silently drop CR/LF/TAB.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_URL_FORBIDDEN_CHARS = re.compile(r"[\s\\]")


def normalize_sabnzbd_base_url(url: str | None) -> tuple[str, str, int, str] | None:
    """``(scheme, host, effective port, base path)`` of a SABnzbd base URL.

    Strict on purpose, since the result decides where a deployment secret may
    be sent: only ``http``/``https`` with a host, no userinfo, query, fragment,
    control characters, whitespace or backslashes, and a valid port. Scheme and
    host are case-folded, a missing port becomes the scheme default, and one
    trailing slash on the path is ignored; nothing else is normalized. Returns
    None for anything unset or malformed.
    """
    if not url:
        return None
    url = url.strip()
    if (
        not url
        or _CONTROL_CHARS.search(url)
        or _URL_FORBIDDEN_CHARS.search(url)
        or "?" in url
        or "#" in url
    ):
        return None
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
        if parts.username is not None or parts.password is not None or "@" in parts.netloc:
            return None
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if port is None:
        port = _DEFAULT_PORTS[scheme]
    elif port == 0:
        return None
    path = parts.path.removesuffix("/")
    if path.endswith("/"):
        return None
    return scheme, host.lower(), port, path


def is_trusted_sabnzbd_url(base_url: str | None) -> bool:
    """True when ``base_url`` is exactly the trusted ``SABNZBD_URL`` destination.

    ``SABNZBD_URL`` is a non-secret deployment setting. If it is unset or
    malformed nothing is trusted, so the environment key is never used.
    """
    trusted = normalize_sabnzbd_base_url(os.environ.get("SABNZBD_URL"))
    if trusted is None:
        return False
    return trusted == normalize_sabnzbd_base_url(base_url)


def resolve_sabnzbd_api_key(base_url: str | None) -> str:
    """The deployment API key (env/``FILE__``) for requests to ``base_url``.

    Returned only when ``base_url`` matches the trusted ``SABNZBD_URL``, so a
    settings change can not re-aim the deployment secret. Otherwise "".
    """
    if not is_trusted_sabnzbd_url(base_url):
        log.debug("SABnzbd environment API key not used: URL is not the trusted SABNZBD_URL")
        return ""
    return resolve_secret("SABNZBD_API_KEY") or ""


class SABnzbdClient:
    """SABnzbd API client.

    ``api_key=None`` falls back to ``SABNZBD_API_KEY`` /
    ``FILE__SABNZBD_API_KEY``, but only when ``base_url`` matches the trusted
    ``SABNZBD_URL``; otherwise no key is sent. An explicit empty string means
    "send no key"; an explicit nonempty key is always used.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = resolve_sabnzbd_api_key(base_url) if api_key is None else api_key
        self._client = client

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=self.base_url, timeout=10.0), True

    def _base_params(self, mode: str) -> dict[str, str]:
        params = {"mode": mode, "output": "json"}
        if self.api_key:
            params["apikey"] = self.api_key
        return params

    @staticmethod
    def _to_float(value: object) -> float:
        """Best-effort numeric coercion for SABnzbd's string-typed fields."""
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0

    async def add_nzb(self, content: bytes, name: str, category: str) -> str | None:
        """Push an NZB file to SABnzbd, returning the assigned nzo_id.

        Uses ``mode=addfile`` with a multipart ``nzbfile`` field. Returns
        None on any failure (transport error, non-200, ``status`` not true,
        or no ``nzo_ids`` in the response).
        """
        client, owns_client = await self._get_client()
        try:
            params = self._base_params("addfile")
            if category:
                params["cat"] = category
            filename = name if name.endswith(".nzb") else f"{name}.nzb"
            files = {"nzbfile": (filename, content, "application/x-nzb")}
            response = await client.post("/api", params=params, files=files)
            if response.status_code != 200:
                log.warning("SABnzbd addfile: HTTP %s", response.status_code)
                return None
            try:
                data = response.json()
            except ValueError:
                log.warning("SABnzbd addfile: response was not JSON")
                return None
            if not isinstance(data, dict) or not data.get("status"):
                log.warning("SABnzbd addfile: status not true in response")
                return None
            nzo_ids = data.get("nzo_ids") or []
            if not nzo_ids:
                log.warning("SABnzbd addfile: no nzo_ids returned")
                return None
            nzo_id = str(nzo_ids[0])
            log.debug("SABnzbd addfile %r (cat=%s) -> %s", filename, category, nzo_id)
            return nzo_id
        except httpx.HTTPError as exc:
            log.warning("SABnzbd addfile failed: %s", exc)
            return None
        finally:
            if owns_client:
                await client.aclose()

    async def queue(self) -> list[dict]:
        """Return the live download queue as normalized slot dicts.

        Never raises: failures are logged and yield an empty list.
        """
        client, owns_client = await self._get_client()
        try:
            response = await client.get("/api", params=self._base_params("queue"))
            if response.status_code != 200:
                log.warning("SABnzbd queue: HTTP %s", response.status_code)
                return []
            try:
                data = response.json()
            except ValueError:
                log.warning("SABnzbd queue: response was not JSON")
                return []
            queue = data.get("queue") if isinstance(data, dict) else None
            slots = queue.get("slots") if isinstance(queue, dict) else None
            if not isinstance(slots, list):
                return []
            return [
                {
                    "nzo_id": s.get("nzo_id"),
                    "filename": s.get("filename"),
                    "status": s.get("status"),
                    "size": s.get("size"),
                    "size_left": s.get("sizeleft"),
                    "time_left": s.get("timeleft"),
                    "progress_percent": self._to_float(s.get("percentage")),
                    "category": s.get("category"),
                }
                for s in slots
                if isinstance(s, dict)
            ]
        except httpx.HTTPError as exc:
            log.warning("SABnzbd queue failed: %s", exc)
            return []
        finally:
            if owns_client:
                await client.aclose()

    async def history(self, limit: int = 50) -> list[dict]:
        """Return recent download history as normalized slot dicts.

        Never raises: failures are logged and yield an empty list.
        """
        client, owns_client = await self._get_client()
        try:
            params = self._base_params("history")
            params["limit"] = str(limit)
            response = await client.get("/api", params=params)
            if response.status_code != 200:
                log.warning("SABnzbd history: HTTP %s", response.status_code)
                return []
            try:
                data = response.json()
            except ValueError:
                log.warning("SABnzbd history: response was not JSON")
                return []
            history = data.get("history") if isinstance(data, dict) else None
            slots = history.get("slots") if isinstance(history, dict) else None
            if not isinstance(slots, list):
                return []
            return [
                {
                    "nzo_id": s.get("nzo_id"),
                    "name": s.get("name"),
                    "status": s.get("status"),
                    "size": s.get("size"),
                    "category": s.get("category"),
                    "completed_at": s.get("completed"),
                    # "storage" is SABnzbd's full path to the completed
                    # download folder/file; older SAB versions only send the
                    # deprecated "path" alias. Used by the auto-import
                    # poller (see app/sab_auto_import.py) to locate the
                    # folder on disk; never populated by the queue.
                    "storage": s.get("storage") or s.get("path") or "",
                    "fail_message": s.get("fail_message") or "",
                }
                for s in slots
                if isinstance(s, dict)
            ]
        except httpx.HTTPError as exc:
            log.warning("SABnzbd history failed: %s", exc)
            return []
        finally:
            if owns_client:
                await client.aclose()

    async def _simple_action(self, mode: str, nzo_id: str, extra: dict[str, str]) -> bool:
        """Shared plumbing for the queue/history/retry action endpoints.

        All three are GET requests against ``/api`` that report success via
        ``{"status": true}`` in the JSON body, same envelope as add_nzb().
        Never raises; failures are logged (never including the API key or
        any other request param) and return False.
        """
        client, owns_client = await self._get_client()
        try:
            params = self._base_params(mode)
            params.update(extra)
            response = await client.get("/api", params=params)
            if response.status_code != 200:
                log.warning("SABnzbd %s: HTTP %s (nzo_id=%s)", mode, response.status_code, nzo_id)
                return False
            try:
                data = response.json()
            except ValueError:
                log.warning("SABnzbd %s: response was not JSON (nzo_id=%s)", mode, nzo_id)
                return False
            ok = isinstance(data, dict) and bool(data.get("status"))
            if ok:
                log.debug("SABnzbd %s succeeded (nzo_id=%s)", mode, nzo_id)
            else:
                log.warning("SABnzbd %s: status not true in response (nzo_id=%s)", mode, nzo_id)
            return ok
        except httpx.HTTPError as exc:
            log.warning("SABnzbd %s failed: %s (nzo_id=%s)", mode, exc, nzo_id)
            return False
        finally:
            if owns_client:
                await client.aclose()

    async def queue_remove(self, nzo_id: str) -> bool:
        """Remove one job from the live queue.

        Sends ``del_files=0`` explicitly: this removes the queue entry only
        and never deletes any partially-downloaded data (SABnzbd's own
        "delete download data too" option) -- actions here never touch
        files on disk, only Audiarr's own import pipeline does that.
        """
        return await self._simple_action(
            "queue", nzo_id, {"name": "delete", "value": nzo_id, "del_files": "0"}
        )

    async def history_remove(self, nzo_id: str) -> bool:
        """Remove one entry from history. Never deletes the download's files on disk."""
        return await self._simple_action(
            "history", nzo_id, {"name": "delete", "value": nzo_id, "del_files": "0"}
        )

    async def history_retry(self, nzo_id: str) -> bool:
        """Retry one failed history job (SABnzbd re-queues it from its stored NZB, if retained)."""
        return await self._simple_action("retry", nzo_id, {"value": nzo_id})

    async def version(self) -> str | None:
        """Return the SABnzbd version string, or None if it can't be read.

        Success means: HTTP 200, a parseable JSON body, and a
        version-like ``version`` field. Anything else (transport error,
        non-200, HTML login page, missing field) returns None.
        """
        client, owns_client = await self._get_client()
        try:
            params = {"mode": "version", "output": "json"}
            if self.api_key:
                params["apikey"] = self.api_key
            response = await client.get("/api", params=params)
            if response.status_code != 200:
                log.warning("SABnzbd version check: HTTP %s", response.status_code)
                return None
            try:
                data = response.json()
            except ValueError:
                log.warning("SABnzbd version check: response was not JSON")
                return None
            version = data.get("version") if isinstance(data, dict) else None
            if not isinstance(version, str) or not version.strip():
                log.warning("SABnzbd version check: no version field in response")
                return None
            return version.strip()
        except httpx.HTTPError as exc:
            log.warning("SABnzbd version check failed: %s", exc)
            return None
        finally:
            if owns_client:
                await client.aclose()
