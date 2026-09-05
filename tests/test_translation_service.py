from __future__ import annotations

import httpx
import pytest

from app.i18n.translation_service import TranslationService, TranslationSettings


@pytest.mark.asyncio
async def test_translation_none_returns_original_text():
    service = TranslationService()

    result = await service.translate("Hello", target_language="de")

    assert result.translated_text == "Hello"
    assert result.backend_used == "none"
    assert result.translated is False


@pytest.mark.asyncio
async def test_libretranslate_backend_uses_mocked_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/translate"
        assert b"Hello" in request.content
        return httpx.Response(200, json={"translatedText": "Hallo"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://translate.local",
    )
    service = TranslationService(
        TranslationSettings(backend="libretranslate", base_url="http://translate.local"),
        client=client,
    )

    result = await service.translate("Hello", target_language="de")
    await client.aclose()

    assert result.translated_text == "Hallo"
    assert result.backend_used == "libretranslate"
    assert result.translated is True


@pytest.mark.asyncio
async def test_libretranslate_without_base_url_falls_back_to_original():
    service = TranslationService(TranslationSettings(backend="libretranslate"))

    result = await service.translate("Hello", target_language="de")

    assert result.translated_text == "Hello"
    assert result.backend_used == "libretranslate"
    assert result.translated is False
