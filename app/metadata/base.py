"""Metadata provider interface and shared result model.

See docs/design/architecture.md and docs/providers.md for the rationale
behind the provider-chain design (Audible primary, Audnexus fallback).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

log = logging.getLogger("audiarr.metadata")


class BookMetadata(BaseModel):
    """A single search result, normalized across providers."""

    source: str
    source_id: str
    title: str
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list)
    narrators: list[str] = Field(default_factory=list)
    series: str | None = None
    series_index: float | None = None
    asin: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    release_date: str | None = None
    duration_minutes: int | None = None
    description: str | None = None
    cover_url: str | None = None
    language: str | None = None


class MetadataProvider(ABC):
    """Base class every metadata provider must implement.

    Providers should never raise on "no results" — return an empty list
    instead. Raise only for genuine failures (network error, bad
    response) so the provider chain can log and fall through to the next
    provider.
    """

    name: str = "base"

    @abstractmethod
    async def search(self, query: str, locale: str) -> list[BookMetadata]:
        """Search for audiobooks matching ``query`` in the given locale.

        ``locale`` is an Audible marketplace code (e.g. "de", "us") even
        for non-Audible providers, so the chain can pass one value through
        uniformly; providers that don't use locales may ignore it.
        """
        raise NotImplementedError
