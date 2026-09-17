"""Settings schema for Audiarr.

The sections below mirror the baseline categories found in mature Servarr
apps (Radarr/Sonarr): host, auth, media management, quality profiles, root
folders, download clients, indexers/connect, metadata, UI/localization,
logging, and updates/backup. Audiarr's MVP does not implement every
behaviour those apps have (see docs/parity-targets.md) — this schema exists
so the settings API and UI have a stable, realistic shape to grow into.

Settings are persisted as a single JSON document. Secrets (API keys) are
never stored in this document directly when a ``*_key_file`` (FILE__)
source is used at runtime; the JSON only stores what the user typed in the
UI, which is fine for local single-user home-lab use documented in
SECURITY.md.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Supported Audible marketplaces. The project default is now the US
# marketplace, with German still fully supported as a first-class locale.
AudibleLocale = Literal["us", "uk", "de", "fr", "ca", "au", "it", "es", "jp", "in"]


class HostSettings(BaseModel):
    """Network binding, mirrors Radarr/Sonarr's "Host" settings tab."""

    bind_address: str = "0.0.0.0"
    port: int = 8787
    url_base: str = ""  # e.g. "/audiarr" for reverse-proxy sub-path hosting
    enable_ssl: bool = False
    launch_browser: bool = False


class AuthSettings(BaseModel):
    """Authentication mode, mirrors Radarr/Sonarr's "Security" tab.

    ``password`` is a write-only field: PUT /api/v1/settings accepts a
    plaintext password, hashes it into ``password_hash``, and clears it
    before persisting; ``exclude=True`` means it never round-trips and is
    never present in any serialized settings document (it only ever holds
    a value for the instant of a single PUT request).

    ``password_hash`` intentionally does NOT use ``Field(exclude=True)``:
    that would also strip it from ``model_dump_json()`` on every disk
    write (``app/config.py::save_settings``), silently losing the stored
    hash on every settings save and breaking login. Instead it is a plain,
    persisted field, and the settings API route excludes it from
    responses via FastAPI's ``response_model_exclude`` (see
    routes_settings.py) so GET/PUT responses still never leak it.
    """

    method: Literal["none", "forms"] = "none"
    username: str = ""
    api_key: str = ""
    password: str = Field(default="", exclude=True)
    password_hash: str = ""


class MediaManagementSettings(BaseModel):
    """Library file-handling behaviour."""

    rename_files: bool = False
    file_name_pattern: str = "{author}/{series}/{title} ({year})"
    delete_empty_folders: bool = True


ChapterExpectation = Literal["required", "preferred", "not_required"]


class QualityDefinition(BaseModel):
    """A single audiobook quality tier.

    Audiobook quality isn't resolution/source like video: what matters is
    the container/codec, the bitrate band (spoken word compresses far
    better than music, so these bands are much lower than a video/music
    quality ladder would suggest), whether the encoding is lossless, and
    whether chapter markers are present (they drive in-app navigation and
    have no video-quality equivalent). See docs/design/quality-profiles.md.
    ``id`` is a stable key that QualityProfile.quality_ids and
    cutoff_quality_id reference.
    """

    id: str
    name: str
    container: str = "m4b"
    codec: str = "aac"
    lossless: bool = False
    min_bitrate_kbps: int = 32
    preferred_bitrate_kbps: int = 64
    max_bitrate_kbps: int = 256
    chapters: ChapterExpectation = "preferred"


def _default_quality_definitions() -> list[QualityDefinition]:
    """Audiobook-shaped starter tiers -- not a copy of a video quality pack."""
    return [
        QualityDefinition(
            id="m4b-aac-64",
            name="M4B AAC ~64 kbps",
            container="m4b",
            codec="aac",
            min_bitrate_kbps=32,
            preferred_bitrate_kbps=64,
            max_bitrate_kbps=96,
            chapters="preferred",
        ),
        QualityDefinition(
            id="m4b-aac-128",
            name="M4B AAC ~128 kbps",
            container="m4b",
            codec="aac",
            min_bitrate_kbps=96,
            preferred_bitrate_kbps=128,
            max_bitrate_kbps=160,
            chapters="required",
        ),
        QualityDefinition(
            id="mp3-320",
            name="MP3 320 kbps",
            container="mp3",
            codec="mp3",
            min_bitrate_kbps=192,
            preferred_bitrate_kbps=320,
            max_bitrate_kbps=320,
            chapters="not_required",
        ),
        QualityDefinition(
            id="flac-lossless",
            name="FLAC Lossless",
            container="flac",
            codec="flac",
            lossless=True,
            min_bitrate_kbps=400,
            preferred_bitrate_kbps=1000,
            max_bitrate_kbps=1500,
            chapters="preferred",
        ),
    ]


