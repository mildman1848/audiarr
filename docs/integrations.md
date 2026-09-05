# Integrations

## Audiobookshelf

Audiarr will manage/import metadata and then ask Audiobookshelf to rescan. Current client implements health and scan-library placeholders using bearer-token auth.

## m4b-convertarr

Audiarr delegates MP3/M4B conversion to `m4b-convertarr` via HTTP. This avoids embedding ffmpeg and heavy NLP/converter dependencies in the core app.

## Liberatarr and Hardcover

Future options:

- Liberatarr: reuse Audible library/account knowledge where legally and technically safe.
- Hardcover: optional catalog/list sync, not part of MVP.
