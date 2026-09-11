"""i18n key parity between the supported UI languages.

en.json is treated as the source of truth; de.json must define exactly the
same key set (and vice versa), and no value may be empty. This guards
against a slice adding a new key to only one language file.
"""

from __future__ import annotations

import json
from pathlib import Path

I18N_DIR = Path(__file__).resolve().parent.parent / "app" / "web" / "i18n"


def _load(language: str) -> dict[str, str]:
    return json.loads((I18N_DIR / f"{language}.json").read_text(encoding="utf-8"))


def test_en_and_de_have_the_same_keys():
    en_keys = set(_load("en").keys())
    de_keys = set(_load("de").keys())

    missing_in_de = en_keys - de_keys
    missing_in_en = de_keys - en_keys

    assert not missing_in_de, f"Keys present in en.json but missing in de.json: {sorted(missing_in_de)}"
    assert not missing_in_en, f"Keys present in de.json but missing in en.json: {sorted(missing_in_en)}"


def test_no_empty_translation_values():
    for language in ("en", "de"):
        strings = _load(language)
        empty = [key for key, value in strings.items() if not str(value).strip()]
        assert not empty, f"Empty i18n values in {language}.json: {empty}"
