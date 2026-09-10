"""Client for a SABnzbd download client.

Audiarr does not implement the full grab/queue workflow yet; this client
only covers the health/version check used by the Settings "Download
Clients" section so users can verify their SABnzbd URL and API key before
the rest of the pipeline lands.

SABnzbd exposes a single ``/api`` endpoint driven by a ``mode`` query
parameter. The version probe is unauthenticated-friendly on some setups
but normally needs the API key, so we always send it when present. The
API key can also come from a ``FILE__SABNZBD_API_KEY`` secret file per
LSIO convention (see app/secrets_util.py). The HTTP client is injected
for testability.
"""

from __future__ import annotations

import logging

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.sabnzbd")


class SABnzbdClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        # Fall back to the FILE__/env secret if no key was passed explicitly
        # (e.g. loaded straight from settings.json, which may be blank).
        self.api_key = api_key or resolve_secret("SABNZBD_API_KEY")
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
