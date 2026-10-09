# Audiarr Roadmap

Audiarr is a Servarr-style audiobook manager that shipped `1.0.0`. The
project is English-first, keeps German as a first-class locale, and targets
LinuxServer.io-style Docker deployment.

## Principles

- Keep the core API-first and automation-friendly.
- Keep provider integrations replaceable; no hard lock-in to one metadata source.
- Treat Audible marketplace access conservatively and document assumptions.
- Keep `/config` persistent and `/data` media-oriented, following LSIO conventions.
- Stay intentionally close to the Starr UI/UX family (Radarr/Sonarr/Lidarr): familiar navigation, dense data pages, toolbars, settings forms, modals, save bars, and status pages; deviate only where the audiobook domain genuinely requires it.
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

### 10. Release hardening — Done

- CI publishes versioned and `latest` tags to GHCR and Docker Hub.
- Local `make validate`, `make build`, `make smoke`, and Trivy HIGH/CRITICAL scan are documented (`docs/release-hardening.md`).
- Dependabot open alerts are zero or explicitly triaged; zero open alerts observed at time of closure.
- Runtime image does not include unnecessary build tooling such as pip/setuptools/wheel.
- Automatic config backups are implemented with manual/scheduled ZIP snapshots and retention rotation; update checks are implemented (display-only).

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

### Phase 4 — Media management — Done

- Rename/organize imports per `file_name_pattern` — explicit opt-in only; preview/apply runs per book and never deletes originals. **Done**
- Hardlink/copy import strategies — per root folder (`copy`/`hardlink`/`move`), free-space checks, hardlink copy fallback, dry-run read-only. **Done**
- Root folder free-space and permissions view — probe-backed API fields and library-page badges; `health_issue` webhook on missing/read-only folders. **Done**

### Phase 5 — Hardening for 1.0 — Done

