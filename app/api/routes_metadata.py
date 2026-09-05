"""Metadata search endpoint backed by the provider chain."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import load_settings
from app.providers.chain import ProviderChain, ProviderChainConfig

log = logging.getLogger("audiarr.api.metadata")

router = APIRouter()


def build_provider_chain() -> ProviderChain:
    """Build a ProviderChain from persisted settings.

    Exposed as a FastAPI dependency (``Depends(build_provider_chain)``)
    so tests can override it with ``app.dependency_overrides``.
    """
    import app.providers  # noqa: F401 — ensure built-ins are registered

    settings = load_settings()
    config = ProviderChainConfig(
        provider_order=settings.metadata.provider_order,
        audible_locale=settings.metadata.audible_locale,
        audnexus_base_url=settings.metadata.audnexus_base_url,
    )
    return ProviderChain(config=config)


ChainDep = Annotated[ProviderChain, Depends(build_provider_chain)]


class MetadataSearchRow(BaseModel):
    """Normalized search result row returned by the API."""

    provider_name: str
    provider_uid: str
    title: str
    subtitle: str = ""
    authors: list[str] = []
    narrators: list[str] = []
    series: str = ""
    series_position: int = 0
    cover_url: str | None = None
    asin: str | None = None
    isbn: str | None = None
    locale: str | None = None


class MetadataSearchResult(BaseModel):
    results: list[MetadataSearchRow]
    query: str
    provider_used: str | None = None
    total_results: int | None = None


def _row_from_book(book: Any) -> MetadataSearchRow:
    """Convert a provider BookQuickInfo dataclass into an API row."""
    return MetadataSearchRow(
        provider_name=book.provider_name,
        provider_uid=book.provider_uid,
        title=book.title,
        subtitle=book.subtitle,
        authors=list(book.authors),
        narrators=list(book.narrators),
        series=book.series,
        series_position=book.series_position,
        cover_url=book.cover_url,
        asin=book.asin,
        isbn=book.isbn,
        locale=book.locale,
    )


@router.get("/api/v1/metadata/search", response_model=MetadataSearchResult)
async def search_metadata(
    query: str,
    locale: str | None = None,
    limit: int = 20,
    chain: ChainDep = None,  # type: ignore[assignment]
) -> MetadataSearchResult:
    """Search audiobook metadata via the configured provider chain.

    ``locale`` overrides the persisted default for this request only.
    """
    if locale:
        chain.config.audible_locale = locale

    response = await chain.search(query, page_size=limit)
    provider_used = response.provider_metadata.get("provider_used")

    log.info(
        "Metadata search for %r (locale=%s) -> %d result(s) via %s",
        query,
        chain.config.audible_locale,
        len(response.results),
        provider_used,
    )
    return MetadataSearchResult(
        results=[_row_from_book(b) for b in response.results],
        query=response.query_used or query,
        provider_used=provider_used,
        total_results=response.provider_metadata.get("total_results"),
    )
