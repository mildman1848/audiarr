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

### 9. Profiles, Quality, Tags, Connect (notifications) — Done

- Profiles: audiobook-specific semantics modeled and editable — an ordered, best-first list of quality tiers plus an upgrade cutoff (see `docs/design/quality-profiles.md`). Wired into release-search quality fit, conversion enqueue, per-book assignments, Wanted cutoff upgrades, and optional Releases filtering.
- Quality: real, editable quality definitions (container/codec/bitrate band/lossless/chapter expectations), not a copy of video quality definitions. Used by the same decision paths as Profiles (see `docs/design/quality-profiles.md`, "Wired behavior"). Phase 1 quality routing is complete.
- Tags: modeled as first-class labels with book/root-folder assignments, Settings CRUD UI, Library filtering, and Book Detail editing.
- Connect (outbound webhooks/notifications): implemented as configurable webhooks for grab/import/health/test events, with secret masking in the UI.
- Profiles, Quality, Tags, and Connect are active, editable sections.

### 10. Release hardening — Partial

- CI publishes versioned and `latest` tags to GHCR and Docker Hub.
- Local `make validate`, `make build`, `make smoke`, and Trivy HIGH/CRITICAL scan are documented.
- Dependabot open alerts are zero or explicitly triaged.
- Runtime image does not include unnecessary build tooling such as pip/setuptools/wheel.
- Automatic config backups and update checks are modeled in settings but not implemented — **Next**.

## Roadmap to 1.0.0

Phased plan; each phase lands as small verified slices (issue → branch → PR → CI → deploy).

### Phase 1 — Finish quality routing — Done

- Per-book quality profile assignment: `books.quality_profile` column, book detail UI selector, profile-aware conversion enqueue (empty assignment falls back to the first configured profile). **Done**
- Upgrade search: chase the profile cutoff for monitored books (Wanted page action using per-book profiles). **Done**
- Optional quality filter when grabbing releases (only releases that fit the profile). **Done**

### Phase 2 — Close the automation loop — Done

- Periodic root-folder import scans (scheduler). **Done**
- Auto-import after SABnzbd completes a download. **Done**
- Metadata refresh / wanted-search scheduler. **Done**

### Phase 3 — Tags and Connect — Done

- Tags data model and tagging UI for books and root folders. **Done**
- Connect editor: webhooks/notifications for grab/import/health events. **Done**

### Phase 4 — Media management — In progress

- Rename/organize imports per `file_name_pattern` — explicit opt-in only; preview/apply runs per book and never deletes originals. **Done**
- Hardlink/copy import strategies. **Next**
- Root folder free-space and permissions view. **Next**

### Phase 5 — Hardening for 1.0

- Automatic config DB + settings backups with rotation. **Next**
- Update check (display only, no auto-update). **Next**
- Backup/restore documented and tested once for real. **Next**
- Dependency hygiene and a final security pass (issue #8). **Partial**

### Release

- `1.0.0` ships when Phases 1–5 are green: the automation loop is complete, quality decisions are per-book, and backups exist.

## Later Ideas

- Liberatarr integration for Audible library/import flows.
- Hardcover integration for book tracking and metadata enrichment.
- OPDS or ABS-compatible export.
