# Audiarr Architecture

Audiarr is split into four layers:

1. **FastAPI backend**: settings, system status, metadata search, and connection tests.
2. **Provider chain**: Audible-first abstraction, Audnexus fallback.
3. **Outbound integrations**: Audiobookshelf for playback/server scans, m4b-convertarr for conversion.
4. **Minimal web UI**: server-rendered dashboard with translation files.

## Conversion strategy

MVP uses external `m4b-convertarr` instead of embedding ffmpeg/auto-m4b. This keeps the Audiarr image small and separates CPU-heavy, failure-prone conversion work from library orchestration.

Future embedded conversion is possible, but should only be added after real-world samples prove which converter semantics are needed. Otherwise we build a furnace into the living room and call it architecture.

## Storage

Use one shared `/data` mount to preserve same-filesystem moves and hardlinks:

```text
/data/audiobooks
/data/usenet/complete/audiobooks
/data/usenet/complete/m4b-convertarr
```

Do not recursively chown `/data`; the Docker image only touches its own configured audiobook root.
