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

    MVP note: only "none" is functionally enforced today. "forms" is
    modeled so the UI/settings API shape is stable, but login is a TODO
    (see docs/parity-targets.md).
    """

    method: Literal["none", "forms"] = "none"
    username: str = ""
    api_key: str = ""


class MediaManagementSettings(BaseModel):
    """Library file-handling behaviour."""

    rename_files: bool = False
    file_name_pattern: str = "{author}/{series}/{title} ({year})"
    delete_empty_folders: bool = True


class QualityProfile(BaseModel):
    """A named ordered list of acceptable audio formats."""

    name: str
    allowed_formats: list[str] = Field(default_factory=lambda: ["m4b", "mp3", "flac"])
    cutoff_format: str = "m4b"


class RootFolder(BaseModel):
    """A library root path under the /data mount."""

    path: str


class DownloadClient(BaseModel):
    """Stub download-client entry (e.g. SABnzbd/qBittorrent in the future)."""

    name: str
    type: str = "generic"
    host: str = ""
    port: int = 0
    enabled: bool = False


class Indexer(BaseModel):
    """Stub indexer entry, mirrors Radarr/Sonarr's "Indexers" tab."""

    name: str
    url: str = ""
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
    ui: UiSettings = Field(default_factory=UiSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    updates: UpdateSettings = Field(default_factory=UpdateSettings)
    backup: BackupSettings = Field(default_factory=BackupSettings)
    connections: ConnectionsSettings = Field(default_factory=ConnectionsSettings)