class QualityProfile(BaseModel):
    """A named, ordered preference list of acceptable audiobook quality tiers.

    ``allowed_formats``/``cutoff_format`` are the original MVP fields (bare
    file extensions) and are kept as-is for backwards compatibility with
    existing settings.json documents. ``quality_ids`` is the richer
    audiobook-specific addition: an ordered, best-first list of
    QualityDefinition ids; ``cutoff_quality_id`` is the tier at which
    Audiarr stops seeking upgrades (mirrors ``cutoff_format`` but keyed to a
    real quality tier instead of a bare extension). See
    docs/design/quality-profiles.md for what's wired up vs. deferred.
    """

    name: str
    allowed_formats: list[str] = Field(default_factory=lambda: ["m4b", "mp3", "flac"])
    cutoff_format: str = "m4b"
    quality_ids: list[str] = Field(
        default_factory=lambda: ["m4b-aac-128", "m4b-aac-64", "mp3-320"]
    )
    cutoff_quality_id: str = "m4b-aac-128"
    upgrade_allowed: bool = True


class RootFolder(BaseModel):
    """A library root path under the /data mount."""

    path: str


class DownloadClient(BaseModel):
    """Download-client entry, mirrors Radarr/Sonarr's "Download Clients" tab.

    The MVP implements SABnzbd (``type="sabnzbd"``): configure ``url`` (a
    base URL such as ``http://sab:8080``) or the legacy ``host``/``port``
    pair, plus ``api_key`` and the ``category`` new downloads are filed
    under. ``type="generic"`` remains a no-op placeholder for other
    clients. The connection test lives at
    ``/api/v1/connections/sabnzbd/test``.
    """

    name: str
    type: str = "generic"
    # Preferred: a full base URL. host/port are still accepted for
    # backwards compatibility and older settings.json files.
    url: str = ""
    host: str = ""
    port: int = 0
    api_key: str = ""
    category: str = "audiobooks"
    enabled: bool = False

    def base_url(self) -> str:
        """Resolve a base URL from ``url`` or the ``host``/``port`` pair."""
        if self.url:
            return self.url.rstrip("/")
        if not self.host:
            return ""
        host = self.host.rstrip("/")
        if "://" not in host:
            host = f"http://{host}"
        if self.port:
            host = f"{host}:{self.port}"
        return host


class Indexer(BaseModel):
    """Indexer entry, mirrors Radarr/Sonarr's "Indexers" tab.

    The MVP implements Prowlarr (``type="prowlarr"``) as an indexer
    manager: configure ``url`` and ``api_key``. The connection test lives
    at ``/api/v1/connections/prowlarr/test``.
    """

    name: str
    type: str = "generic"
    url: str = ""
    api_key: str = ""
    enabled: bool = False


class ConnectNotification(BaseModel):
    """Stub outbound notification, mirrors Radarr/Sonarr's "Connect" tab."""

    name: str
    type: str = "webhook"
    url: str = ""
    enabled: bool = False


class MetadataSettings(BaseModel):
    """Metadata provider chain configuration.

    ``provider_order`` is tried in sequence by the metadata search
    endpoint; the first provider that returns results wins. Default order
    is Audible first, Audnexus as a fallback (see docs/providers.md).
    """

    provider_order: list[str] = Field(default_factory=lambda: ["audible", "audnexus"])
    audible_locale: AudibleLocale = "us"
    audnexus_base_url: str = "https://api.audnex.us"
    # When true, a startup task best-effort backfills asin + release_date
    # for legacy books that have both NULL (see app/metadata/backfill.py).
    # Defaults to false: prod imports predate this pipeline and backfilling
    # is a network-dependent, opt-in operation, not something that should
    # run unannounced on every boot.
    backfill_on_start: bool = False


