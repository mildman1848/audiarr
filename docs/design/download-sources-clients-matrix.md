# Download sources & clients — capability matrix (issue #81)

Date: 2026-10-07 · Base: `32b734b` · Issue: [#81](https://github.com/mildman1848/audiarr/issues/81)

> **Status: implemented in 1.1.12 (manual Prowlarr → qBittorrent).** This
> document records the capability matrix behind #81 and how it was resolved:
> qBittorrent is supported as a manual-grab torrent client; direct native
> sources are deferred. No live tracker, indexer, torrent client, credential,
> or account was contacted while writing it; external facts come from public
> documentation pages only.

## 1. Three roles that must stay separate

| Role | Question it answers | Today in Audiarr |
| --- | --- | --- |
| **Indexer / search source** | "Which releases exist for this query?" | Prowlarr (`app/connections/prowlarr.py`) |
| **Download client** | "Fetch this release and tell me when it is done." | SABnzbd (`app/connections/sabnzbd.py`) and, since 1.1.12, qBittorrent (`app/connections/qbittorrent.py`) |
| **Completed-download importer** | "A finished download exists at a path — scan, match, quality-check, import." | `app/sab_auto_import.py` → `app/library/importer.py` |

qBittorrent is a **download client**, not a source: it cannot search for
releases. Adding it does not add any search capability. This matches the
conclusion in [`dewarr-feature-harvest.md`](dewarr-feature-harvest.md)
("stay Prowlarr-first").

## 2. Verified facts

### 2.1 Audiarr repository (inspected at base `32b734b`, before implementation)

- `ProwlarrClient._normalize_release` already carries `protocol`,
  `download_url`, `magnet_url`, `seeders`, `leechers` (`app/connections/prowlarr.py:53-73`).
  Torrent results therefore already reach the UI; they cannot yet be grabbed.
- `ProwlarrClient.download_nzb` is NZB-only: it returns `None` for a `magnet:`
  body (`prowlarr.py:136-138`).
- `POST /api/v1/releases/grab` is hard-wired Prowlarr → SABnzbd
  (`app/api/routes_releases.py:208-253`, `_require_sabnzbd`). Quality-fit and
  release-preference scoring are already applied on the search side
  (`_quality_fit_fields`, `_preference_fields`).
- `SABnzbdClient` exposes `add_nzb`, `queue`, `history` (completed items carry
  a `storage` path), remove/retry actions and `version`.
- `app/sab_auto_import.py`: polls history, filters on status `completed` and
  category, dedupes via `sab_import_state` keyed by `nzo_id` (hash fallback),
  applies `resolve_remote_path()` to `storage` **before** any `is_dir()` /
  `scan_folder()` call, then uses `import_single_folder` (ASIN hint) or
  `run_import` of the containing root folder. Source files are never moved or
  deleted. Failures are recorded per item and never abort the tick.
- `app/remote_path_mapping.py::resolve_remote_path`: longest-prefix
  remote→local mapping, never raises, client-agnostic.
- `app/models/settings.py::DownloadClient`: `name`, `type` (`sabnzbd` or
  `generic` placeholder), `url`/`host`/`port`, `api_key`, `category`,
  `enabled`. There is **no** username/password field, which qBittorrent's
  cookie login needs (see 2.2).
- **Pre-existing secret exposure:** the model explicitly states that
  `DownloadClient.api_key` is **not** excluded from the raw settings API
  response, and `app/api/routes_settings.py` excludes only the auth password
  hash. The current UI masks the key visually, but that is not API-level
  secrecy: today `GET` settings returns download-client API keys in the clear.
  This is **not** a safe precedent for new secrets (see §4 item 10).
  **Resolved in 1.1.12:** `api_key` and `password` are now write-only at the
  settings API; the paragraph above describes the pre-implementation state.

### 2.2 qBittorrent (official WebUI API, 5.0)

Sources: [WebUI API (qBittorrent 5.0)](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29) and
[API-Key Authentication (≥ v5.2.0)](https://github.com/qbittorrent/qBittorrent/wiki/API-Key-Authentication-%28%E2%89%A5v5.2.0%29), retrieved 2026-10-07.

- **Auth, option A — API key (qBittorrent ≥ v5.2.0 / WebAPI v2.14.1):**
  stateless Bearer-token authentication using a configured 32-character API key. API
  keys cannot call the WebAPI auth endpoints. No session/cookie handling and
  no stored username+password are needed, so this is **preferred wherever the
  client version supports it**. Do not assume it exists on 5.0/5.1 or older.
- **Auth, option B — username/password SID (compatibility path):** `username`
  + `password` login returns a cookie containing `SID`; later requests must
  send it. The `Referer` or `Origin` header must match the request's `Host`.
  Only an explicit alternative for releases older than 5.2.0, and only if the
  implementation chooses to support them.
- All endpoints except login require authentication under either option —
  **no anonymous API access is assumed.**
- **Submit:** `POST /api/v2/torrents/add` accepts `urls` (http, https, magnet,
  `bc://bt/`) or `torrents` (file bytes), plus `savepath`, `category`, `tags`,
  `paused`.
- **Observe:** `GET /api/v2/torrents/info` returns per torrent `hash`,
  `category`, `state`, `progress` (0–1), `save_path`, `content_path`, `tags`.
  It accepts `filter=completed`, the documented completion query (combine with
  `category`/`tag`).
- **`content_path` caveat:** it is the root folder for multi-file torrents but
  an absolute **file** path for single-file torrents. Single-file results must
  be handled explicitly; they are not a folder.
- **Version:** `GET /api/v2/app/version` returns a version string (usable as
  the connection test).

### 2.3 Prowlarr / SABnzbd (public documentation)

- [Prowlarr overview](https://wiki.servarr.com/prowlarr): indexer manager/proxy
  that "supports management of both Torrent Trackers and Usenet Indexers".
- [Prowlarr Quick Start](https://wiki.servarr.com/en/prowlarr/quick-start-guide):
  indexers are added from a definition list; if a site is not listed, "Generic
  Newznab" (usenet) or "Generic Torznab" (torrents) can be used.
- [Prowlarr supported-indexers list](https://github.com/Servarr/Wiki/blob/master/prowlarr/supported-indexers.md)
  (page states Prowlarr build `2.6.5.5620`, nightly): **MyAnonaMouse is listed**
  under *Private Trackers* (torrent, "large ebook and audiobook tracker").
  **No AudioBookBay / AudiobookBay entry exists** in that list (searched
  case-insensitively, 0 matches). The list shows only that a definition
  exists; it says nothing about account requirements, stability, or terms of use.
- [SABnzbd API reference (4.5)](https://sabnzbd.org/wiki/configuration/4.5/api)
  documents `addfile`, `addurl`, `queue`, `history` modes, but the page itself
  warns it is for an older SABnzbd version. It is used here only to confirm the
  broad API shape, not current-version compatibility; the existing client's own
  behaviour is the source of truth.

## 3. Capability matrix

| Path | Capability / role | Documented interface & auth | Search / submission | Completed path & status | Audiarr reuse | Risks (security / maintenance / legal / access) | Decision |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Prowlarr + SABnzbd** (existing) | Source + usenet client | Prowlarr API key; SAB API key | Prowlarr search → NZB fetch → `addfile` | History `storage` + status `completed`, per-`nzo_id` dedupe | 100 % (it *is* the pipeline) | Already shipped; no change | **Keep as working default; do not alter behaviour** |
| **Prowlarr + qBittorrent** | Same source, new torrent client | API key (Bearer) on qB ≥ 5.2.0 / WebAPI v2.14.1 (recommended); cookie `SID` session from username/password (Referer/Origin must match) only as compatibility for older releases | Prowlarr result `magnet_url`/`download_url` → `torrents/add` | `torrents/info?filter=completed`: `hash`, `state`, `progress`, `save_path`/`content_path` (`content_path` is a file path for single-file torrents) | Prowlarr search, quality/preference scoring, `resolve_remote_path`, and the shared importer are reused; qB client adapter, write-only credentials, and per-client info-hash state are implemented | New stored secret (API key, or username+password on older qB) is excluded at the settings API boundary; session handling only on the legacy path; single-file `content_path` handling; completion keys off `filter=completed`, with `state` variants (completed/seeding/paused) tolerated; seeding obligations on private trackers are the user's concern, so never auto-remove | **Implemented in 1.1.12** as a manual-grab client (see §4) |
| **Direct native MyAnonaMouse** | Tracker as source | Not verified: no stable, documented, authorized API reviewed for this work. Prowlarr has a definition, which already covers search through the existing source | Would duplicate Prowlarr search | Would still need a client | None beyond what Prowlarr already offers | Private account, per-user credentials/tokens, site-rule and ToS exposure, brittle if it relied on page structure, ongoing maintenance | **Defer.** Revisit only with a documented authorized API and a maintenance owner |
| **Direct AudiobookBay-style** | Public-style site as source | Not verified: no official API reviewed; **not in Prowlarr's supported list** (build `2.6.5.5620`) | Would require site-specific access code | n/a | None | Scraping fragility, unclear authorization, legal/content-rights exposure, anti-bot measures (must not be bypassed) | **Defer / out of scope.** Do not implement or document access workarounds |

User-exported or local inputs (e.g. files the user drops in a folder) are a
separate option and not part of #81 unless already supported.

## 4. Architecture (as implemented in 1.1.12)

The points below were the design recommendations; the shipped implementation follows them, with the deviations noted in §6 and the setup constraints.

1. **Client adapter boundary.** A small interface (`add`, `list_completed`,
   `version`) with the SABnzbd client and a new qBittorrent client behind it.
   Keep `SABnzbdClient` and its routes untouched; the adapter wraps, not
   rewrites.
2. **Prowlarr stays the only source.** qBittorrent receives only the magnet /
   torrent URL of a release the user explicitly picked from Prowlarr results.
   No tracker search or tracker credentials inside Audiarr.
3. **Protocol routing on grab.** `protocol == usenet` → SABnzbd (unchanged);
   `torrent` → qBittorrent if one is enabled, otherwise a clear 503, exactly
   like today's "no SABnzbd configured". Today's torrent behaviour (SAB path
   rejects magnets) must not silently change.
4. **Bounded placement.** Submit with a fixed `category` (and an Audiarr
   `tags` marker) from the client entry; do not let request input choose
   `savepath`.
5. **Discover completion via client API.** Poll `torrents/info` with
   `filter=completed`, narrowed to the category/tag. Treat `state` names as
   secondary: `filter=completed` is authoritative, and tests tolerate the
   documented completed/seeding/paused variants (see §5).
6. **Resolve before touching disk.** Apply `resolve_remote_path()` to the
   reported path before any `is_dir()` / scan, as `sab_auto_import` does.
   Prefer `content_path`, but it is a root folder only for multi-file
   torrents; for single-file torrents it is an absolute file path. Define that
   case explicitly (importer currently expects a folder, e.g. import via the
   containing directory or skip with a recorded reason).
7. **Dedupe per client and identifier.** Key state by `(client, hash)` (qB
   `hash`), not by name; do not reuse SAB's `nzo_key` namespace in a way that
   collides. A migration (with a documented restore point) is expected.
8. **Same importer and quality policy.** Feed the existing
   `import_single_folder` / `run_import` flow. No second scoring engine.
9. **Non-destructive.** The first slice never deletes torrents or downloaded
   data and never pauses/stops seeding.
10. **Credentials.** Recommend the qBittorrent **API key** (≥ 5.2.0) as the
    primary credential; offer username/password (SID flow) only as an explicit
    compatibility option for older releases, with the version threshold shown
    in the UI/docs. Any #81 qB secret (API key, or legacy username/password)
    must be **excluded at the settings API response boundary** (not merely
    masked in the UI), never logged, and covered by tests. The current
    behaviour is **not** a precedent: `download_clients[*].api_key` is
    returned in the raw settings response today. Either bring the existing
    `api_key` under the same exclusion in this slice, or treat that exposure
    as a pre-existing defect that must be fixed before `api_key` is reused for
    qBittorrent. Preserve-on-save semantics (an omitted/blank secret keeps the
    stored value) must be designed alongside, so excluding secrets does not
    wipe them on the next settings write.

Automating Prowlarr search/grab for already-configured wanted workflows is not
new native-source behaviour: it remains Prowlarr-sourced and bound by the same
explicit settings. Nothing here enables new automatic downloading or changes a
default.

## 5. Unknowns

- Which qB versions operators actually run (the deployed version is unknown;
  it decides whether the legacy SID path is needed at all). `state` values are
  documented in the official WebUI API page (including the completed/seeding
  variants `uploading`, `pausedUP`, `queuedUP`, `stalledUP`, `checkingUP`);
  `filter=completed` is the authoritative completion query, so tests should
  tolerate the documented completed/seeding/paused variants rather than
  equating completion with a single `state` literal.
- Whether Prowlarr's `downloadUrl` for torrents returns `.torrent` bytes or a
  magnet redirect for the user's indexers (the existing code only handles the
  magnet case by rejecting it); the adapter must handle both or fail safely.
- Whether the supported-indexers list reflects what works for a given user
  account; it only shows that a definition exists.
- No access-control, terms-of-use, or legality assessment of any specific
  tracker is made here; users are responsible for lawful sources.

## 6. Acceptance & test checklist (mapped to #81)

| #81 criterion | Evidence required |
| --- | --- |
| Matrix documented before implementation | Done: this document predates the implementation |
| One new path implemented **or** explicitly deferred with rationale | qBittorrent: **implemented** per §4 (manual grabs). Native MAM / AudiobookBay: **deferred** per §3 |
| Completed downloads use the same import / remote-path / quality pipeline | **Done:** `tests/test_qbittorrent_auto_import.py` verifies remote-path mapping, shared importer use, root containment, retryable paths, and deduplication |
| Submission test | Fake qB transport: a Bearer authorization header is sent (API-key mode) or a login cookie is sent (legacy mode); `torrents/add` receives `urls`/category/tags, no caller-controlled `savepath` |
| Completion query test | Fake qB transport: `torrents/info` called with `filter=completed` plus category/tag |
| Path-mapping test | Longest-prefix mapping applied to qB `content_path`/`save_path` before filesystem access; single-file torrent (`content_path` is a file) handled explicitly |
| Safe-failure tests | Login 403, timeout, malformed JSON, missing path, unmapped/nonexistent path, no qB configured → recorded failure/skip or 503, never an unhandled exception or deletion |
| SABnzbd preserved | Existing SAB tests pass unmodified; usenet grab behaviour unchanged |
| Dedupe | Same hash on two ticks imports once |
| No secret leakage | Test that the qB API key (and legacy username/password) is **absent from the raw settings API response** (excluded, not just masked) and from logs; test covers the existing `download_clients[*].api_key` exposure (fixed in this slice, or explicitly tracked as a blocking pre-existing defect); secret survives a settings save that omits it |
| Real subprocess / tests per repo convention | Run the project's test and lint commands and record real output in the PR |

## 7. Sources

1. qBittorrent, [WebUI API (qBittorrent 5.0)](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29) — retrieved 2026-10-07.
2. qBittorrent, [API-Key Authentication (≥ v5.2.0)](https://github.com/qbittorrent/qBittorrent/wiki/API-Key-Authentication-%28%E2%89%A5v5.2.0%29) — retrieved 2026-10-07.
3. Servarr, [Prowlarr](https://wiki.servarr.com/prowlarr) — retrieved 2026-10-07.
4. Servarr, [Prowlarr Quick Start Guide](https://wiki.servarr.com/en/prowlarr/quick-start-guide) — retrieved 2026-10-07.
5. SABnzbd, [API reference 4.5](https://sabnzbd.org/wiki/configuration/4.5/api) — retrieved 2026-10-07; page carries an older-version warning.
6. Servarr, [Prowlarr supported indexers](https://github.com/Servarr/Wiki/blob/master/prowlarr/supported-indexers.md) — retrieved 2026-10-07 (build `2.6.5.5620`).
7. Internal: [`dewarr-feature-harvest.md`](dewarr-feature-harvest.md); source files cited in §2.1.

## Setup constraints: completed-download paths (issue #81)

The qBittorrent completed-download importer never widens filesystem access.
`content_path` (after `resolve_remote_path`) must resolve — `..` and symlinks
included — strictly beneath a configured root folder, otherwise nothing is
probed or imported. To import torrents, therefore:

- add the qBittorrent download directory (as Audiarr sees it) as a root
  folder, **or** configure a remote path mapping that translates qBittorrent's
  path into a directory beneath an existing root folder;
- use a torrent content layout that creates a subfolder (single-file torrents
  are skipped deliberately and are terminal);
- a completed item with no `content_path` yet is also retryable, since the
  client may not have populated the path in its first completion response;
- a path outside every root, or one that does not exist yet (volume not
  mounted), is recorded as a retryable state (`failed`, reason prefixed
  `retry:`) and re-checked on every poll tick until it resolves, so fixing the
  mapping/mount is enough; no manual re-trigger is needed. There is no retry
  cap or backoff. Transient processing errors are retried the same way.
  Imported torrents stay deduped.
- A manual grab requires both a non-blank category and tag on the qBittorrent
  client (completion import filters on both); `/releases/grab` answers 422
  otherwise. Torrent HTTP(S) URLs are accepted only when they are Prowlarr's
  canonical `<base>/<indexer id>/download` URL on the configured Prowlarr.
  Connection-test endpoints reuse stored API keys only for the matching
  configured client URL; blank settings fields preserve, but do not clear,
  stored secrets.
