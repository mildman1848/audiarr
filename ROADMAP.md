# Audiarr Roadmap

Audiarr is an early Servarr-style audiobook manager scaffold. The project is English-first, keeps German as a first-class locale, and targets LinuxServer.io-style Docker deployment.

## Principles

- Keep the core API-first and automation-friendly.
- Keep provider integrations replaceable; no hard lock-in to one metadata source.
- Treat Audible marketplace access conservatively and document assumptions.
- Keep `/config` persistent and `/data` media-oriented, following LSIO conventions.
- Prefer small verified milestones over a large untested rewrite.
- Do not build a beautiful UI over an unverified import pipeline. That is how humans summon support tickets.

## MVP Milestones

### 1. Metadata provider chain

Goal: implement a real provider abstraction with Audible as the primary provider and Audnexus as fallback.

Acceptance criteria:

- Provider interface supports search by title, author, ASIN, and ISBN when available.
- Audible marketplace setting defaults to `us` and supports `de`.
- Audnexus fallback is used when Audible does not return a usable match.
- Provider responses are normalized into Audiarr book/author/narrator/series models.
- Tests cover US and German locale behavior using fixtures, not live network calls only.

### 2. Library domain model and persistence

Goal: move from scaffold data structures to a usable audiobook library model.

Acceptance criteria:

- Database tables/models exist for authors, books, editions, narrators, series, files, imports, and provider IDs.
- API endpoints can create/list/update/delete root folders and library entries.
- Imported metadata keeps source/provider attribution.
- Migration path is explicit and tested.

### 3. Import pipeline and matching

Goal: import existing audiobook folders and match them to metadata.

Acceptance criteria:

- Scanner can inspect `/data/audiobooks` and extract basic file/folder metadata.
- Matching supports exact ASIN/ISBN hints and fuzzy title/author matching.
- Dry-run mode shows proposed actions before writes.
- Import results are persisted with clear status and debug logs.

### 4. Audiobookshelf connection

Goal: connect Audiarr to Audiobookshelf for library refresh and future sync operations.

Acceptance criteria:

- Settings support Audiobookshelf URL and API key via safe config/secret handling.
- Connection test validates reachability without logging secrets.
- Trigger library scan/refresh endpoint is implemented.
- Failure modes are surfaced in health checks.

### 5. M4B conversion integration

Goal: integrate the existing m4b-convertarr workflow as an optional conversion backend.

Acceptance criteria:

- Settings support m4b-convertarr URL/API key or command mode, depending on selected backend.
- Import pipeline can enqueue MP3-to-M4B conversion jobs.
- Conversion job state is visible via API and UI.
- Originals are never deleted unless an explicit safe setting is enabled.
- Tests cover job creation and failed conversion handling.

### 6. Servarr-style UI baseline

Goal: keep the UI familiar for Servarr users while using Audible-inspired accent colors.

Acceptance criteria:

- Navigation and settings layout follow Servarr-like grouping.
- Audible-inspired accent color is used without copying protected branding assets.
- English UI is complete for MVP paths; German translation is present and extendable.
- No private household names or homelab internals appear in public UI/docs.

### 7. Release hardening

Goal: keep published images reproducible and low-noise.

Acceptance criteria:

- CI publishes versioned and `latest` tags to GHCR and Docker Hub.
- Local `make validate`, `make build`, `make smoke`, and Trivy HIGH/CRITICAL scan are documented.
- Dependabot open alerts are zero or explicitly triaged.
- Runtime image does not include unnecessary build tooling such as pip/setuptools/wheel.

## Later Ideas

- Liberatarr integration for Audible library/import flows.
- Hardcover integration for book tracking and metadata enrichment.
- OPDS or ABS-compatible export.
- Queue/history dashboard inspired by Servarr activity pages.
- Quality profiles for bitrate/container/chapter handling.
