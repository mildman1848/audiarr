"""Client for a Prowlarr indexer manager.

This client covers the health/status check used by the Settings "Indexers"
section (so users can verify their Prowlarr URL and API key) plus the
interactive release search and NZB grab used by the Arr-core loop.

Prowlarr uses the Servarr v3 API shape: ``GET /api/v1/system/status`` with
the API key in an ``X-Api-Key`` header. The key can also come from a
``PROWLARR_API_KEY`` / ``FILE__PROWLARR_API_KEY`` secret per LSIO convention
(see app/secrets_util.py), but only for the destination named by the
non-secret ``PROWLARR_URL`` setting (see ``resolve_prowlarr_api_key``). The
HTTP client is injected for testability.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from urllib.parse import parse_qsl, unquote_plus, urlsplit

import httpx

from app.secrets_util import resolve_secret

log = logging.getLogger("audiarr.connections.prowlarr")

# Raw C0/C1 control characters. URL parsers silently drop CR/LF/TAB, so this
# must be checked on the unmodified string before any parsing.
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_PROWLARR_PROXY_QUERY_KEYS = frozenset({"apikey", "link", "file"})
_BAD_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_WINDOWS_DRIVE_PATH = re.compile(r"[A-Za-z]:[\\/]")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 10
_MAX_URL_LENGTH = 4096


_DEFAULT_PORTS = {"http": 80, "https": 443}
_URL_FORBIDDEN_CHARS = re.compile(r"[\s\\]")


def normalize_prowlarr_base_url(url: str | None) -> tuple[str, str, int, str] | None:
    """``(scheme, host, effective port, base path)`` of a Prowlarr base URL.

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
        or CONTROL_CHARS.search(url)
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


def is_trusted_prowlarr_url(base_url: str | None) -> bool:
    """True when ``base_url`` is exactly the trusted ``PROWLARR_URL`` destination.

    ``PROWLARR_URL`` is a non-secret deployment setting. If it is unset or
    malformed nothing is trusted, so the environment key is never used.
    """
    trusted = normalize_prowlarr_base_url(os.environ.get("PROWLARR_URL"))
    if trusted is None:
        return False
    return trusted == normalize_prowlarr_base_url(base_url)


def resolve_prowlarr_api_key(stored: str | None, base_url: str | None) -> str:
    """The effective Prowlarr API key for requests to ``base_url``.

    A stored (or explicitly supplied) key always wins. The env/file secret
    (``PROWLARR_API_KEY`` / ``FILE__PROWLARR_API_KEY``) is a deployment
    secret, so it is only returned when ``base_url`` matches the trusted
    ``PROWLARR_URL``; a settings change can therefore not re-aim it. This is
    the key ``ProwlarrClient`` actually sends, so URL validation that compares
    against "the configured key" must use it, not the stored value.
    """
    if stored:
        return stored
    if not is_trusted_prowlarr_url(base_url):
        log.debug("Prowlarr environment API key not used: URL is not the trusted PROWLARR_URL")
        return ""
    return resolve_secret("PROWLARR_API_KEY") or ""


def url_origin(url: str) -> tuple[str, str, int | None] | None:
    """``(scheme, host, port)`` of an http(s) URL, or None if it has none."""
    try:
        parts = urlsplit(url.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        return parts.scheme, parts.hostname.lower(), parts.port
    except ValueError:
        return None


def is_prowlarr_download_path(url: str, prowlarr_url: str) -> bool:
    """True only for Prowlarr's canonical ``<base>/<indexer id>/download`` URL.

    Same-origin alone would let a caller point a client at any Prowlarr
    endpoint (e.g. ``/api/v1/...``); real result URLs always have this shape.
    Embedded credentials (``user:pass@``) are rejected as well.
    """
    try:
        parts = urlsplit(url.strip())
        if parts.username is not None or parts.password is not None:
            return False
        base = urlsplit(prowlarr_url.strip()).path.rstrip("/")
    except ValueError:
        return False
    return re.fullmatch(re.escape(base) + r"/\d+/download", parts.path) is not None


def is_safe_prowlarr_filename(name: str) -> bool:
    """True when ``name`` can't act as a filesystem path escape.

    Prowlarr's ``GetDownload`` URL-decodes ``file`` a second time and uses it as
    the download filename, so a double-encoded ``%252e%252e%252f`` becomes
    ``../``. Harmless embedded slashes (``AC/DC`` titles) stay valid; absolute
    paths (POSIX, drive-letter, UNC), ``.``/``..`` components and control
    characters do not.
    """
    if not name or CONTROL_CHARS.search(name):
        return False
    if name[0] in "/\\" or _WINDOWS_DRIVE_PATH.match(name):
        return False
    return not any(part.strip() in (".", "..") for part in re.split(r"[\\/]", name))


def has_canonical_prowlarr_proxy_query(url: str, api_key: str) -> bool:
    """True only for Prowlarr's generated proxy-download query.

    Prowlarr emits ``?apikey=<key>&link=<proxy token>&file=<filename>``. Anything
    else (extra or duplicate keys, a fragment, malformed percent escapes, or an
    API key other than the configured one) is rejected so a caller can't smuggle
    extra parameters through the Prowlarr download endpoint. Values are decoded
    before comparing, and neither the URL nor the key is ever reported.
    """
    if not api_key or "#" in url:
        return False
    try:
        query = urlsplit(url.strip()).query
        if not query or _BAD_PERCENT_ESCAPE.search(query):
            return False
        pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, errors="strict")
    except ValueError:  # includes UnicodeDecodeError from invalid UTF-8 escapes
        return False
    keys = [key for key, _ in pairs]
    if len(keys) != len(_PROWLARR_PROXY_QUERY_KEYS) or set(keys) != _PROWLARR_PROXY_QUERY_KEYS:
        return False
    values = dict(pairs)
    if not all(values.values()):
        return False
    try:
        # Prowlarr form-decodes ``file`` again after the framework did, so
        # validate both the once- and twice-decoded forms.
        second_pass = unquote_plus(values["file"], errors="strict")
    except ValueError:
        return False
    if not all(is_safe_prowlarr_filename(name) for name in (values["file"], second_pass)):
        return False
    return hmac.compare_digest(values["apikey"].encode(), api_key.encode())


