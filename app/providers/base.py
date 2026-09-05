"""app.providers.base — provider contract and shared data types.

This module defines the stable contract every metadata provider must
implement, plus the normalized dataclasses the rest of Audiarr consumes.
Provider registration lives in ``app.providers.registry``.

All provider methods are async so stub providers and real network
providers satisfy the same contract without adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BookQuickInfo:
    """Smallest stable search-hit summary we need from a provider."""

    provider_uid: str
    provider_name: str
    title: str
    subtitle: str = ""
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    series: str = ""
    series_position: int = 0
    cover_url: str | None = None
    asin: str | None = None
    isbn: str | None = None
    locale: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrowseResponse:
    """
    Contract any provider's browse/search endpoint must return.

    We normalize to a flat list of ``BookQuickInfo`` plus optional
    pagination metadata so the rest of Audiarr does not have to know
    whether the source is Audible, Audnexus, or something else.
    """

    results: list[BookQuickInfo] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 0
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BookDetailInfo:
    provider_uid: str
    provider_name: str
    provider_external_id: str
    title: str
    subtitle: str = ""
    description: str = ""
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    series: str = ""
    series_position: int = 0
    genres: list[str] = field(default_factory=list)
    language: str = ""
    duration_seconds: int = 0
    release_date: str = ""
    cover_url: str | None = None
    asin: str | None = None
    isbn: str | None = None
    publishers: list[str] = field(default_factory=list)
    content_type: str = ""
    sample_url: str | None = None
    audible_locale: str = ""
    provider_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResponse:
    """
    Contract for provider search APIs that accept query strings.

    This is intentionally similar to :class:`BrowseResponse` because
    many real audiobook sources treat search and browse as the same
    endpoint shape.
    """

    results: list[BookQuickInfo] = field(default_factory=list)
    query_used: str = ""
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class BaseMetadataProvider:
    """
    Stable contract every metadata provider must fulfill.

    Implementations must be stateless at the class level and safe to
    instantiate multiple times. Every public method is async so that
    both stub and real network providers can coexist without adapters.
    """

    provider_name: str = "unknown"
    provider_url: str = ""
    provider_description: str = ""
    provider_version: str = "0.1.0"
    requires_api_key: bool = False

    def __init__(self, *, api_key: str = "", region: str = "us", **kwargs: Any) -> None:
        self.api_key = api_key
        self.region = region
        self.kwargs = kwargs

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) or not self.requires_api_key

    async def healthcheck(self) -> bool:
        """Return True when this provider thinks it is reachable/usable."""
        return True

    async def browse(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        sort: str = "newest",
        filter: str = "",
    ) -> BrowseResponse:
        return BrowseResponse(results=[], page=page, page_size=page_size)

    async def search(self, query: str, **kwargs: Any) -> SearchResponse:
        return SearchResponse(results=[])

    async def get_detail(self, external_id: str, **kwargs: Any) -> BookDetailInfo | None:
        return None

    def _normalize_authors(self, raw: Any) -> list[str]:
        return self._normalize_named_entries(raw)

    def _normalize_narrators(self, raw: Any) -> list[str]:
        return self._normalize_named_entries(raw)

    def _normalize_named_entries(self, value: Any) -> list[str]:
        """Normalize ``[{"name": "X"}, ...]`` and ``["X", ...]`` to names."""
        if value is None:
            return []
        if isinstance(value, str):
            return [self._normalize_string(value)]
        try:
            names = []
            for item in value:
                if item is None:
                    continue
                if isinstance(item, dict):
                    name = item.get("name") or item.get("title")
                    if name is not None:
                        names.append(self._normalize_string(name))
                else:
                    names.append(self._normalize_string(item))
            return [n for n in names if n]
        except TypeError:
            return []

    def _normalize_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [self._normalize_string(value)]
        try:
            return [self._normalize_string(item) for item in value if item is not None]  # type: ignore[arg-type]
        except TypeError:
            return []

    def _normalize_string(self, value: Any) -> str:
        return str(value).strip() if value is not None else ""

    def _normalize_optional(self, value: Any) -> str | None:
        normalized = self._normalize_string(value)
        return normalized or None
