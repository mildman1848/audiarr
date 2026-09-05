"""Metadata search endpoint backed by the provider chain."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import load_settings
from app.metadata.base import BookMetadata
from app.metadata.chain import MetadataProviderChain, build_default_providers

log = logging.getLogger("audiarr.api.metadata")

router = APIRouter()


class MetadataSearchRequest(BaseModel):
    query: str
    locale: str | None = None  # falls back to settings.metadata.audible_locale


class MetadataSearchResponse(BaseModel):
    results: list[BookMetadata]
    provider_used: str | None


@router.post("/api/v1/metadata/search", response_model=MetadataSearchResponse)
async def search_metadata(request: MetadataSearchRequest) -> MetadataSearchResponse:
    settings = load_settings()
    locale = request.locale or settings.metadata.audible_locale

    chain = MetadataProviderChain(
        providers=build_default_providers(),
        order=settings.metadata.provider_order,
    )
    results, provider_used = await chain.search(request.query, locale)
    log.info(
        "Metadata search for %r (locale=%s) -> %d result(s) via %s",
        request.query,
        locale,
        len(results),
        provider_used,
    )
    return MetadataSearchResponse(results=results, provider_used=provider_used)