def is_prowlarr_download_url(url: str, prowlarr_url: str, api_key: str) -> bool:
    """Full check that ``url`` is a proxy download URL of the configured Prowlarr."""
    if not url or CONTROL_CHARS.search(url) or len(url.strip()) > _MAX_URL_LENGTH:
        return False
    wanted = url_origin(url)
    return (
        wanted is not None
        and wanted == url_origin(prowlarr_url)
        and is_prowlarr_download_path(url, prowlarr_url)
        and has_canonical_prowlarr_proxy_query(url, api_key)
    )


def _same_origin(a: httpx.URL, b: httpx.URL) -> bool:
    return (a.scheme, a.host, a.port) == (b.scheme, b.host, b.port)


class ProwlarrClient:
    """Prowlarr API client.

    ``api_key=None`` falls back to ``PROWLARR_API_KEY`` /
    ``FILE__PROWLARR_API_KEY``, but only when ``base_url`` matches the trusted
    ``PROWLARR_URL``; otherwise no key is sent. An explicit empty string means
    "send no key" (used for test connections to a destination that is not a
    configured Prowlarr).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = (
            resolve_prowlarr_api_key(None, base_url) if api_key is None else api_key
        )
        self._client = client

    def _headers(self) -> dict[str, str]:
        if self.api_key:
            return {"X-Api-Key": self.api_key}
        return {}

    async def _get_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        # follow_redirects: Prowlarr's proxy download URL 301s to the actual
        # indexer NZB endpoint (e.g. file.treasure-maps.com/getnzb/...).
        return (
            httpx.AsyncClient(
                base_url=self.base_url, timeout=30.0, follow_redirects=True
            ),
            True,
        )

    @staticmethod
    def _normalize_release(raw: dict) -> dict:
        """Map a raw Prowlarr search result onto Audiarr's release shape."""
        categories: list[str] = []
        for cat in raw.get("categories") or []:
            if isinstance(cat, dict) and cat.get("name"):
                categories.append(str(cat["name"]))
        return {
            "guid": raw.get("guid"),
            "indexer_id": raw.get("indexerId"),
            "indexer": raw.get("indexer"),
            "title": raw.get("title"),
            "size": raw.get("size"),
            "seeders": raw.get("seeders"),
            "leechers": raw.get("leechers"),
            "publish_date": raw.get("publishDate"),
            "download_url": raw.get("downloadUrl"),
            "magnet_url": raw.get("magnetUrl"),
            "protocol": raw.get("protocol"),
            "age": raw.get("age"),
            "categories": categories,
        }

    async def search(self, query: str, limit: int = 50) -> list[dict]:
        """Run an interactive release search, returning normalized dicts.

        Never raises: any transport error, malformed base URL, non-200
        response (redirects are never followed, so a 3xx counts as one), or
        unexpected JSON shape is logged and yields an empty list so the caller
        can surface "no results" instead of a 500.
        """
        client: httpx.AsyncClient | None = None
        owns_client = False
        try:
            client, owns_client = await self._get_client()
            # follow_redirects=False even on an injected client: httpx would
            # otherwise forward the custom X-Api-Key header cross-origin.
            response = await client.get(
                "/api/v1/search",
                params={"query": query, "type": "search", "limit": limit},
                headers=self._headers(),
                follow_redirects=False,
            )
            if response.status_code != 200:
                log.warning("Prowlarr search: HTTP %s", response.status_code)
                return []
            try:
                data = response.json()
            except ValueError:
                log.warning("Prowlarr search: response was not JSON")
                return []
            if not isinstance(data, list):
                log.warning("Prowlarr search: unexpected JSON shape")
                return []
            releases = [self._normalize_release(r) for r in data if isinstance(r, dict)]
            log.debug("Prowlarr search for %r -> %d release(s)", query, len(releases))
            return releases
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            log.warning("Prowlarr search failed: %s", exc)
            return []
        finally:
            if owns_client and client is not None:
                await client.aclose()

    async def download_nzb(self, download_url: str) -> bytes | None:
        """Fetch the NZB file for a usenet release via Prowlarr.

        ``download_url`` is the ``downloadUrl`` from a Prowlarr search result,
        which is already a self-contained proxy URL
        (``{base}/{n}/download?apikey=...&link=<token>&file=<name>``). It is
        usually caller-supplied, so it fails closed: anything that is not that
        exact shape on the configured Prowlarr origin (see
        ``is_prowlarr_download_url``) is rejected before any request is made,
        because httpx would otherwise send the API key header to whatever
        absolute URL it was given.

        Prowlarr's proxy URL redirects to the actual indexer. Redirects are
        followed manually: the ``X-Api-Key`` header is sent only while the
        hop stays on the configured Prowlarr origin and is dropped for good
        once a redirect leaves it.

        A release that resolves to a magnet link comes back as a ``magnet:``
        body (or redirect). We only support usenet here, so that is treated as
        a failure.

        Returns the raw NZB bytes, or None on any failure (invalid URL,
        transport error, non-200, empty body, magnet redirect).
        """
        if not is_prowlarr_download_url(download_url, self.base_url, self.api_key or ""):
            log.warning("Prowlarr NZB download: rejected a non-Prowlarr download URL")
            return None
        try:
            start = httpx.URL(download_url.strip())
            origin = httpx.URL(self.base_url)
        except httpx.InvalidURL:
            log.warning("Prowlarr NZB download: invalid URL")
            return None
        if not _same_origin(start, origin):
            log.warning("Prowlarr NZB download: rejected a non-Prowlarr download URL")
            return None

        client, owns_client = await self._get_client()
        try:
            response = await self._get_following_redirects(client, start, origin)
            if response is None:
                return None
            if response.status_code != 200:
                log.warning("Prowlarr NZB download: HTTP %s", response.status_code)
                return None
            content = response.content
            if not content:
                log.warning("Prowlarr NZB download: empty response body")
                return None
            if content[:7].lower().startswith(b"magnet:"):
                log.info("Prowlarr NZB download: release is a magnet link, not usenet")
                return None
            log.debug("Prowlarr NZB download -> %d byte(s)", len(content))
            return content
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            log.warning("Prowlarr NZB download failed: %s", exc)
            return None
        finally:
            if owns_client:
                await client.aclose()

    async def _get_following_redirects(
        self, client: httpx.AsyncClient, url: httpx.URL, origin: httpx.URL
    ) -> httpx.Response | None:
        """GET ``url``, following redirects without leaking the API key.

        The header is attached only to hops on ``origin`` and, once a redirect
        has left it, never again. Returns None for a magnet redirect, a
        non-http(s) target, a malformed ``Location`` or too many hops.
        """
        send_key = True
        for _ in range(_MAX_REDIRECTS + 1):
            if send_key and not _same_origin(url, origin):
                send_key = False
            response = await client.get(
                url,
                headers=self._headers() if send_key else {},
                follow_redirects=False,
            )
            location = response.headers.get("location")
            if response.status_code not in _REDIRECT_STATUSES or not location:
                return response
            if location.strip().lower().startswith("magnet:"):
                log.info("Prowlarr NZB download: release is a magnet link, not usenet")
                return None
            try:
                url = response.url.join(location)
            except httpx.InvalidURL:
                log.warning("Prowlarr NZB download: malformed redirect target")
                return None
            if url.scheme not in ("http", "https"):
                log.warning("Prowlarr NZB download: unsupported redirect scheme")
                return None
        log.warning("Prowlarr NZB download: too many redirects")
        return None

    async def status(self) -> dict | None:
        """Return the Prowlarr system status dict, or None on failure.

        Success means: HTTP 200 and a parseable JSON object. Prowlarr's
        status payload carries fields like ``version`` and ``appName``;
        we return the whole dict so the caller can surface the version.
        Redirects are never followed (a 3xx is a failure) and a malformed base
        URL yields None instead of raising.
        """
        client: httpx.AsyncClient | None = None
        owns_client = False
        try:
            client, owns_client = await self._get_client()
            # follow_redirects=False even on an injected client: httpx would
            # otherwise forward the custom X-Api-Key header cross-origin.
            response = await client.get(
                "/api/v1/system/status",
                headers=self._headers(),
                follow_redirects=False,
            )
            if response.status_code != 200:
                log.warning("Prowlarr status check: HTTP %s", response.status_code)
                return None
            try:
                data = response.json()
            except ValueError:
                log.warning("Prowlarr status check: response was not JSON")
                return None
            if not isinstance(data, dict):
                log.warning("Prowlarr status check: unexpected JSON shape")
                return None
            return data
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            log.warning("Prowlarr status check failed: %s", exc)
            return None
        finally:
            if owns_client and client is not None:
                await client.aclose()
