# i18n and Optional Translation

Audiarr is **English-first**. German (`de`) is a supported first-class UI language, but the default UI language and default Audible marketplace are now US/English oriented.

## Static UI translations

Curated UI strings live in:

- `app/web/i18n/en.json`
- `app/web/i18n/de.json`

The loader in `app/web/i18n_util.py` intentionally stays small. UI strings should be reviewed by humans instead of blindly machine-translated.

## Optional community/open-source translation backend

Dynamic text translation is handled by `app/i18n/translation_service.py`.

Supported MVP backend:

| Backend | Status | Notes |
|---|---|---|
| `none` | default | Offline-safe no-op, returns original text |
| `libretranslate` | scaffolded | Works with self-hosted LibreTranslate-compatible `/translate` endpoints |

Future candidates:

- Argos Translate for local/offline translation.
- OpenNMT-based community instances.
- Other open APIs that can be wrapped without locking Audiarr to a proprietary provider.

## Why optional?

Audiarr metadata may include descriptions from Audible, Audnexus, Hardcover, or local files. Automatic translation can be useful, but it also introduces latency, privacy implications, and subtle metadata damage. Translation should therefore be opt-in and clearly visible to the user.

Implementation entrypoint: `app/i18n/translation_service.py`.

## Security and privacy

Never send private library paths, API keys, Audible account identifiers, or raw OAuth redirects to translation services. Translate only user-visible text fields such as descriptions or summaries.
