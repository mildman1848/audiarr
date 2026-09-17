# Audiarr Roadmap

Audiarr is a pre-1.0 Servarr-style audiobook manager. The project is
English-first, keeps German as a first-class locale, and targets
LinuxServer.io-style Docker deployment.

## Principles

- Keep the core API-first and automation-friendly.
- Keep provider integrations replaceable; no hard lock-in to one metadata source.
- Treat Audible marketplace access conservatively and document assumptions.
- Keep `/config` persistent and `/data` media-oriented, following LSIO conventions.
- Prefer small verified milestones over a large untested rewrite.
- Do not build a beautiful UI over an unverified import pipeline. That is how humans summon support tickets.

## Milestones

Status legend: **Done** — implemented and tested; **Partial** — implemented
but with real gaps; **Next** — not started.

### 1. Metadata provider chain — Done

- Provider interface supports search by title, author, ASIN, and ISBN when available.
- Audible marketplace setting defaults to `us` and supports `de` and other locales, via Audible's public catalog API (no account/device registration).
- Audnexus fallback is used when Audible does not return a usable match.
- Provider responses are normalized into Audiarr book/author/narrator/series models.
- Tests cover US and German locale behavior using fixtures, not live network calls only.

### 2. Library domain model and persistence — Done

- Database tables/models exist for authors, books, editions, narrators, series, files, imports, and provider IDs.
- API endpoints can create/list/update/delete root folders and library entries.
- Imported metadata keeps source/provider attribution.
- Migration path is explicit and tested (`app/db_migrations`).

### 3. Import pipeline and matching — Done

- Scanner inspects configured root folders and extracts basic file/folder metadata.
- Matching supports exact ASIN/ISBN hints and fuzzy title/author matching.
- Dry-run mode shows proposed actions before writes; a real import run is separate and explicit.
- Unmatched folders get a manual match/ignore review page (`/import`).
- Import results are persisted with clear status and debug logs.

### 4. Audiobookshelf connection — Done

- Settings support Audiobookshelf URL and API key via safe config/secret handling.
- Connection test validates reachability without logging secrets.
- Trigger library scan/refresh endpoint is implemented.
- Failure modes are surfaced in health checks.

### 5. M4B conversion integration — Done

- Settings support an m4b-convertarr backend (URL/API key) or a disabled/command mode.
- Import pipeline can enqueue MP3-to-M4B conversion jobs; job state is visible on the dashboard.
- Originals are never deleted unless the explicit `delete_originals` setting is enabled.
- Tests cover job creation and failed conversion handling.

### 6. Release search and download clients — Done

- Prowlarr indexer settings, connection test, and release search (the `/search` "Releases" page).
- SABnzbd download client settings, connection test, and grab-to-queue.
- Activity page shows the live SABnzbd queue and history.

### 7. Authentication — Done

- Forms login (session cookie) and API-key (`X-Api-Key`) auth, enforced by ASGI middleware, not just modeled settings.
- Login rate limiting and PBKDF2 password hashing.

### 8. Servarr-style UI baseline — Done

- Navigation and settings layout follow Servarr-like grouping, with dedicated `/settings/<section>` pages.
- Audible-inspired accent color is used without copying protected branding assets.
- English UI is complete; German translation is present and kept in parity (`tests/test_i18n_parity.py`).
- No private household names or homelab internals appear in public UI/docs.

### 9. Profiles, Quality, Tags, Connect (notifications) — Partial

- Profiles: audiobook-specific semantics modeled and editable — an ordered, best-first list of quality tiers plus an upgrade cutoff (see `docs/design/quality-profiles.md`). Not yet used by import matching or conversion job dispatch.
- Quality: real, editable quality definitions (container/codec/bitrate band/lossless/chapter expectations), not a copy of video quality definitions. Not yet used by import matching or conversion job dispatch.
- Tags: not modeled beyond the settings document shape; placeholder page.
- Connect (outbound webhooks/notifications): not implemented; placeholder page that links to the working Audiobookshelf/m4b-convertarr connections instead.
- Tags and Connect are explicitly marked "Planned" in the Settings overview and their own pages; Profiles and Quality are now active, editable sections.

### 10. Release hardening — Partial

- CI publishes versioned and `latest` tags to GHCR and Docker Hub.
- Local `make validate`, `make build`, `make smoke`, and Trivy HIGH/CRITICAL scan are documented.
- Dependabot open alerts are zero or explicitly triaged.
- Runtime image does not include unnecessary build tooling such as pip/setuptools/wheel.
- Automatic config backups and update checks are modeled in settings but not implemented — **Next**.

## Next

- Wire quality profiles/definitions into import matching and conversion job dispatch (settings model + editor landed; decisioning is next, see `docs/design/quality-profiles.md`).
- Tags management once indexers/download clients/connections support multiple entries.
- Connect: a webhook/notification editor for grab/import/health events.
- Automatic config DB backups and update-check settings.
- UI polish pass: consistent empty states and density across Dashboard, Library, Calendar, Wanted, and Activity.

## Later Ideas

- Liberatarr integration for Audible library/import flows.
- Hardcover integration for book tracking and metadata enrichment.
- OPDS or ABS-compatible export.
