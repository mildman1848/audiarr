"""Connection test endpoints for outbound integrations."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.connections.audiobookshelf import AudiobookshelfClient
from app.connections.m4b_convertarr import M4BConvertarrClient

log = logging.getLogger("audiarr.api.connections")

router = APIRouter()


class ConnectionTestRequest(BaseModel):
    url: str
    api_key: str | None = None


class ConnectionTestResponse(BaseModel):
    ok: bool
    message: str


@router.post("/api/v1/connections/audiobookshelf/test", response_model=ConnectionTestResponse)
async def test_audiobookshelf(request: ConnectionTestRequest) -> ConnectionTestResponse:
    client = AudiobookshelfClient(base_url=request.url, api_key=request.api_key)
    ok = await client.health()
    log.info("Audiobookshelf connection test to %s -> %s", request.url, ok)
    return ConnectionTestResponse(
        ok=ok,
        message="Audiobookshelf reachable" if ok else "Could not reach Audiobookshelf",
    )


@router.post("/api/v1/connections/m4b-convertarr/test", response_model=ConnectionTestResponse)
async def test_m4b_convertarr(request: ConnectionTestRequest) -> ConnectionTestResponse:
    client = M4BConvertarrClient(base_url=request.url, api_key=request.api_key)
    ok = await client.health()
    log.info("m4b-convertarr connection test to %s -> %s", request.url, ok)
    return ConnectionTestResponse(
        ok=ok,
        message="m4b-convertarr reachable" if ok else "Could not reach m4b-convertarr",
    )