class ConversionSettings(BaseModel):
    """MP3 -> M4B conversion backend settings (roadmap #6).

    ``backend`` selects the mode:
    - ``m4b-convertarr``: talk to a running m4b-convertarr container
      (HTTP POST /api/convert with optional bearer/api key).
    - ``command``: run a local command template per job (e.g. ffmpeg
      wrapper); ``{{source}}`` and ``{{output}}`` are substituted.

    ``delete_originals`` defaults to False — originals are NEVER deleted
    unless the user explicitly opts in (safe default; the conversion
    backend owns file movements).
    """

    backend: Literal["disabled", "m4b-convertarr", "command"] = "disabled"
    base_url: str = "http://localhost:8080"
    api_key: str = ""
    command_template: str = ""
    delete_originals: bool = False
    # Shared secret the m4b-convertarr POST_CONVERT hook sends as X-Api-Key
    # when calling back Audiarr. Empty = webhook accepts unauthenticated
    # requests (e.g. isolated docker networks).
    webhook_api_key: str = ""
    # A running job whose backend never called back gets failed after this
    # many hours (webhook lost / converter crashed).
    job_timeout_hours: int = 6


class UiSettings(BaseModel):
    """UI/localization settings."""

    language: Literal["en", "de"] = "en"
    theme: Literal["dark", "light"] = "dark"
    date_format: str = "YYYY-MM-DD"


class TranslationSettings(BaseModel):
    """Optional community/open-source translation backend settings.

    The default is offline-safe and sends no text anywhere. Users can opt
    into a LibreTranslate-compatible backend, preferably self-hosted.
    """

    backend: Literal["none", "libretranslate"] = "none"
    base_url: str = ""
    api_key: str = ""
    default_source_language: str = "en"
    default_target_language: str = "de"


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    retention_days: int = 14


class UpdateSettings(BaseModel):
    branch: str = "main"
    automatic: bool = False


class BackupSettings(BaseModel):
    folder: str = "/config/backups"
    interval_hours: int = 24
    retention_copies: int = 7


class AudiobookshelfConnection(BaseModel):
    """Connection to an existing Audiobookshelf server for playback/scan.

    ``api_key`` set here is only used when no FILE__/env secret is
    configured; see app/connections/audiobookshelf.py for resolution order.
    """

    url: str = ""
    api_key: str = ""
    # The Audiobookshelf library to rescan after imports/conversions.
    # Find its id in Audiobookshelf under Settings -> Libraries.
    library_id: str = ""
    enabled: bool = False


class M4BConvertarrConnection(BaseModel):
    """Connection to an m4b-convertarr instance for audio conversion jobs.

    Audiarr intentionally delegates conversion to the external
    m4b-convertarr service rather than embedding a converter (see
    docs/design/architecture.md for the rationale).
    """

    url: str = ""
    api_key: str = ""
    webhook_path: str = "/api/v1/convert"
    enabled: bool = False


class ConnectionsSettings(BaseModel):
    audiobookshelf: AudiobookshelfConnection = Field(default_factory=AudiobookshelfConnection)
    m4b_convertarr: M4BConvertarrConnection = Field(default_factory=M4BConvertarrConnection)


class Settings(BaseModel):
    """Top-level Audiarr settings document."""

    host: HostSettings = Field(default_factory=HostSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    media_management: MediaManagementSettings = Field(default_factory=MediaManagementSettings)
    quality_definitions: list[QualityDefinition] = Field(
        default_factory=_default_quality_definitions
    )
    quality_profiles: list[QualityProfile] = Field(
        default_factory=lambda: [QualityProfile(name="Standard")]
    )
    root_folders: list[RootFolder] = Field(
        default_factory=lambda: [RootFolder(path="/data/audiobooks")]
    )
    download_clients: list[DownloadClient] = Field(default_factory=list)
    indexers: list[Indexer] = Field(default_factory=list)
    connect: list[ConnectNotification] = Field(default_factory=list)
    metadata: MetadataSettings = Field(default_factory=MetadataSettings)
    conversion: ConversionSettings = Field(default_factory=ConversionSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    updates: UpdateSettings = Field(default_factory=UpdateSettings)
    backup: BackupSettings = Field(default_factory=BackupSettings)
    connections: ConnectionsSettings = Field(default_factory=ConnectionsSettings)