- Automatic config DB + settings backups with rotation. **Done**
- Update check (display only, no auto-update). **Done**
- Backup/restore documented and tested once for real (#34). **Done**
- Dependency hygiene and a final security pass (issue #8). **Done**

### Phase 6 — Starr UI/UX parity for 1.0

Gap matrix and priorities: [`docs/design/starr-ui-parity.md`](docs/design/starr-ui-parity.md) (audit #48).

Audiarr should feel like a deliberate member of the Starr UI family, not a forked movie app with labels changed. Keep Radarr/Sonarr/Lidarr interaction patterns wherever they make sense, while preserving audiobook-specific entities and workflow semantics.

- Starr UI parity audit and gap matrix (#48). **Done**
- Shell and navigation Starr parity pass (#49). **Done**
- Primary data pages Starr parity pass (#50). **Done**
- Settings Starr parity pass (#51). **Done**
- Final Starr-style release polish pass (#52). **Done**

### Release — Shipped

- `1.0.0` shipped: Phases 1–6 are green. The automation loop is complete, quality decisions are per-book, backups and restore are verified, and the UI/UX is intentionally close to the Starr family.
- `1.1.0` shipped: Liberatarr integration as a Wanted source (#67), reusing Audible library/account knowledge where legally and technically safe.
- `1.1.2` shipped: OPDS 1.2/Atom catalog export and an auth-protected download route (#65).

## 1.1.x Feature-Parity Convergence

Gap analysis and full rationale: [`docs/design/feature-parity-gap-analysis.md`](docs/design/feature-parity-gap-analysis.md).

Two questions drove this track: have we adopted (or deliberately adapted)
everything worth adopting from Radarr/Sonarr, and what do sibling
audiobook-manager projects (Listenarr, Chaptarr) have that we should
selectively harvest? The answer is a sequence of small `1.1.x` slices —
deliberately **not** a `1.2.0` jump — so each lands as its own verified
milestone rather than a large untested rewrite.

- **1.1.1 — Parity audit + roadmap (docs-only).** This roadmap update and the gap-analysis doc. **Done**
- **1.1.2 — OPDS export feed (#65).** Kept next because it's a scoped, already-planned integration rather than a structural UI change; revisit optional calendar/reader-consumer notes (e.g. iCal/webcal link) if cheap to add alongside it. **Done**
- **1.1.3 — Add New + Book Detail Starr action parity (#68).** Root folder/quality-profile/monitored/tags chosen at add time; Book Detail toolbar gets refresh/rescan/search/organize/delete as first-class actions; library table gets sortable column headers (including monitored/quality profile). Highest-value P0 gap identified in both `starr-ui-parity.md` and the new gap analysis. **Done** — scoped MVP; author/series-level cascade monitoring (vs. the existing per-book monitor toggle) is deferred, no schema for it exists yet and it would be a genuine scope increase beyond this slice.
- **1.1.4 — Activity queue/history actions + remote path mappings (#69).** Queue remove/retry/mark-failed and history retry/remove actions; remote path mappings for Docker/NAS setups where the download client and Audiarr see different filesystem paths. **Done** — queue remove and history retry/remove are real (backed by SABnzbd's documented queue/history/retry API); queue retry and mark-failed remain unimplemented, since SABnzbd has no supported API action for either against a still-queued job.
- **1.1.5 — Custom release preferences / audiobook custom formats MVP (#70).** Scoring/conditions for narrator/edition/publisher/dramatized-vs-unabridged preferences, plus an optional "wait for a better release" rule (Radarr/Sonarr's delay/release-profile concepts, adapted). **Done** — term-based preferred/blocked scoring (`app/release_preferences.py`) plus a `minimum_preference_score` threshold as the wait rule; narrator/publisher/language are a `category` label on the same term model (real per-release metadata matching is deferred, see `docs/design/release-preferences.md`). Wired into release search display and enforced (not just displayed) in the unattended Wanted upgrade search.
- **1.1.6 — Listenarr/Chaptarr audio metadata harvest (#71).** ffprobe-based chapter/duration/bitrate metadata as read-only enrichment around the existing external conversion backend (no embedded converter); edition/multi-file clarity in the file table; opportunistic naming-token verification. **Done** — `app/library/audio_probe.py` wraps `ffprobe` (never raises, non-fatal `probe_status`), wired into the import pipeline's `_persist_files`; schema v15 adds nullable audio-metadata columns plus a `library_file_chapters` table; Book Detail's file table groups by edition (format/abridgement/locale/narrator/file-count) with per-file duration/bitrate/codec columns and an expandable chapter table; `{codec}`/`{bitrate}` added as naming tokens.
- **1.1.7 — Import lists / metadata profiles groundwork (#72).** Generalize the import-list concept with Liberatarr as the first concrete source, so a future #66 Hardcover addition (or #79/#80 reading-list/follow sources) can plug into the same abstraction instead of inventing a parallel sync/status system; stand up metadata profiles (language/content-warning restrictions), replacing the "coming next" placeholder. Deliberately groundwork only -- no Hardcover integration and no enforcement of metadata-profile restrictions yet. **Done** — `app/import_lists.py` adds normalized `ImportListSource`/`ImportListSyncResult` types, a `list_sources()`/`sync_source()` entry point, and a Liberatarr adapter that wraps the existing read-only client (`app/connections/liberatarr.py`) without touching its behavior; the legacy `/api/v1/liberatarr/*` routes are unchanged and now share the same row-processing loop internally. New generic `GET /api/v1/import-lists` and `POST /api/v1/import-lists/{id}/sync` routes plus a Settings → Import Lists status page (Starr-style table: enabled/status/last sync/last result/sync action); the Connections page's Liberatarr card also now shows persisted status/last-sync. `MetadataProfile` (name, allowed languages, content-warning-term placeholder, enabled/default flags) is a new settings-backed list (no DB migration -- follows the existing `ConnectNotification`/`ReleasePreferenceTerm` settings-list pattern) with a real editable list replacing the Settings → Metadata placeholder.
- **1.1.8 — System tabs/tasks/events/logs and UI settings parity (#73).** Tasks list, Events log, in-UI log viewer, and functional theme/date-format UI settings. **Done** — System page has Status/Tasks/Events/Logs tabs. Tasks reports process-local runtime state and UTC next-due time for the five existing schedulers (timestamps reset on restart; only Backup has a manual row action). Events is the existing read-only import-job audit list, not a universal event bus. Logs reads a bounded (1000-record) in-memory ring buffer with redaction applied before storage (API caps at 500 records; not a persistent log, cleared on restart). Theme and date-format settings are functional. Color-impaired mode (an optional "if cheap" item in the issue) is deferred and was not added.
- **1.1.9 — Dewarr feature harvest roadmap pass ([#78](https://github.com/mildman1848/audiarr/issues/78)).** Docs-only comparison against Dewarr's reading lists, follows, discovery, sources/clients, notifications, and access model. **Done** — see [`docs/design/dewarr-feature-harvest.md`](docs/design/dewarr-feature-harvest.md) for adopt/adapt/defer/skip decisions pinned to an upstream snapshot. Existing follow-up issues [#79](https://github.com/mildman1848/audiarr/issues/79)–[#83](https://github.com/mildman1848/audiarr/issues/83) remain in sequence; StoryGraph session-cookie access and automatic acquisition are not assumed, and [#66](https://github.com/mildman1848/audiarr/issues/66) Hardcover enrichment remains distinct.
- **1.1.10 — Reading-list imports from Goodreads and StoryGraph (#79).** Add optional import-list sources for Goodreads shelves/CSV and StoryGraph lists/tags, reusing the existing metadata matching/import-list groundwork rather than a parallel discovery engine. **Done** — Goodreads public shelf RSS (fixed host `www.goodreads.com`, saved as non-secret user-id + shelf sources; private `key=` URLs rejected) and user-exported Goodreads CSV, plus **StoryGraph user-exported CSV upload only — no login, no session cookies, no scraping**. Entries are previewed read-only and matched through the existing `ProviderChain`; only an explicit, per-entry selection (max 50) creates monitored books, re-resolved from the provider's own detail record and idempotent against known provider ids. Reading lists are partial observations: absence never deletes or unmonitors a local book, and nothing is downloaded or auto-searched (Wanted stays in charge). Bounded inputs (2 MiB feed, 5 MiB CSV, 2000 parsed rows, 20 saved sources); Settings → Import Lists gains source management and a preview/selection UI with EN/DE strings. Goodreads feed shelves and ids are restricted to ASCII.
- **1.1.11 — Author and series follows (#80).** Monitor future books and reviewed back-catalog gaps for selected authors/series, with explicit preview/exclusion handling before monitored books are created. **Done** — persisted author/series follows (schema v16) with a Follows page and Book Detail follow actions. Refresh is manual only (no scheduler); future-dated results may auto-create monitored books, while back-catalog candidates require explicit user selection, and owned/missing/excluded states are shown. Provider errors and truncation are reported (`partial`/`failed`); no searches, grabs or downloads. Limits: one page of up to 50 results, only the first provider with results is used, Audnexus supplies no author/series search, exact case-insensitive name matching, and future titles appear only if a provider lists them.
- **1.1.12 — Native sources and torrent download clients (#81).** Evaluate and, if clean, add audiobook-native sources plus qBittorrent/torrent-client support while preserving the existing Prowlarr + SABnzbd path, remote path mappings, and import strategy semantics. **Done** — manual Prowlarr → qBittorrent torrent grabs, with a required category and tag on the client; completed downloads import through the shared importer with remote-path mapping. Constraints: the mapped path must resolve beneath a configured root folder, single-file torrents are terminally skipped, and missing/not-yet-visible paths and transient errors are retried on each poll without a cap/backoff. Download-client secrets are write-only at the settings API, and connection tests reuse stored keys only for matching configured URLs. Direct native sources (MyAnonaMouse, AudiobookBay-style) are deliberately deferred; Prowlarr stays the only source. No automatic torrent grabbing and no torrent deletion or seeding control.
- **1.1.13 — Rich notifications and discovery digests (#82).** Extend Connect-style events toward discovery/wanted/import failure/stalled-download notifications and optional digesting, without leaking credentials or private list data. **Next**
- **1.1.14 — Shared-library users and access model decision (#83).** Decide whether Audiarr remains a household-admin tool or grows users/roles/per-library access (with OIDC/Plex only after the access model is clear). **Next**

## Later Ideas

- Native multi-indexer UI beyond Prowlarr-first — only if Prowlarr proves insufficient in practice after the native-source/client investigation.
- Root folder relocation / library move workflow — plausible fit, high blast-radius (filesystem mutation); needs a concrete user need and explicit backup-first/approval gating before scheduling.
