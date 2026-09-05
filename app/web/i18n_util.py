"""Tiny i18n loader.

Not a full i18n framework — just enough structure so the UI is ready for
German/English translation now instead of having strings hardcoded in
templates. Add more locales by dropping a new JSON file next to en.json/
de.json and adding it to SUPPORTED_LANGUAGES.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

I18N_DIR = Path(__file__).parent / "i18n"
SUPPORTED_LANGUAGES = ("en", "de")
def get_default_ui_language() -> str:
    """Return the default UI language for new installations.

    The project is English-first with German as a supported first-class
    alternative. Providers/metadata can keep their own locale defaults
    independently of the UI language.
    """
    return "en"


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def load_strings(language: str) -> dict[str, str]:
    if language not in SUPPORTED_LANGUAGES:
        language = get_default_ui_language()
    path = I18N_DIR / f"{language}.json"
    return json.loads(path.read_text(encoding="utf-8"))
