"""Logging setup.

Uses plain stdlib logging so container logs are readable via `docker logs`
without needing a log aggregator. Level is controlled by settings (or the
AUDIARR_LOG_LEVEL env var before settings.json exists, e.g. during very
first boot).

Besides stdout, records are mirrored into a small bounded in-memory ring
buffer (see :class:`RedactingRingBufferHandler`) that backs the read-only
System -> Logs view. The buffer is process-local and never persisted: there
is no log file, and it is empty after every restart.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections import deque
from datetime import UTC, datetime

REDACTED = "[REDACTED]"

# Ring buffer size and per-record message cap keep memory use bounded
# (worst case roughly LOG_BUFFER_CAPACITY * MAX_MESSAGE_CHARS characters).
LOG_BUFFER_CAPACITY = 1000
MAX_MESSAGE_CHARS = 8000

# Key names whose values are secrets. The optional prefix covers compound
# names such as ``client_secret``, ``access_token`` or ``X-Api-Key``. A bare
# ``key`` is deliberately NOT a label (it would redact ordinary words such as
# "sort key"); only qualified key names are listed.
_SECRET_KEY = (
    r"[A-Za-z0-9_.-]*"
    r"(?:password|passwd|passphrase|secret|token|jwt|credentials?"
    r"|api[_-]?key|webhook[_-]?key|private[_-]?key|access[_-]?key|signing[_-]?key"
    r"|session[_-]?id|session)"
)

# ``Authorization: Bearer abc`` / ``Cookie: a=b; c=d``: header values are
# redacted wholesale (cookies to end of line, auth schemes keep their name).
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization)(['\"]?\s*[:=]\s*['\"]?)"
    r"(?:(bearer|basic|token|digest)\s+)?[^\s'\",;&]+"
)
_COOKIE_HEADER_RE = re.compile(r"(?i)\b(cookie|set-cookie)(['\"]?\s*[:=]\s*)[^\r\n]*")
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
# ``password=abc``, ``"api_key": "abc"``, ``X-Api-Key: abc``, ``?token=abc&x=1``.
_KEY_VALUE_RE = re.compile(
    rf"(?i)\b({_SECRET_KEY})(['\"]?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s'\",;&]+)"
)
# PEM private key blocks (multi-line), e.g. ``-----BEGIN RSA PRIVATE KEY-----``.
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.DOTALL
)
# Unlabelled JWTs: three base64url segments, the first being a ``{"`` header.
# Payload and signature may be short or empty (``e30`` is ``{}``; ``alg=none``
# tokens end in a bare dot), so only the ``eyJ`` header prefix and two dots are
# required.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*")
# ``https://user:password@host`` credentials embedded in URLs.
_URL_CREDENTIALS_RE = re.compile(r"(://[^/\s:@]+:)[^@\s/]+(@)")


def _redact_auth_header(match: re.Match[str]) -> str:
    scheme = f"{match.group(3)} " if match.group(3) else ""
    return f"{match.group(1)}{match.group(2)}{scheme}{REDACTED}"


def _redact_key_value(match: re.Match[str]) -> str:
    value = match.group(3)
    quote = value[0] if value[0] in "\"'" else ""
    return f"{match.group(1)}{match.group(2)}{quote}{REDACTED}{quote}"


def redact_secrets(text: str) -> str:
    """Replace secret values in free text with ``[REDACTED]``, keeping context."""
    text = _PRIVATE_KEY_BLOCK_RE.sub(REDACTED, text)
    text = _AUTH_HEADER_RE.sub(_redact_auth_header, text)
    text = _COOKIE_HEADER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _KEY_VALUE_RE.sub(_redact_key_value, text)
    text = _JWT_RE.sub(REDACTED, text)
    return _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(2)}", text)


class RedactingRingBufferHandler(logging.Handler):
    """Keep the newest N log records in memory as redacted plain dicts.

    Only the formatted message plus exception/stack text is stored (never
    ``record.args`` or the LogRecord itself), and every string field (message,
    exception text and logger name) is redacted *before* entering the buffer, so
    nothing downstream can leak a secret.
    """

    def __init__(self, capacity: int = LOG_BUFFER_CAPACITY) -> None:
        super().__init__(level=logging.NOTSET)
        self.capacity = capacity
        self._records: deque[dict[str, str]] = deque(maxlen=capacity)
        self._buffer_lock = threading.Lock()
        self._formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            try:
                message = record.getMessage()
            except Exception:  # noqa: BLE001 -- malformed %-args must not drop the record
                message = str(record.msg)
            parts = [message]
            if record.exc_info and record.exc_info[0] is not None:
                parts.append(self._formatter.formatException(record.exc_info))
            if record.stack_info:
                parts.append(self._formatter.formatStack(record.stack_info))
            text = redact_secrets("\n".join(parts))
            if len(text) > MAX_MESSAGE_CHARS:
                text = text[:MAX_MESSAGE_CHARS] + "…[truncated]"
            entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(
                    timespec="milliseconds"
                ),
                "level": record.levelname,
                "logger": redact_secrets(record.name),
                "message": text,
            }
            with self._buffer_lock:
                self._records.append(entry)
        except Exception:  # noqa: BLE001 -- logging must never raise into callers
            self.handleError(record)

    def snapshot(self, limit: int) -> list[dict[str, str]]:
        """Return up to ``limit`` newest records, newest first (copies)."""
        if limit <= 0:
            return []
        with self._buffer_lock:
            newest_first = list(self._records)[::-1][:limit]
        return [dict(entry) for entry in newest_first]

    def clear(self) -> None:
        with self._buffer_lock:
            self._records.clear()


# Process-wide buffer shared by every configure_logging() call.
LOG_BUFFER = RedactingRingBufferHandler()


def install_log_buffer(logger: logging.Logger | None = None) -> RedactingRingBufferHandler:
    """Attach the shared ring buffer to the root logger exactly once."""
    target = logger or logging.getLogger()
    if LOG_BUFFER not in target.handlers:
        target.addHandler(LOG_BUFFER)
    return LOG_BUFFER


def configure_logging(level: str | None = None) -> None:
    resolved = (level or os.environ.get("AUDIARR_LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, resolved, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    install_log_buffer()
    # Never log secret values; provider/connection modules only log that a
    # secret was resolved and from which source (see app/secrets_util.py).
    # The in-memory buffer additionally redacts common secret patterns.
