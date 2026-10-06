# Changelog

## 1.1.11

- Author and series follows (#80): a new Follows page (and follow actions on Book Detail) lets you follow an author or a series by name. Follows and their candidates are persisted (schema v16, `016_follows.sql`) and reviewed as candidates instead of being imported blindly.
- Refresh is **manual only**. There is no scheduler; each follow has a Refresh action that asks the metadata provider chain for that author's/series' books. Nothing is searched, grabbed or downloaded -- Wanted stays in charge of acquisition.
- Candidates are classified as: future (release date after today), back-catalog, owned, missing or excluded. Future-dated results may auto-create monitored books (default quality profile/root folder). Back-catalog results are only listed; a book is created only when you explicitly select and add the candidate. Owned status is derived at read time from provider ids/ASIN, excluded candidates are never touched by a refresh, and if a book created from a candidate is later deleted the candidate returns to review.
- Refresh reports honest status instead of a silent empty success: `ok`, `partial` (a provider failed but another answered, or results were truncated) or `failed` (provider error and no usable result, HTTP 502). Provider errors and truncation are shown in the result.
- Limits: a refresh fetches a single page of at most 50 results; truncation is reported as `partial` status and does not page further. Audnexus supplies no author or series search, so it contributes nothing to follows. Only the first provider that returns results is used. Names are matched exactly and case-insensitively. Future titles appear only if a provider actually lists them.
- New EN/DE i18n strings for the Follows page and Book Detail follow actions (dictionaries at exact key parity).

## 1.1.10

- Reading-list imports (#79): Settings → Import Lists can now preview and import books from a public Goodreads shelf RSS feed, a Goodreads library CSV export, or a StoryGraph CSV export, reusing the existing provider matching and import-list groundwork rather than a parallel discovery engine.
- Goodreads feeds: only public shelf RSS URLs (`/review/list_rss/<user id>?shelf=<name>`) on `goodreads.com` are accepted and fetched from the fixed host `www.goodreads.com`; saved sources store only the numeric user id and shelf name. Private feed URLs carrying a `key=` token are rejected, and the key is never stored or logged. User ids must be ASCII digits; other Unicode numerals (e.g. Arabic-Indic digits) are rejected.
- StoryGraph is supported as **user-exported CSV upload only**. There is no StoryGraph login, no session-cookie handling and no scraping.
- Preview is read-only. Importing requires an explicit per-entry selection (maximum 50) of a verifiable provider candidate; books are created monitored from the provider's own detail record, already-known books are skipped, and nothing is downloaded or searched automatically. A reading list is a partial observation, so a book missing from a later feed/CSV is never deleted or unmonitored.
- Inputs are bounded (2 MiB feed, 5 MiB CSV, 2000 parsed rows, up to 20 saved sources); the feed parser rejects DTD/entity declarations.
- New `reading_list_err_*` i18n strings (including `feed_url_private_key` and `csv_format_unrecognized`) and UI strings added in both English and German.

## 1.1.8

- System UI parity (#73): the System page now has Starr-style Status / Tasks / Events / Logs tabs, deep-linkable per tab.
- Tasks: a new `GET /api/v1/system/tasks` endpoint reports the actual process-local runtime state and UTC next-due time of the five existing schedulers. Runtime timestamps are in-process only and reset on restart. Only Backup has a manual task-row action; the import, SABnzbd and wanted-search schedulers are listed read-only and are not manually triggered from this page.
- Events: the tab is the existing read-only import-job audit list, not a universal event bus.
- Logs: a new `GET /api/v1/system/logs` endpoint and Logs tab read a bounded in-memory ring buffer (1000 records; the API caps responses at 500). Messages, exceptions, JWTs, private-key blocks, credentials, headers and logger names are redacted before they are stored in the buffer. It is not a persistent/file log and is cleared on process restart.
- Settings → UI: theme and date-format controls are now functional -- they persist and change the rendered UI instead of being read-only placeholders. The optional color-impaired mode mentioned in the issue was not added.
- New i18n keys for the System tabs and UI settings added in both English and German (dictionaries at exact key parity).

## 1.1.7

- Import lists / metadata profiles groundwork (#72): a new `app/import_lists.py` module gives Liberatarr (#64) -- and any future pull-style source such as a later #66 Hardcover addition -- one shared, normalized status/sync shape (`ImportListSource`, `ImportListSyncResult`) instead of each source inventing its own plumbing. The existing `/api/v1/liberatarr/*` routes and their behavior are unchanged; they now share the row-processing loop internally with the new generic routes.
- New generic `GET /api/v1/import-lists` (status per configured source) and `POST /api/v1/import-lists/{id}/sync` API, plus a Settings → Import Lists page: a dense Starr-style status table (name, enabled, status, last sync, last result) with a per-source Sync action. The Connections page's Liberatarr card also now shows a persisted status/last-sync summary instead of only the last in-page test/sync message.
- `LiberatarrSettings` gains `sync_status`/`last_sync_at`/`last_sync_error`/`last_sync_created`/`last_sync_skipped` fields, written after every sync from either entry point (settings-only, no DB migration -- same pattern as `ConnectNotification`'s `last_event`/`last_status`/`last_error`).
- Metadata profiles (#72 groundwork): a new `MetadataProfile` settings model (name, allowed languages, a content-warning-term placeholder, enabled/default flags) replaces the Settings → Metadata "coming next" placeholder with a real, editable profile list (Standard profile seeded by default). Deliberately minimal and not yet enforced anywhere -- not a parental-control/content-rating engine.
- New i18n keys (`import_lists_*`, `settings_metadata_profiles_*`, `settings_section_import_lists`, `connections_liberatarr_status_*`) added in both English and German.

## 1.1.6

- Listenarr/Chaptarr audio metadata harvest (#71): read-only `ffprobe` enrichment around the existing external m4b-convertarr/command conversion backend (no embedded converter, originals never modified) — duration, bitrate, codec, container, and chapter data for every imported file.
- `app/library/audio_probe.py`: a new async `probe_file()` wrapper runs `ffprobe` as a subprocess and normalizes its JSON into duration/bitrate/codec/container/chapters; never raises — a missing binary, unreadable file, or malformed output all resolve to a non-fatal `probe_status` (`ok`/`error`/`unavailable`) that the importer persists instead of blocking the import.
- Schema v15 (`015_file_audio_metadata.sql`): `library_files` gains nullable `duration_seconds`/`bitrate_kbps`/`codec`/`container`/`chapter_count`/`probe_status`/`probe_error` columns, plus a new `library_file_chapters` table (one row per chapter, cascade-deleted with its file).
- The import pipeline (`_persist_files`) probes every placed file before inserting its `library_files` row — read-only, and never fails an import when ffprobe is missing or a file can't be probed.
- Book Detail's file table now groups files by edition and shows format/abridgement/locale badges, a file count, and the book's narrators per edition (edition/multi-file/narrator clarity); per-file Duration/Bitrate/Codec columns; and a per-file expandable chapter table where chapter data exists. Probe failures/pending state show as a visible badge instead of blank cells.
- Naming/organize tokens gain `{codec}` and `{bitrate}` (opportunistic verification against Chaptarr's token set, #71) — empty string for a file that hasn't been probed yet, same as any other optional token.
- The production Docker image now installs `ffmpeg` (provides `ffprobe`) alongside the existing runtime dependencies.

## 1.1.5

- Custom release preferences / audiobook custom formats MVP (#70): a new persisted `release_preferences` settings section (preferred terms with scores, blocked terms, a `minimum_preference_score` threshold) adapts Radarr/Sonarr's Custom Formats, Release Profiles, and Delay Profiles into one small, audiobook-first slice — no condition DSL, no time-based delay page. Ships with audiobook-specific defaults (`unabridged` preferred, `abridged`/`dramatized` blocked).
- `app/release_preferences.py`: a new pure scoring module (independent of the existing `app/quality.py` container/codec/bitrate fit) matches release titles against preferred/blocked terms and reports a score, accepted/rejected status, and human-readable reasons.
- Release search (`/search`) now shows a "Preference" badge with score and a reasons tooltip next to the existing Quality badge, so a release visibly explains why it scored well or poorly; existing quality-fit behavior and fields are unchanged.
- The unattended Wanted upgrade search (manual button and periodic scheduler) now drops any release whose preference score is rejected (blocked term match, or below the minimum score) before grabbing — release preferences are enforced there, not just displayed. The interactive Releases page stays informational only; a human can still grab a flagged release.
- New Settings > Release Preferences page: editable preferred/blocked term rows plus the minimum score field, following the same GET → merge → PUT pattern as every other settings section.

## 1.1.4

- Remote path mappings (#69): configurable Settings > Download Clients editor to translate a download client's completed-download path to the path Audiarr sees (Docker/NAS setups with different mounts), resolved via deterministic longest-remote-path-prefix matching and applied before the SABnzbd auto-import pipeline touches the filesystem.
- Activity queue/history actions (#69): queue "Remove" and history "Retry"/"Remove" are now real, backed by SABnzbd's documented queue/history/retry API actions (never deletes on-disk data); queue retry/mark-failed remain unimplemented since SABnzbd has no supported API action for either.

## 1.1.3

- Add New modal gains Starr-style options at add time, including tags, alongside root-folder and quality-profile selection (#68).
- Book Detail toolbar adds refresh, rescan, search, organize, and delete actions (#68). Rescan triggers a full Audiobookshelf library scan rather than a per-book scan.
- Library table gains clickable sortable Monitored and Quality Profile columns (#68).

## 1.1.2

- OPDS 1.2/Atom catalog export (#65): root/new/authors/narrators/search feeds and acquisition links.
- Auth-protected OPDS download route for primary audiobook file, preferring M4B and enforcing root-folder safety.
- Settings OPDS read-only feed URL page with EN/DE i18n.

## 1.1.0

- Add Liberatarr (Libation) as an optional read-only Wanted source (#64): configure base URL/token in Connections, test reachability, proxy the library, and idempotently create monitored books from Not Liberated Audible purchases. The client matches Liberatarr's real API shape (`{"books": [...]}`, `product_id` ASINs, integer status enum, `language` locale) and keeps defensive fallbacks.
- Refresh README for the 1.0/post-release state with Streamyfin-style sections, clearer install/development/security/roadmap guidance, and public-safe wording around optional Liberatarr sync.

## 1.0.0

Audiarr 1.0.0 ships as the first complete release: Phases 1-6 of the roadmap are done.

- Complete automation loop (Phase 2): periodic root-folder import scans, auto-import after SABnzbd completes a download, and a metadata refresh / wanted-search scheduler run without manual intervention.
- Per-book quality decisions (Phase 1): per-book quality profile assignment, upgrade search that chases the profile cutoff for monitored books, and an optional quality filter when grabbing releases.
- Tags and Connect (Phase 3): first-class tags with book/root-folder assignments and Library filtering, plus a Connect webhook editor for grab/import/health/test events.
- Media management (Phase 4): explicit preview/apply file organization, per-root-folder import strategies (hardlink default with automatic copy fallback, zero extra disk usage), and root-folder health (free space, writability, existence) surfaced as API fields, library badges, and `health_issue` webhooks.
- Hardening (Phase 5): automatic config/database backups with retention rotation, a documented and tested backup/restore drill, a display-only update check against the GitHub releases API, and a real Trivy HIGH/CRITICAL security gate in CI.
- Starr UI/UX parity (Phase 6): shell/navigation, primary data pages, and Settings brought in line with Radarr/Sonarr/Lidarr interaction patterns, finished with a release polish pass.

## Unreleased

- Final Starr-style release polish pass (#52): tighten Activity and Import empty/config states with shared Starr-style empty-state components and actionable hints; refresh README wording so shipped Profiles/Quality/Connect/Tags and Starr-family UI state are described accurately; mark Phase 6 complete in the roadmap; bump project version to `0.6.4`.
- Settings Starr parity pass (#51): Security moves to its own settings section; General now focuses on host/maintenance; Settings gains more consistent Starr-style subsection grouping and hints for Download Clients, Indexers, Metadata, Conversion, UI, and Connect; UI settings are now editable (theme + date format) and apply immediately; connection/test buttons disable during requests and report inline + toast results; FastAPI validation errors render as useful field messages instead of bare HTTP codes; persisted secrets remain masked/blank in forms and server-rendered HTML. Bumps project version to `0.6.3`.
- Primary data pages Starr parity pass (#50): Library table gains clickable sortable headers with URL state; Add New now uses a Starr-style add wizard with root-folder and quality-profile selection validated at create time (no create-then-patch partial books); books can store/clear a root-folder preference used by organize preview/apply; Book Detail adds refresh/search actions and author/narrator monograms; Wanted gains reason filter and sort controls; Import Problems gains retry-from-guess; Activity Queue shows disabled pause/remove controls with an explicit read-only SABnzbd explanation. Adds schema migration 014 (`books.root_folder_id`) and bumps project version to `0.6.2`.
- Shell and navigation Starr parity pass (#49): the System page becomes a
  Starr-style Status/Tasks/Events tab strip (ARIA tablist, keyboard
  navigation, deep-linkable via `?tab=`); Tasks lists the five schedulers
  read-only from existing settings/backup data; Events shows the import
  jobs audit trail with a refresh button; the Dashboard gains a compact
  health banner (root folder missing/read-only) fed by a new read-only
  `health` aggregation on `/api/v1/system/status` that reuses the
  folder-health probe; Library is visually primary in the sidebar while
  the Dashboard stays the landing page as a documented intentional
  deviation. EN/DE i18n throughout. Bump project version to `0.6.1`.
- Make the Trivy HIGH/CRITICAL config scan a real gate (`exit-code: '1'` in
  `.github/workflows/security.yml` and `make security`) instead of
  report-only. Add `.trivyignore` triaging the one known finding, DS-0002
  ("Specify at least 1 USER command"): the LSIO/s6-overlay base image
  requires root for `/init` and s6-rc service setup, while the long-running
  Audiarr API drops to `abc` via `s6-setuidgid` in
  `root/usr/local/bin/start-audiarr-api`, verified by `make smoke`. Document
  the triage in `docs/release-hardening.md`.
- Close release hardening and dependency hygiene for Phase 5 item 4 (#8):
  add `docs/release-hardening.md` documenting the GHCR/Docker Hub tag
  publishing model (`<version>`, `sha-<short>`, `latest`), the local
  `make validate`/`make build`/`make smoke` and Trivy HIGH/CRITICAL
  workflow, and the Dependabot zero-open-alerts hygiene rule; records
  zero open Dependabot alerts observed at time of closure. Linked from
  the README publishing section. Bump project version to `0.5.4`.
- Document and close the backup/restore drill for Phase 5 item 3 (#34): a
  new `docs/backup-restore-runbook.md` covering backup contents, restore
  prerequisites/procedure (Docker Compose and local/dev), rollback, and a
  verification checklist; linked from the README; records a real restore
  drill (2026-09-26) performed against a disposable instance, result PASS.
  Update the Settings backup hint copy (EN/DE) now that restore is
  documented and tested instead of "planned for a later release". Bump
  project version to `0.5.3`.
- Add a display-only update check for Phase 5 item 2 (#33): a single GET against the GitHub releases API (disableable via a new Settings -> General "Update check" toggle, no other telemetry), current/latest version and a Starr-style "update available" callout with a link to the release notes on the System/Status page, and a manual "Check for updates" button plus `POST /api/v1/system/update-check`; Audiarr never auto-updates. EN/DE i18n. Bump project version to `0.5.2`.
- Add automatic configuration backups for Phase 5 item 1 (#32): safe SQLite online snapshot + `settings.json` ZIP archives with manifest SHA256s, manual backup endpoint/button, backup listing, retention rotation, delayed-first scheduled backups, and EN/DE Settings UI; bump project version to `0.5.1`.
- Make hardlink the default import strategy with copy as the automatic fallback (zero extra disk usage, sources stay seedable; migration 013 flips existing default-`copy` root folders to `hardlink` since no explicit user choice existed before); UI texts updated to reflect the fallback behavior; bump project version to `0.4.4`.
- Add root-folder health view: free space, writability, and existence surfaced as API fields and library-page badges, with `health_issue` webhook dispatch for missing or read-only root folders (#31); bump project version to `0.4.3` for Phase 4 item 3.
- Add per-root-folder import strategies (`copy`, `hardlink`, `move`) with free-space checks before copy/move, hardlink fallback to copy on cross-device or unsupported filesystems, and reports the chosen placement strategy in the in-memory import result; a real import now places matched media files according to the root folder's strategy while dry-run stays strictly read-only; bump project version to `0.4.2` for Phase 4 item 2 (#30).
- Add explicit preview/apply file organization for a book's imported files, with pattern rendering, conflict checks, and best-effort rollback; bump project version to `0.4.1` for Phase 4 item 1.
- Productize public wording: Audiarr is described as a pre-1.0 audiobook manager, not a scaffold.
- Align web navigation labels around Arr-style Add New and Releases workflows.
- Mark planned Settings sections explicitly instead of showing misleading save controls.
- Adopt Audiarr-native roadmap versioning (`0.<phase>.<completed item within that phase>`) and stop using the old household/fork `mldm<N>` suffix.

## 0.1.1

- Update FastAPI, Starlette, Pydantic, Uvicorn, and Jinja2 pins for security.
- Remove pip, setuptools, wheel, and system Python build helpers from the runtime image after dependency installation.
- Keep the image LSIO/s6-compatible while reducing runtime scanner noise and attack surface.

## 0.1.0

- Initial Audiarr application foundation.
- FastAPI backend, settings API, metadata provider chain, Audiobookshelf and m4b-convertarr clients.
- Minimal Servarr-inspired UI with Audible-orange accent.
- LSIO/s6 Docker image foundation.
- GitHub Actions for lint/test, Docker build/publish, security scan, and mirror placeholders.
