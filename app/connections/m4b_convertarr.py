"""Client for an external m4b-convertarr instance.

Design decision (see docs/design/architecture.md "Conversion strategy"):
Audiarr delegates M4B conversion to the separate m4b-convertarr service
via HTTP instead of embedding a converter. That keeps Audiarr's container
small, lets conversion scale/fail independently, and reuses a
already-hardened LSIO/s6 image rather than duplicating ffmpeg/AtomicParsley
tooling here. Embedding a converter directly into Audiarr remains a
possible future option once the external-first integration has proven
itself; see docs/design/architecture.md for the trade-offs.

The API key can come from the settings document or from
``M4B_CONVERTARR_API_KEY`` / ``FILE__M4B_CONVERTARR_API_KEY``, the latter only
for the destination named by the non-secret ``M4B_CONVERTARR_URL`` (see
``resolve_m4b_convertarr_api_key``).
"""

from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlsplit

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.m4b_convertarr")

_DEFAULT_PORTS = {"http": 80, "https": 443}
# Raw C0/C1 control characters, checked before parsing because URL parsers
# silently drop CR/LF/TAB.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_URL_FORBIDDEN_CHARS = re.compile(r"[\s\\]")


def normalize_m4b_convertarr_base_url(url: str | None) -> tuple[str, str, int, str] | None:
    """``(scheme, host, effective port, base path)`` of a m4b-convertarr base URL.

    Strict on purpose, since the result decides where a deployment secret may
    be sent: only ``http``/``https`` with a host, no userinfo, query, fragment,
    control characters, whitespace or backslashes, and a valid port. Scheme and
    host are case-folded, a missing port becomes the scheme default, and one
    trailing slash on the path is ignored; nothing else is normalized. Returns
    None for anything unset or malformed.
    """
    if not url:
        return None
    if _CONTROL_CHARS.search(url) or _URL_FORBIDDEN_CHARS.search(url) or "?" in url or "#" in url:
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


def is_trusted_m4b_convertarr_url(base_url: str | None) -> bool:
    """True when ``base_url`` is exactly the trusted ``M4B_CONVERTARR_URL`` destination.

    ``M4B_CONVERTARR_URL`` is a non-secret deployment setting. If it is unset or
    malformed nothing is trusted, so the environment key is never used.
    """
    trusted = normalize_m4b_convertarr_base_url(os.environ.get("M4B_CONVERTARR_URL"))
    if trusted is None:
        return False
    return trusted == normalize_m4b_convertarr_base_url(base_url)


def resolve_m4b_convertarr_api_key(base_url: str | None) -> str:
    """The deployment API key (env/``FILE__``) for requests to ``base_url``.

    Returned only when ``base_url`` matches the trusted ``M4B_CONVERTARR_URL``, so a
    settings change can not re-aim the deployment secret. Otherwise "".
    """
    if not is_trusted_m4b_convertarr_url(base_url):
        log.debug("m4b-convertarr environment API key not used: URL is not the trusted M4B_CONVERTARR_URL")
        return ""
    return resolve_secret("M4B_CONVERTARR_API_KEY") or ""


class M4BConvertarrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        webhook_path: str = "/api/v1/convert",
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.webhook_path = webhook_path
        # None falls back to the FILE__/env secret, but only for the
        # destination named by M4B_CONVERTARR_URL; "" is an explicit "no key"
        # so a connection test never hands the deployment secret to an
        # arbitrary URL.
        self.api_key = resolve_m4b_convertarr_api_key(base_url) if api_key is None else api_key
        self._client = client

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(base_url=self.base_url, timeout=10.0), True

    async def health(self) -> bool:
        client, owns_client = await self._get_client()
        try:
            response = await client.get("/health", headers=self._headers())
            return response.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("m4b-convertarr health check failed: %s", exc)
            return False
        finally:
            if owns_client:
                await client.aclose()

    async def submit_conversion(self, source_path: str, target_format: str = "m4b") -> bool:
        """POST a conversion job/webhook to m4b-convertarr. Placeholder payload shape."""
        client, owns_client = await self._get_client()
        try:
            response = await client.post(
                self.webhook_path,
                json={"source_path": source_path, "target_format": target_format},
                headers=self._headers(),
            )
            return response.status_code in (200, 201, 202)
        except httpx.HTTPError as exc:
            log.warning("m4b-convertarr conversion submission failed: %s", exc)
            return False
        finally:
            if owns_client:
                await client.aclose()
