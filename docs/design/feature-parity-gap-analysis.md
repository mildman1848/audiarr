# Feature Parity Gap Analysis (1.1.x)

## Purpose and scope

This document answers two questions raised for the `1.1.x` cycle:

1. Have we adopted (or deliberately adapted) the feature set of Radarr/Sonarr,
   not just their UI look-and-feel?
2. What do Listenarr and Chaptarr — two other audiobook-oriented Servarr-style
   projects — have that Audiarr should selectively adopt?

[`docs/design/starr-ui-parity.md`](starr-ui-parity.md) (issue #48) already did
a detailed CSS/layout/interaction audit for the `1.0.0` release. This document
is a **product feature** audit: it looks at feature families (Custom Formats,
Import Lists, Remote Path Mappings, queue actions, etc.), not pixel-level
parity, and it folds in cross-project research (Listenarr, Chaptarr) that the
UI-parity audit did not cover.

Scope is deliberately conservative: everything recommended here targets the
`1.1.x` patch/minor track (current version `1.1.0`, after the Liberatarr
integration, #67). Nothing here proposes a `1.2.0` jump.

## Radarr/Sonarr parity matrix

Feature families per the Servarr settings documentation
(wiki.servarr.com/radarr/settings, wiki.servarr.com/sonarr/settings), compared
against Audiarr's current implementation.

| Feature family | Audiarr status | Verdict | 1.1.x action |
|---|---|---|---|
| Media management (naming tokens, folders, import options, hardlink/copy/move, root folders) | Implemented: `file_name_pattern`, root-folder CRUD, hardlink/copy/move per root folder, free-space/permission checks, rename preview/apply. | Adapted, functionally close. | None required; polish only (see Add New / Book Detail rows below). |
| Extra files / file management permissions | Not modeled (no companion-file handling; audiobooks rarely ship sidecar subtitle/nfo files the way video does). | Out of scope. | Skip — low value for audiobook libraries. |
| Quality profiles | Implemented with audiobook-specific semantics (ordered quality tiers + cutoff). See `docs/design/quality-profiles.md`. | Adapted, done. | None. |
| Delay profiles | Missing (no per-profile "wait N hours for a better protocol" behavior). | Gap, minor value for a single-user/homelab usage pattern. | 1.1.5 — fold into "custom release preferences" as an optional wait rule, not a separate settings page. |
| Release profiles (must-contain/must-not-contain, preferred terms) | Missing. | Gap. | 1.1.5 — see Custom Formats row; audiobook equivalent (narrator/edition/publisher preferred terms) covers this need. |
| Quality definitions | Implemented: container/codec/bitrate band/lossless/chapter-expectation model, not a copy of video quality tiers. | Adapted, done. | None. |
| Custom Formats (conditions, scoring, import/update) | Missing. Audiobook release selection today is quality-fit only, no scoring language for "prefer dramatized," "prefer this narrator," "avoid abridged." | Gap, real value. | 1.1.5 — audiobook custom-format MVP (see below). |
| Indexers (multiple, native definitions, usenet/torrent, interactive/RSS search) | Prowlarr-only by design (`docs/design/architecture.md`); interactive search exists (`/search`), RSS-style background search exists via Wanted. | Deliberate architectural choice, not a gap. | Keep Prowlarr-first. Revisit native multi-indexer UI only if Prowlarr proves insufficient; not scheduled in 1.1.x. |
| Download clients (multiple, usenet/torrent, completed/failed handling, remove completed, remote path mappings) | SABnzbd only; queue view is read-only; no remote path mappings. | Gap — remote path mappings and queue actions have real Docker/NAS value; multi-client is lower urgency. | 1.1.4 — remote path mappings + queue actions (remove/retry/mark failed). Multi-client (qBittorrent/Transmission/NZBGet) stays a later idea, not committed to 1.1.x yet. |
| Import lists | Missing as a generic concept. Liberatarr (1.1.0) is a bespoke Wanted-sync source; OPDS/Hardcover are tracked separately (#65, #66). | Gap, but partially covered by point integrations. | 1.1.7 — generalize into an import-list abstraction once OPDS/Hardcover exist, so future sources don't each need bespoke wiring. |
| Connect (notifications) | Implemented: configurable webhooks for grab/import/health/test events, secret masking. | Adapted, done. | None. |
| Metadata (provider priority, refresh) | Implemented: Audible/Audnexus chain, refresh interval/batch size. Metadata *profiles* (language/content restrictions) are an explicit placeholder. | Partial. | 1.1.7 — metadata profiles groundwork alongside import lists. |
| Tags | Implemented: CRUD, book/root-folder assignment, filtering. | Adapted, done. | None. |
| General — host/security/proxy/logging/backups/updates | Mostly implemented; Security is nested under General instead of its own section; no proxy setting; UI theme/date-format are read-only placeholders. | Partial. | 1.1.8 — System tabs + UI settings parity pass (Security as its own page is optional/low priority). |
| UI — calendar, dates, theme, color-impaired mode, language | Calendar implemented (month grid + agenda + day modal, no iCal link). Theme/date-format are placeholders. Language (EN/DE) is a deliberate Audiarr feature beyond Starr's typical scope. | Partial. | 1.1.8 for theme/date-format; iCal/webcal link reconsidered in 1.1.2 alongside OPDS if it's cheap to add. |
| Analytics | Not implemented. | Deliberate omission. | Do not add unless explicitly requested, and then only local-only/opt-in. See "Do not copy blindly." |
| Sonarr episode/season monitoring model | N/A — Audiarr has no episode concept. Series/author/book monitoring is the correct audiobook analog and already exists at the book level. | Adapted at the right altitude. | Series-level monitoring (monitor all future books by an author/series) is worth checking as part of 1.1.3 Add New parity, not a Sonarr-episode copy. |
| Add New flow (root folder/quality profile/monitored/tags chosen at add time) | `/metadata` search → one-click add, no root folder/profile/tags/monitor choice at add time. | Gap, the single biggest structural difference from Starr's add flow (also flagged in `starr-ui-parity.md`, P0). | 1.1.3. |
| Book Detail toolbar actions (refresh/rescan/search/organize/delete) | Toolbar has Back + Delete only; organize exists as a separate page section, not a toolbar action; no refresh/rescan/search-from-detail. | Gap (also flagged in `starr-ui-parity.md`, P0). | 1.1.3. |
| Library table parity (sortable headers, richer filters, mass editor) | Sort is a dropdown (title/author only), one text filter, one tag filter; no column-header sort, no bulk/mass edit. | Gap. | 1.1.3, as part of the same UI parity slice as Add New/Book Detail (shared table component). |
| System tabs (Tasks, Events, Log Files) | Single scrolling `/system/status` page with Health/About/Updates/Backup/Logging cards; no Tasks (scheduled jobs) list, no Events log, no in-UI log viewer. | Gap (also flagged in `starr-ui-parity.md`, P0). | 1.1.8. |

## Listenarr/Chaptarr harvest matrix

Findings from local checkouts (`/home/captain/workspace/Listenarr`,
`/home/captain/workspace/chaptarr-src-probe`), filtered for what actually
fits Audiarr's architecture and audiobook-first product direction.

| Feature | Why it matters | Audiarr fit | 1.1.x action |
|---|---|---|---|
| Multi-client downloads (qBittorrent, Transmission, SABnzbd, NZBGet) | Homelab users often already run a torrent client; SABnzbd-only forces usenet. | Good fit long-term, but a real scope increase (client abstraction, per-client settings UI). | Not committed in 1.1.x; revisit after remote-path-mapping/queue-action work (1.1.4) proves out the download-client abstraction boundary. |
| Remote path mappings | Directly needed whenever the download client and Audiarr see different filesystem paths (common in Docker/NAS setups) — this is a correctness bug magnet, not a nice-to-have. | Direct fit, small and self-contained. | 1.1.4 (high priority). |
| Real-time monitoring via SignalR/websocket push | Nicer UX than polling for queue/activity updates. | Fit, but Audiarr's polling model already works; this is a pure UX upgrade. | Defer past 1.1.x unless polling proves insufficient. |
| Manual import companion + Prowlarr indexer import/upsert + indexer debug search | Overlaps with Audiarr's existing `/import` manual-match flow and Prowlarr integration. | Mostly already covered; indexer debug search (show raw indexer response for troubleshooting) is a small, useful addition. | Consider as a small add-on during 1.1.4 (queue/activity work) if time allows; not a separate slice. |
| Author/series monitoring (`AuthorMonitoringController`, `SeriesMonitoringController`) | Lets a user say "monitor everything by this author/series," not just book-by-book. | Strong fit for audiobook semantics (matches Sonarr's series-monitoring altitude, mapped to author/series instead of episodes). | Fold into 1.1.3 Add New parity as a monitor-mode choice. |
| Root folder relocation / library move workflow with progress, retry, recovery | Useful when a user reorganizes storage, but high-risk (filesystem mutation) and Audiarr has no reported need yet. | Fit is plausible but high blast-radius for the value delivered right now. | Not scheduled; revisit only if a concrete user need appears. Would require explicit backup-first + approval gating per project safety conventions. |
| ffprobe/ffmpeg metadata extraction, chapter tables, audio tag writing | Audiarr currently delegates conversion (and its metadata) to an external m4b-convertarr/command backend; richer local chapter/duration/bitrate visibility would improve the file table and book detail without owning conversion. | Good fit as *read-only enrichment* around the existing external-conversion architecture — not a reason to embed a converter. | 1.1.6. |
| MyAnonamouse-specific helper/search options | Tracker-specific search tuning. | Overlaps with Prowlarr's role (Prowlarr already normalizes tracker-specific search); duplicating tracker-specific logic inside Audiarr would fight the Prowlarr-first architecture decision. | Skip. |
| Notifications/Discord diagnostic stubs | Overlaps with Audiarr's existing Connect webhooks. | Already covered functionally. | Skip. |
| Frontend modals: manual search, library import search, root folder, quality profile, download/indexer, wanted view, calendar, downloads view | Confirms the "Starr modal pattern" (add/edit via dialog, not full navigation) is the right target shape. | Directly informs 1.1.3's Add New / Book Detail toolbar work. | Use as a reference pattern during 1.1.3 implementation, not a separate slice. |
| Narrator-aware organization, multi-edition support | Audiarr already models narrators; multi-edition (same book, different narrator/abridgement/publisher) is not yet a first-class concept in the file/book model. | Good fit, meaningful for audiobook correctness. | 1.1.6, alongside chapter/edition metadata work — keep scope to "recognize and display edition differences," not a full edition-management UI in this slice. |
| Publisher-aware / dramatized / multi-part release handling | Improves release matching quality (e.g., "avoid abridged" as a preference, not silently accepted). | Directly maps onto the Custom Formats/scoring gap already identified from the Radarr/Sonarr side. | 1.1.5, same slice as audiobook custom-format MVP. |
| Audio formats: M4B, MP3 chapters, multi-file audiobooks | Audiarr's conversion pipeline already targets M4B; multi-file MP3 handling nuances (e.g., chapter numbering across files) benefit from the ffprobe enrichment above. | Fit, incremental. | 1.1.6. |
| MP3 → M4B conversion with chapter preservation via m4b-tool | Chaptarr embeds this; Audiarr deliberately delegates to an external m4b-convertarr/command backend. | Do not copy the embedding; strengthen the *metadata visibility* around the existing external step instead. | See "Do not copy blindly" below. Covered by 1.1.6's ffprobe enrichment, not a converter rewrite. |
| Dual media libraries (audiobook + eBook roots or colocated roots) | Chaptarr manages both media types in one instance. | Out of scope — Audiarr is audiobook-only by product direction; adding eBook modeling would be a scope change requiring explicit user sign-off, not a documentation-driven addition. | Skip; flag as a "Do not copy blindly" item. |
| Metadata profiles (language/content restrictions) | Lets a user restrict which languages/editions are considered a match. | Fit, matches the already-planned "Metadata profiles" placeholder in Audiarr's settings. | 1.1.7. |
| Flexible renaming with audiobook-specific tokens | Audiarr already has `file_name_pattern` with tokens; verify parity against Chaptarr's token set (narrator, series, edition) during 1.1.6 edition work rather than as a separate exercise. | Mostly covered. | Verify/extend tokens opportunistically during 1.1.6, not a dedicated slice. |
| `ImportList`, `CustomFormat`, `MetadataProfile`, `BookInteractiveSearch`, `IndexerFlags` (code evidence from Chaptarr, itself a Readarr fork) | Confirms these are load-bearing concepts in the broader Servarr ecosystem, reinforcing the Radarr/Sonarr-side gaps already identified (Custom Formats, Import Lists, Metadata Profiles). | Cross-validates the matrix above rather than introducing new items. | No separate action; already reflected in 1.1.5/1.1.7 above. |
| `Bookshelf` UI concept, `MediaTypeToggle`, `BookEditionSelect` | UI patterns for a mixed audiobook/eBook shelf. | Only `BookEditionSelect` is relevant (edition disambiguation); `MediaTypeToggle`/mixed-shelf concepts don't apply since Audiarr is audiobook-only. | Edition selection folded into 1.1.6; skip the rest. |

## Do not copy blindly

- **No TV episode/movie-specific fields.** Sonarr's episode/season monitoring
  and Radarr's movie-collection fields have no audiobook analog. Where a
  Sonarr concept is worth adapting, map it to the *series/author/book*
  altitude Audiarr already uses, never to episodes.
- **No cloud analytics.** Radarr/Sonarr's opt-in analytics telemetry is not
  planned. If ever requested, it must be local-only and explicitly opt-in per
  this project's privacy-first stance — never adopt Sonarr's default-on
  pattern.
- **No embedded audio converter.** Chaptarr embeds m4b-tool directly. Audiarr
  deliberately keeps conversion external (m4b-convertarr/command backend) so
  the core app doesn't own ffmpeg/m4b-tool dependency management. Strengthen
  *metadata visibility* around the external step (1.1.6 ffprobe enrichment)
  rather than pulling conversion in-process, unless the user explicitly
  chooses to change that architecture.
- **No dual audiobook/eBook library.** Chaptarr's colocated audiobook+eBook
  roots are out of scope; Audiarr stays audiobook-only.
- **No tracker-specific search logic** (e.g., MyAnonamouse-specific helpers)
  that duplicates what Prowlarr already normalizes — this would fight the
  Prowlarr-first architecture decision, not extend it.
- **Keep audiobook semantics everywhere a Starr concept is adapted:**
  narrators, series/author monitoring, edition/abridgement awareness, and
  chapter metadata are first-class; video-specific vocabulary (resolution,
  HDR, codec-for-video) is not.

## 1.1.x recommended sequence

1. **1.1.1 — Parity audit + roadmap (docs-only).** This document and the
   `ROADMAP.md` update. No code changes.
2. **1.1.2 — OPDS/export feed (#65).** Already tracked; kept next because it's
   a scoped, already-planned integration (like Hardcover, #66) rather than a
   structural UI change, and unblocks the next item's optional iCal/calendar
   consumer notes.
3. **1.1.3 — Add New + Book Detail Starr action parity (#68).** Root
   folder/quality-profile/monitor-mode/tags at add time; Book Detail toolbar
   (refresh/rescan/search/organize/delete as first-class actions); library
   table sortable headers. Highest user-visible value among the P0 items in
   both parity audits.
4. **1.1.4 — Activity queue/history actions + remote path mappings (#69).** Queue
   remove/retry/mark-failed, history retry/remove, and remote path mappings
   (small, self-contained, high correctness value for Docker/NAS setups).
5. **1.1.5 — Custom release preferences / audiobook custom formats MVP (#70).**
   Scoring/conditions for narrator/edition/publisher/dramatized preferences;
   folds in delay-profile-style "wait for better" behavior as an optional
   rule rather than a separate settings page.
6. **1.1.6 — Listenarr/Chaptarr audio metadata harvest (#71).** ffprobe-based
   chapter/duration/bitrate metadata, edition/multi-file clarity in the file
   table, opportunistic naming-token verification.
7. **1.1.7 — Import lists / Hardcover (#66) / metadata profiles groundwork (#72).**
   Generalize the import-list concept once OPDS and Hardcover exist as
   concrete sources; stand up metadata profiles (language/content
   restrictions).
8. **1.1.8 — System tabs/tasks/events/logs and UI settings parity (#73).** Tasks
   list, Events log, in-UI log viewer, functional theme/date-format settings.

Each slice above should land as its own issue → branch → PR → CI cycle,
consistent with this project's "small verified milestones" principle. None of
this sequence requires or implies a `1.2.0` version bump.
