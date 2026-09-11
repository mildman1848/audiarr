"""Authentication primitives and ASGI middleware.

Implements Radarr/Sonarr-style "Security" tab semantics: forms login
(session cookie) plus API-key auth (``X-Api-Key`` header). Stdlib only —
no new dependencies.

When ``settings.auth.method == "none"`` (the default) the middleware is a
complete no-op: every request passes through untouched.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import secrets
import time
from collections import deque
from collections.abc import Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import load_settings

log = logging.getLogger("audiarr.auth")

PBKDF2_ITERATIONS = 390_000
SESSION_COOKIE_NAME = "audiarr_session"
DEFAULT_SESSION_TTL_SECONDS = 7 * 24 * 3600

LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 15 * 60

# Paths that never require authentication, even when auth is enabled.
# Entries ending in "/" are treated as prefixes; others must match exactly.
EXEMPT_PATHS: tuple[str, ...] = (
    "/health",
    "/static",
    "/login",
    "/api/v1/auth/login",
    "/api/v1/webhooks/",
)


def _is_exempt(path: str) -> bool:
    for exempt in EXEMPT_PATHS:
        if exempt.endswith("/"):
            if path.startswith(exempt):
                return True
        elif path == exempt or path.startswith(exempt + "/"):
            return True
    return False


# -- password hashing ---------------------------------------------------------


def hash_password(pw: str) -> str:
    """Hash ``pw`` as ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    """Constant-time verify ``pw`` against a hash produced by :func:`hash_password`."""
    try:
        scheme, iterations_str, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


# -- login rate limiting ------------------------------------------------------


class LoginRateLimiter:
    """In-memory sliding-window brute-force guard for the login endpoint.

    Homelab scale — stdlib only, no redis/slowapi. Tracks failed-attempt
    timestamps per client IP in a ``dict[str, deque[float]]``; each call to
    :meth:`check`/:meth:`record_failure` opportunistically drops timestamps
    older than the window (and the IP's entry entirely once it is empty) so
    memory stays bounded without a background thread. Successful logins do
    not clear the window themselves — old failures simply age out.
    """

    def __init__(
        self,
        max_attempts: int = LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
        window_seconds: int = LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, client_ip: str, now: float) -> deque[float]:
        """Drop timestamps older than the window; return the live deque."""
        attempts = self._failures.setdefault(client_ip, deque())
        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del self._failures[client_ip]
        return attempts

    def check(self, client_ip: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for ``client_ip``."""
        now = time.time()
        attempts = self._prune(client_ip, now)
        if len(attempts) < self.max_attempts:
            return True, 0
        retry_after = max(1, math.ceil(attempts[0] + self.window_seconds - now))
        return False, retry_after

    def record_failure(self, client_ip: str) -> None:
        """Record a failed login attempt for ``client_ip``."""
        now = time.time()
        attempts = self._prune(client_ip, now)
        attempts.append(now)
        self._failures[client_ip] = attempts

    def reset(self, client_ip: str) -> None:
        """Clear all recorded failures for ``client_ip`` (successful login)."""
        self._failures.pop(client_ip, None)


# -- session tokens -------------------------------------------------------------


def _session_key(api_key: str) -> bytes:
    """Derive the session-signing key from the API key (no separate secret)."""
    return hashlib.sha256(api_key.encode("utf-8")).digest()


def make_session_token(username: str, ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS) -> str:
    """Return a signed ``exp_ts|username|sig_hex`` session token.

    Keyed by ``sha256(api_key)`` so no separate session secret needs to be
    generated or persisted — rotating the API key also invalidates
    outstanding sessions.
    """
    api_key = load_settings().auth.api_key
    exp = int(time.time()) + ttl_seconds
    payload = f"{exp}|{username}"
    sig = hmac.new(_session_key(api_key), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def verify_session_token(token: str) -> str | None:
    """Return the username if ``token`` is validly signed and unexpired."""
    api_key = load_settings().auth.api_key
    try:
        exp_str, username, sig = token.split("|", 2)
        exp = int(exp_str)
    except ValueError:
        return None

    payload = f"{exp}|{username}"
    expected_sig = hmac.new(
        _session_key(api_key), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    if exp < int(time.time()):
        return None
    return username


# -- middleware -----------------------------------------------------------------


class AuthMiddleware:
    """Pure ASGI middleware enforcing session-cookie or API-key auth.

    ``get_auth_settings`` is a zero-arg callable returning the current
    :class:`app.models.settings.Settings` object (``app.config.load_settings``
    in production) so settings changes take effect on the next request
    without restarting the process, and so this module never imports
    ``app.config`` at call sites that would create an import cycle with
    ``app.main``.
    """

    def __init__(self, app: ASGIApp, get_auth_settings: Callable[[], object]) -> None:
        self.app = app
        self.get_auth_settings = get_auth_settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = self.get_auth_settings()
        auth = settings.auth  # type: ignore[attr-defined]

        if auth.method == "none":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if _is_exempt(path):
            await self.app(scope, receive, send)
            return

        if self._is_authorized(scope, auth):
            await self.app(scope, receive, send)
            return

        if path.startswith("/api/"):
            await self._send_json_401(send)
        else:
            await self._send_redirect_to_login(send)

    @staticmethod
    def _is_authorized(scope: Scope, auth: object) -> bool:
        headers = dict(scope.get("headers") or [])

        api_key = getattr(auth, "api_key", "")
        header_key = headers.get(b"x-api-key", b"").decode("latin-1")
        if api_key and header_key and hmac.compare_digest(header_key, api_key):
            return True

        cookie_header = headers.get(b"cookie", b"").decode("latin-1")
        token = _parse_cookie(cookie_header, SESSION_COOKIE_NAME)
        if token and verify_session_token(token):
            return True

        return False

    @staticmethod
    async def _send_json_401(send: Send) -> None:
        body = b'{"detail": "Unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _send_redirect_to_login(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [(b"location", b"/login")],
            }
        )
        await send({"type": "http.response.body", "body": b""})


def _parse_cookie(cookie_header: str, name: str) -> str | None:
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        if key.strip() == name:
            return value.strip()
    return None
