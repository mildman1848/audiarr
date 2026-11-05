"""Settings read/write endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.auth import hash_password
from app.config import load_settings, save_settings
from app.connect import sanitize_last_error
from app.models.settings import ConnectNotification, DownloadClient, Indexer, Settings

log = logging.getLogger("audiarr.api.settings")

router = APIRouter()

# auth.password is already Field(exclude=True) (write-only, never
# persisted). auth.password_hash is a plain, persisted field so it
# survives a settings save (see app/models/settings.py for why it can't
# also use Field(exclude=True)); it is excluded here, at the API-response
# boundary, so GET/PUT never echo it back.
#
# Download-client secrets (api_key, password) are write-only too: they are
# stored, but never returned. A blank value on PUT keeps the stored secret
# for the matching client (see _preserve_download_client_secrets).
#
# The Prowlarr indexer api_key is write-only too: responses expose only the
# derived api_key_set flag. A blank value on PUT keeps the stored key of the
# matching indexer, but only while its url is unchanged (see
# _preserve_indexer_secrets). auth.api_key is intentionally NOT covered.
#
# Connect webhook header_value is write-only as well; responses expose only
# the derived header_value_set flag. A blank value on PUT keeps the stored
# secret of the entry with the same stable id, but only while its url and
# header_name are unchanged (see _preserve_connect_header_secrets). A legacy
# entry with a blank/duplicate id cannot inherit; a save that would silently
# drop its secret is rejected instead (see _reject_connect_secret_loss).
#
# The integration secrets (Audiobookshelf/m4b-convertarr api_key, Liberatarr
# token, conversion api_key/webhook_api_key, translation api_key) are
# write-only as well; responses expose only the derived *_set flags (see
# _preserve_integration_secrets).
_SETTINGS_RESPONSE_EXCLUDE = {
    "auth": {"password_hash"},
    "download_clients": {"__all__": {"api_key", "password"}},
    "indexers": {"__all__": {"api_key"}},
    "connect": {"__all__": {"header_value"}},
    "connections": {
        "audiobookshelf": {"api_key"},
        "m4b_convertarr": {"api_key"},
        "liberatarr": {"token"},
    },
    "conversion": {"api_key", "webhook_api_key"},
    "translation": {"api_key"},
}

# Outbound secrets sent to a configured destination: (section path, url
# field, secret field). A blank value on save keeps the stored secret only
# while the destination is unchanged.
_OUTBOUND_SECRETS = (
    ("connections.audiobookshelf", "url", "api_key"),
    ("connections.m4b_convertarr", "url", "api_key"),
    ("connections.liberatarr", "base_url", "token"),
    ("conversion", "base_url", "api_key"),
    ("translation", "base_url", "api_key"),
)

# Server-owned delivery metadata of a Connect webhook; never taken from a
# client (see _restore_connect_delivery_metadata).
_CONNECT_DELIVERY_FIELDS = (
    "last_event",
    "last_status",
    "last_status_code",
    "last_error",
    "last_delivered_at",
)


def _preserve_connect_header_secrets(incoming: Settings, stored: Settings) -> None:
    """Apply write-only semantics to ``connect[].header_value`` on save.

    A blank incoming value inherits the stored secret only from the stored
    entry with the same nonblank ``id``; name and order are never used. An
    unknown/blank/duplicated id inherits nothing, and a stored id that
    appears more than once is ambiguous and is never inherited from. A
    blank ``header_name`` clears the header entirely (name and value). A
    nonblank incoming value always replaces the stored one.

    The inherited secret is bound to its destination: it is kept only when
    ``url`` and ``header_name`` are unchanged (exact match after trimming
    surrounding whitespace). Otherwise a blank value would re-aim a stored
    secret at a new target, so the save is rejected with a 422 asking for
    the secret to be re-entered. The message never includes URL or secret.
    """
    stored_ids = [c.id for c in stored.connect if c.id]
    previous: dict[str, ConnectNotification] = {
        c.id: c for c in stored.connect if c.id and stored_ids.count(c.id) == 1
    }
    seen: set[str] = set()
    for notif in incoming.connect:
        first_use = bool(notif.id) and notif.id not in seen
        seen.add(notif.id)
        if not notif.header_name.strip():
            notif.header_name = ""
            notif.header_value = ""
            continue
        if notif.header_value or not first_use:
            continue
        old = previous.get(notif.id)
        if old is None or not old.header_value:
            continue
        if (
            notif.url.strip() != old.url.strip()
            or notif.header_name.strip() != old.header_name.strip()
        ):
            raise HTTPException(
                422,
                "The webhook URL or header name changed: re-enter the header value "
                "to save it with the new destination",
            )
        notif.header_value = old.header_value


def _reject_connect_secret_loss(incoming: Settings, stored: Settings) -> None:
    """Fail closed when a save would silently erase a legacy Connect secret.

    A stored secret whose entry has a blank or duplicated ``id`` can never be
    inherited (see ``_preserve_connect_header_secrets``), so a blank incoming
    value for it would erase the secret without notice. The incoming entry is
    recognised only by an exact ``url`` + ``header_name`` match (surrounding
    whitespace ignored) with such a stored entry; that match is a detection
    signal, never a reason to inherit. Run after the inheritance step, so an
    entry that did inherit or got a new value is not flagged. A blank
    ``header_name`` (header intentionally cleared) and removed entries are
    left alone, and a (url, header_name) pair shared by several stored
    secrets is too ambiguous to attribute and is not flagged. The message
    never includes URL or secret.
    """

    def target(notif: ConnectNotification) -> tuple[str, str]:
        return notif.url.strip(), notif.header_name.strip()

    stored_ids = [c.id for c in stored.connect if c.id]
    with_secret = [c for c in stored.connect if c.header_value and c.header_name.strip()]
    targets = [target(c) for c in with_secret]
    unmatchable = {
        target(c)
        for c in with_secret
        if (not c.id or stored_ids.count(c.id) != 1) and targets.count(target(c)) == 1
    }
    for notif in incoming.connect:
        if notif.header_value or not notif.header_name.strip():
            continue
        if target(notif) in unmatchable:
            raise HTTPException(
                422,
                "A stored webhook header value could not be matched to this entry: "
                "re-enter the header value to save it",
            )


def _destination(url: str) -> str:
    """Comparable form of a destination URL (surrounding space/slash ignored)."""
    return url.strip().rstrip("/")


def _section(settings: Settings, path: str):
    section = settings
    for part in path.split("."):
        section = getattr(section, part)
    return section


def _preserve_integration_secrets(incoming: Settings, stored: Settings) -> None:
    """Apply write-only semantics to the integration secrets on save.

    A blank incoming outbound secret keeps the stored one, but only while the
    destination (``url``/``base_url``, surrounding space and trailing slash
    ignored) is unchanged. A blank secret with a changed destination and a
    saved secret would re-aim it at a new target, so the save is rejected
    with a 422 asking for the secret to be re-entered; the message never
    includes URL or secret. A non-blank value always wins. The inbound
    ``conversion.webhook_api_key`` is not sent anywhere: a blank value keeps
    the stored one independently of ``conversion.base_url``. Every check
    runs before the first assignment, so a rejected save mutates nothing.
    """
    inherit: list[tuple[object, str, str]] = []
    for path, url_field, secret_field in _OUTBOUND_SECRETS:
        new = _section(incoming, path)
        old = _section(stored, path)
        saved = getattr(old, secret_field)
        if getattr(new, secret_field) or not saved:
            continue
        if _destination(getattr(new, url_field)) != _destination(getattr(old, url_field)):
            raise HTTPException(
                422,
                f"The {path.rsplit('.', 1)[-1]} URL changed: re-enter its secret "
                "to save it with the new destination",
            )
        inherit.append((new, secret_field, saved))
    if not incoming.conversion.webhook_api_key:
        inherit.append(
            (incoming.conversion, "webhook_api_key", stored.conversion.webhook_api_key)
        )
    for section, field, value in inherit:
        setattr(section, field, value)


def _restore_connect_delivery_metadata(incoming: Settings, stored: Settings) -> None:
    """Take Connect ``last_*`` delivery metadata from the persisted webhook.

    These fields are written only by the dispatcher/test endpoint, never by a
    client. An incoming entry inherits them from the stored entry only when
    its nonblank ``id`` occurs exactly once in both lists; a new, blank or
    ambiguous id gets them cleared. ``last_error`` is sanitized afterwards.
    """
    stored_ids = [c.id for c in stored.connect if c.id.strip()]
    incoming_ids = [c.id for c in incoming.connect if c.id.strip()]
    previous = {
        c.id: c
        for c in stored.connect
        if c.id.strip() and stored_ids.count(c.id) == 1 and incoming_ids.count(c.id) == 1
    }
    for notif in incoming.connect:
        old = previous.get(notif.id)
        for field in _CONNECT_DELIVERY_FIELDS:
            value = (
                getattr(old, field)
                if old is not None
                else ConnectNotification.model_fields[field].default
            )
            setattr(notif, field, value)
        notif.last_error = sanitize_last_error(notif.last_error)


def _preserve_indexer_secrets(incoming: Settings, stored: Settings) -> None:
    """Fill a blank write-only indexer ``api_key`` from the stored entry.

    Entries are matched by ``(type, name)`` when that pair is unique on both
    sides. A renamed entry is matched by type only when unambiguous (exactly
    one unmatched stored and one unmatched incoming indexer of that type); if
    a stored key would be silently dropped because the match is ambiguous,
    the save is rejected with a 422.

    The inherited key is bound to its destination: it is kept only while
    ``url`` is unchanged. A blank key on a changed ``url`` would re-aim the
    stored secret at a new target, so that is rejected with a 422 asking for
    the key to be re-entered. A non-blank value always wins. Every check runs
    before the first assignment, so a rejected save mutates nothing.
    """
    stored_count: dict[tuple[str, str], int] = {}
    for old in stored.indexers:
        stored_count[(old.type, old.name)] = stored_count.get((old.type, old.name), 0) + 1
    incoming_count: dict[tuple[str, str], int] = {}
    for new in incoming.indexers:
        incoming_count[(new.type, new.name)] = incoming_count.get((new.type, new.name), 0) + 1

    matched: dict[int, Indexer] = {}
    for idx, new in enumerate(incoming.indexers):
        key = (new.type, new.name)
        if stored_count.get(key) == 1 and incoming_count[key] == 1:
            matched[idx] = next(o for o in stored.indexers if (o.type, o.name) == key)
    used = {id(old) for old in matched.values()}

    for type_ in {i.type for i in incoming.indexers}:
        new_idx = [
            i for i, n in enumerate(incoming.indexers) if n.type == type_ and i not in matched
        ]
        old_left = [o for o in stored.indexers if o.type == type_ and id(o) not in used]
        if len(new_idx) == 1 and len(old_left) == 1:
            matched[new_idx[0]] = old_left[0]
            continue
        if any(
            not incoming.indexers[i].api_key and any(o.api_key for o in old_left)
            for i in new_idx
        ):
            raise HTTPException(
                422,
                f"Ambiguous {type_} indexer rename: re-enter its API key "
                "or save one indexer change at a time",
            )

    inherit: list[tuple[Indexer, str]] = []
    for idx, old in matched.items():
        new = incoming.indexers[idx]
        if new.api_key or not old.api_key:
            continue
        if _destination(new.url) != _destination(old.url):
            raise HTTPException(
                422,
                "The indexer URL changed: re-enter the API key to save it "
                "with the new destination",
            )
        inherit.append((new, old.api_key))
    for new, key in inherit:
        new.api_key = key


def _preserve_download_client_secrets(incoming: Settings, stored: Settings) -> None:
    """Fill blank write-only download-client secrets from the stored entry.

    Entries are matched by ``(type, name)``. A renamed entry is matched by
    type only when that is unambiguous (exactly one unmatched stored and one
    unmatched incoming client of that type). If a stored secret would be
    silently dropped because the match is ambiguous, the save is rejected
    with a 422 instead. A non-blank value always wins.

    An inherited secret is bound to its destination: when a blank
    ``api_key``/``password`` would be carried over to a changed ``base_url()``
    the save is rejected with a 422 asking for the secret to be re-entered.
    """
    previous = {(c.type, c.name): c for c in stored.download_clients}
    matched: dict[int, DownloadClient] = {}
    for idx, client in enumerate(incoming.download_clients):
        old = previous.get((client.type, client.name))
        if old is not None:
            matched[idx] = old
    used = {id(old) for old in matched.values()}

    for type_ in {c.type for c in incoming.download_clients}:
        new_idx = [
            i for i, c in enumerate(incoming.download_clients)
            if c.type == type_ and i not in matched
        ]
        old_left = [c for c in stored.download_clients if c.type == type_ and id(c) not in used]
        if len(new_idx) == 1 and len(old_left) == 1:
            matched[new_idx[0]] = old_left[0]
            continue
        would_lose = any(
            (not incoming.download_clients[i].api_key and any(c.api_key for c in old_left))
            or (not incoming.download_clients[i].password and any(c.password for c in old_left))
            for i in new_idx
        )
        if would_lose:
            raise HTTPException(
                422,
                f"Ambiguous {type_} download client rename: re-enter its API key / "
                "password or save one client change at a time",
            )

    for idx, old in matched.items():
        client = incoming.download_clients[idx]
        inherits = (not client.api_key and old.api_key) or (not client.password and old.password)
        if inherits and client.base_url() != old.base_url():
            raise HTTPException(
                422,
                f"The {client.type} download client URL changed: re-enter its API key / "
                "password to save it with the new destination",
            )

    for idx, old in matched.items():
        client = incoming.download_clients[idx]
        if not client.api_key:
            client.api_key = old.api_key
        if not client.password:
            client.password = old.password


@router.get("/api/v1/settings", response_model=Settings, response_model_exclude=_SETTINGS_RESPONSE_EXCLUDE)
async def get_settings() -> Settings:
    return load_settings()


@router.put("/api/v1/settings", response_model=Settings, response_model_exclude=_SETTINGS_RESPONSE_EXCLUDE)
async def put_settings(settings: Settings) -> Settings:
    stored = load_settings()
    if settings.auth.password:
        settings.auth.password_hash = hash_password(settings.auth.password)
    else:
        # Empty password on PUT keeps whatever hash is already stored.
        settings.auth.password_hash = stored.auth.password_hash
    settings.auth.password = ""
    _preserve_download_client_secrets(settings, stored)
    _preserve_indexer_secrets(settings, stored)
    _preserve_connect_header_secrets(settings, stored)
    _reject_connect_secret_loss(settings, stored)
    _preserve_integration_secrets(settings, stored)
    # last_* is server-written delivery metadata: a caller must not be able
    # to forge it or store arbitrary (e.g. URL-bearing) text in last_error.
    _restore_connect_delivery_metadata(settings, stored)

    save_settings(settings)
    log.info("Settings updated via API")
    return settings
