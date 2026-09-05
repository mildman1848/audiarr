"""Optional translation service abstraction.

Audiarr is English-first and ships curated UI translations for supported
locales. This module is for optional dynamic text translation, such as
metadata summaries or future helper text. It deliberately keeps the core
app offline-capable: when no backend is configured, translation is a
no-op that returns the original text.
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx
from pydantic import BaseModel

log = logging.getLogger("audiarr.i18n.translation")

TranslationBackend = Literal["none", "libretranslate"]


class TranslationSettings(BaseModel):
    """Optional community/open-source translation backend settings."""

    backend: TranslationBackend = "none"
    base_url: str = ""
    api_key: str = ""
    default_source_language: str = "en"
    default_target_language: str = "de"


class TranslationResult(BaseModel):
    """Normalized translation response."""

    translated_text: str
    source_language: str
    target_language: str
    backend_used: TranslationBackend
    translated: bool


class TranslationService:
    """Translate dynamic text through an optional community backend.

    Supported MVP backend:
    - ``none``: returns input unchanged.
    - ``libretranslate``: calls a LibreTranslate-compatible ``/translate``
      endpoint. This can be a self-hosted instance or a community endpoint.
    """

    def __init__(
        self,
        settings: TranslationSettings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or TranslationSettings()
        self._client = client

    async def translate(
        self,
        text: str,
        target_language: str | None = None,
        source_language: str | None = None,
    ) -> TranslationResult:
        """Translate text, or return it unchanged when disabled."""
        source = source_language or self.settings.default_source_language
        target = target_language or self.settings.default_target_language

        if not text or self.settings.backend == "none":
            return TranslationResult(
                translated_text=text,
                source_language=source,
                target_language=target,
                backend_used="none",
                translated=False,
            )

        if self.settings.backend == "libretranslate":
            return await self._translate_libretranslate(text, source, target)

        log.warning("Unsupported translation backend configured: %s", self.settings.backend)
        return TranslationResult(
            translated_text=text,
            source_language=source,
            target_language=target,
            backend_used="none",
            translated=False,
        )

    async def _translate_libretranslate(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> TranslationResult:
        if not self.settings.base_url:
            log.warning("LibreTranslate backend selected but base_url is empty")
            return TranslationResult(
                translated_text=text,
                source_language=source_language,
                target_language=target_language,
                backend_used="libretranslate",
                translated=False,
            )

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            base_url=self.settings.base_url.rstrip("/"),
            timeout=15.0,
        )
        payload = {
            "q": text,
            "source": source_language,
            "target": target_language,
            "format": "text",
        }
        if self.settings.api_key:
            payload["api_key"] = self.settings.api_key

        try:
            response = await client.post("/translate", json=payload)
            response.raise_for_status()
            data = response.json()
            translated_text = data.get("translatedText") or text
            return TranslationResult(
                translated_text=translated_text,
                source_language=source_language,
                target_language=target_language,
                backend_used="libretranslate",
                translated=translated_text != text,
            )
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("LibreTranslate request failed: %s", exc)
            return TranslationResult(
                translated_text=text,
                source_language=source_language,
                target_language=target_language,
                backend_used="libretranslate",
                translated=False,
            )
        finally:
            if owns_client:
                await client.aclose()
