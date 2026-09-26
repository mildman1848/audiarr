# Starr UI Parity Audit (issue #48)

Evidence-based gap matrix comparing Audiarr's **current** web UI against
well-established Radarr/Sonarr/Lidarr ("Starr family") UI conventions, so
follow-up issues #49 (shell/nav), #50 (data pages), #51 (settings), #52
(polish) have concrete, prioritized targets.

Product direction (confirmed): stay as close as practical to the Starr
UI/UX family. Do not invent a unique design language. Deviate only where
audiobook-specific semantics require it (narrators, m4b conversion,
import-strategy, provider chain).

## Method and evidence basis

- Audiarr side: read directly from `app/web/templates/`, `app/web/routes.py`,
  `app/web/static/css/style.css`, `app/web/static/js/*.js`, and
  `app/web/i18n/{en,de}.json` on branch `docs/starr-ui-parity-audit`
  (2026-09-26). Every "Audiarr current state" cell cites a file (and line
  where useful).
- Starr side: based on well-established Radarr/Sonarr/Lidarr UI
  conventions (dark theme, left icon sidebar, top toolbar, dense
  sortable/filterable tables, poster/list toggles, modal dialogs, settings
  left sub-nav with per-section save, System: Status/Tasks/Events/Backup/
  Updates tabs, toasts, empty states with actions). Where I could not
  verify a specific current-version Starr detail against source, it is
  marked `assumed`.
- No code was changed. This document is docs-only.

## Legend

- **Gap**: `none` (already matches Starr) / `minor` (cosmetic or small
  behavioral delta) / `major` (missing capability or structurally
  different from Starr convention).
- **Priority**: P0 (blocks release-feel Starr parity) / P1 (nice, visible
  polish) / P2 (optional / long-tail).
- **Owner**: `#49` shell/nav, `#50` data pages, `#51` settings, `#52`
  polish, or `won't-do-1.0` (with reason).
- **Audiobook exception?**: `yes` + why, or `no`.

## Gap matrix

| Area | Starr reference behavior | Audiarr current state | Gap | Priority | Owner | Audiobook exception? |
|---|---|---|---|---|---|---|
| Global shell — theme | Dark theme, orange/amber accent, flat panels (`assumed`: exact hex varies by Sonarr v3/v4 theme). | Dark theme via CSS custom properties, `--bg-body:#1c1e21`, `--accent:#f7991c` (amber), flat panel borders. `app/web/templates/login.html:9-19`, `style.css` root vars. | none | — | — | no |
| Global shell — nav placement | Fixed-width left icon+label sidebar, collapses to off-canvas on mobile. | Left `<aside class="sidebar">` with icon glyph + label per item, off-canvas drawer + backdrop + Escape-to-close on mobile (`base.html:20-41`, `common.js:120-175`). | none | — | — | no |
| Global shell — landing page | Radarr/Sonarr/Lidarr have **no separate "Dashboard"**; the app opens directly on the Movies/Series/Artist list (their main library view), with health/activity as smaller widgets, not a full landing page (`assumed`, consistent across all three apps' known layouts). | Audiarr's `/` renders a dedicated Dashboard (`index.html`) with quick-action cards + stat cards + a conversion-jobs widget; Library is a separate nav item. `routes.py:118-143`. | major | P1 | #49 | no — this is a structural nav decision, not audiobook-specific. Recommend a product call: either drop the standalone Dashboard and land on Library (closest Starr parity), or explicitly document the Dashboard as an intentional deviation. |
| Global shell — search | Top toolbar has a global search icon/box that jumps to results across the library (`assumed` exact placement). | Topbar search button opens a modal overlay (`#global-search-overlay`) that queries metadata search live, debounced 300ms, capped at 10 results; results are **not clickable** (no book id returned by that endpoint). `base.html:53-55,78-83`, `common.js:177-260`. | minor | P2 | #52 | no |
| Global shell — toasts | Bottom-right (or top-right) transient toasts for background actions. | `#toast-container` bottom-right, `role="status"`, auto-dismiss 5s, click-to-dismiss, success/error/info kinds. `base.html:76`, `common.js:19-48`, CSS `.toast*`. | none | — | — | no |
| Global shell — language switch | N/A (Starr apps are not EN/DE toggle in top bar; language is an OS/browser or app setting). | EN/DE toggle buttons live in the topbar (`base.html:56-59`). | n/a (audiobook/home-lab specific) | — | won't-do-1.0 (deliberate Audiarr feature) | yes — bilingual household requirement, not a Starr pattern to imitate |
| Dashboard / Health widget | Sonarr/Radarr show a persistent Health warnings widget (indexer down, root folder missing, etc.) prominently, often as a banner. | System health is only on `/system/status` (`system.html:18-24`), not surfaced on the Dashboard/global shell. | minor | P1 | #49 | no |
| Library — list vs grid toggle | Poster/grid view and table/list view toggle buttons in the page toolbar, state often persisted. | `#view-grid-btn` / `#view-table-btn` toggle buttons exist (`library.html:10-11`), covers rendered with real aspect ratio in grid (`style.css:794-829` "Arr-style cover-card grid"). View persistence not verified — `library.js:265-266,585-586` toggles DOM only, no localStorage/setView persistence check confirmed beyond in-memory state. | minor | P2 | #50 | no |
| Library — table columns | Dense, sortable table: click a column header to sort (title, author, size, etc.), often with a saved sort/column-filter state. | Table view exists (`library.js:200-220`, columns incl. narrators col) but sorting is a **dropdown** (`#library-sort`, `library.html:18-21`, options: title/author only), not clickable column headers. No column-level filter, only one global text filter (`#library-filter`) + one tag-filter dropdown. | major | P0 | #50 | no |
| Library — filter/search | Toolbar text filter + tag/genre filter + sort. | Text filter (`#library-filter`), tag filter dropdown (`#library-tag-filter`), sort dropdown (`#library-sort`, title/author only — no size/date-added/size sort). `library.html:14-21`. | minor | P1 | #50 | no |
| Library — empty state | Icon + message + primary action button ("Add your first movie") when library is empty. | Shared `.empty-state` component exists with icon/title/hint/action, rendered via `window.AudiarrUI.emptyState()`; used on Library/Wanted/Calendar/Activity per code comment. `style.css:386-423`. Actual per-page wiring not individually re-verified beyond CSS + comment. | none (assumed correct per comment) | — | — | no |
| Library — root folders + import strategy | Root folder management typically lives in Settings → Media Management, not the library page itself; import strategy (hardlink/copy/move) is an advanced/media-management setting. | Root-folder add form and import-strategy picker (hardlink/copy/move) are inline on the Library page itself (`library.html:59-80`), in addition to `settings/media_management.html` linking back to `/library`. | minor | P2 | #50 | yes — Audiarr's manual "dry-run import" workflow (`library.html:83-107`) is audiobook-specific (folder-name parsing across locales), not a Starr concept; keep it, but consider moving root-folder CRUD fully into Settings for parity while leaving the import trigger on Library. |
| Book detail — layout | Hero panel: cover, title, author(s) chips, tags, quality profile, monitored toggle, stats row (size, runtime, quality), then file table + activity/history for that item. | Hero panel with cover, title/subtitle, authors chips, **narrators chips** (audiobook-specific), quality profile line, tag chips, stats, file table with per-file `format` column. `book_detail.js:47,77-135,113,179`. | none (structurally close) | — | — | narrators row: yes, audiobook-specific, keep |
| Book detail — actions | Toolbar: Edit, Delete, Refresh & Scan, Search Monitored, Rename. Often a "..." overflow menu. | Toolbar has only Back + Delete (disabled by default) (`book_detail.html:7-14`). No refresh/rescan, no manual search-from-detail-page, no rename action visible in the toolbar. | major | P0 | #50 | no |
| Book detail — organize/rename | Sonarr/Radarr "Rename files" is typically a preview-then-apply modal reachable from the item or a bulk Settings→Media Management action. | Audiarr has an inline "Organize" card with Preview/Apply buttons, disabled by default, plus a result area (`book_detail.html:21-34`). Functionally close but presented as a static page section rather than a modal/dialog. | minor | P2 | #50 | no |
| Book detail — conversion status | N/A in Starr apps. | No per-book m4b conversion status/badge found on the book detail page (only a global "conversion-jobs" widget on the Dashboard, `index.html:81-89`). | major (audiobook feature gap, not a Starr parity item) | P1 | #50 | yes — m4b conversion status per book is audiobook-specific and should be added to the book detail hero/file table, not modeled on Starr (which has no analog) |
| Add New / metadata search | Dedicated "Add New" page: search provider, then an **Add modal** per result with root folder, quality profile, monitor mode, tags, and a "Search for missing" toggle before committing. | `/metadata` page: single search form (query/locale/limit) → results table → one-click "Add to library" button per row (`metadata.js:89-127`). Payload has no root folder, quality profile, monitor-mode, or tags — these are not chosen at add time. | major | P0 | #50 | no — this is the single biggest structural gap vs. Starr's add flow |
| Releases search (interactive search) | Per-item "Interactive Search" results table: protocol, indexer, size, age, quality/rejection reason, one-click Grab; often filterable by "only show approved". | `/search` page: query form → results table with protocol badge, quality-status badge (preferred/accepted/below_cutoff/rejected), age, size, per-row Grab button with independent in-flight state; "only quality-fit releases" toggle persisted in localStorage. `search.js:1-80+`, `search.html`. | none | — | — | no |
| Wanted / Missing | Two tabs or list filters: "Missing" and "Cutoff Unmet", each a sortable table with per-item quick-search action. | Two stacked panels: Missing list + Cutoff-unmet list (`wanted_missing.html:20-42`), one shared text filter. Tabs vs. stacked panels is a minor layout delta; per-item quick-search-from-row not confirmed. | minor | P1 | #50 | no |
| Calendar | Month grid + agenda list, iCal/webcal subscribe link, click a day for a modal listing releases due that day. | Month grid + agenda list + day-click modal (`calendar.html:17-50`, `#calendar-day-modal`). No ICS/webcal subscribe link found anywhere in `calendar.html` or `calendar.js`. | minor | P2 | won't-do-1.0 (low value without external calendar consumers in a single-user homelab context; revisit if requested) | no |
| Activity — Queue | Live download queue with progress bars, per-item pause/resume, remove, change priority, and a status/error column. | Queue table with status icon, progress bar, import-status badge; polls every 5s while tab visible (`activity.js:1-90`). **No per-item remove/pause/priority actions** found — read-only view of the SABnzbd queue. | major | P0 | #50 | no |
| Activity — History | Sortable history table: title, event type, date, quality, indexer, with a "Remove from history" / "Mark as failed" / manual-import-retry action per row. | History tab exists (`activity.html:31-39`, `#activity-tab-history`), shows completed/failed status badges and import-status badges (`activity.js:72-90`). No confirmed per-row retry/remove actions. | major | P1 | #50 | no |
| Import (unmatched files) | Radarr/Sonarr "Manual Import" screen: table of unmatched files/folders with inline match search and per-row "Import" action; a separate "ignored" list is not a standard Starr concept. | `/import` page: unmatched list, collapsible "ignored" `<details>` section, and a match modal (`#import-match-overlay`) with inline search-by-query + locale (`import.html:16-63`). Structurally close to Starr's manual import, modal-based matching is good parity. | minor | P1 | #50 | ignored-list: yes, audiobook-specific de-noising for repeated false-positive folders, keep |
| System — tabs | System page has distinct tabs: **Status, Tasks, Events, Log Files, Backup, Updates** (`assumed`, standard across Radarr/Sonarr/Lidarr v3/v4). | Single scrolling page with card sections: Health, About, Updates, Backup, Logging (`system.html:17-96`). **No Tasks tab** (scheduled/recurring jobs list), **no Events tab** (event log with filters), **no Log Files viewer/download** in the UI (only a logging-level/retention summary). | major | P0 | #49 | no |
| Settings — navigation structure | Left vertical sub-nav (or top tab strip in v4) with sections: Media Management, Profiles, Quality, Custom Formats, Indexers, Download Clients, Import Lists, Connect, Metadata, Tags, General, UI. Selecting a section loads it in place; a single Save button per section, dirty-state indicator. | Horizontal pill-row sub-nav (`settings/_nav.html`, `.settings-subnav`) rendered per dedicated `/settings/<slug>` page (`shell.html`), not a persistent left sub-nav — matches Sonarr v4's move to horizontal tabs reasonably well. Per-section save bar with dirty-state text (`settings_no_changes`/`settings_unsaved_changes`, `settings.js:745-748`) and a "Show advanced" toggle (`shell.html:20-27`) — good parity. | minor | P2 | #49 | no |
| Settings — Media Management | Root folders, file naming pattern, rename on import, delete empty folders, importing completed downloads. | Naming pattern, rename toggle, delete-empty-folders, scan interval, SAB import category/interval, wanted-search interval + counters. `media_management.html`. Root folder CRUD lives on Library page instead of here (see Library row above). | minor | P1 | #51 | no |
| Settings — Profiles (Quality Profiles) | Table/list of quality profiles, each with cutoff + allowed qualities, add/edit inline or via modal. | `#profiles-editor` inline editor + "Add" button (`profiles.html`), no `<dialog>`/modal found in `settings.js` — inline card-based editing. Functionally plausible parity, not modal-based. | minor | P2 | #51 | no |
| Settings — Quality (definitions) | Table of quality definitions with min/max size sliders per quality tier. | `#quality-definitions` list + "Add" button (`quality.html`). Editor internals not fully re-verified line-by-line; structure present. | none (assumed) | — | — | no |
| Settings — Indexers | List of configured indexers (add/edit/test/delete), usually via Prowlarr sync or native indexer definitions. | Single Prowlarr connection card (enable/name/url/api-key/test) — Audiarr delegates indexer management to Prowlarr rather than modeling native indexers. `indexers.html`. | minor | P2 | won't-do-1.0 (architectural choice: Prowlarr-only is intentional per `docs/design/architecture.md`) | no |
| Settings — Download Clients | List of download clients (add/edit/test/delete/priority), typically supports multiple clients. | Single SABnzbd client card (enable/name/url/api-key/category/test) — one client, not a list. `download_clients.html`. | minor | P2 | won't-do-1.0 (MVP scope: SABnzbd-only; revisit if multi-client is requested) | no |
| Settings — Connect (notifications) | List of notification connections (Discord, Slack, webhook, etc.) with add/edit/test/delete, each with event-trigger checkboxes. | `#connect-list` + "Add" button, dedicated `connect.js` (`connect.html`). List-based, matches Starr's Connect pattern structurally. | none (assumed, not deeply re-verified) | — | — | no |
| Settings — Metadata | Provider priority order, refresh interval/batch size + counters; "Metadata profiles" explicitly marked "next" (placeholder). | Locale, provider-order display (read-only string, not drag-to-reorder), refresh interval/batch-size + last-run/updated/failed/remaining counters, profiles sub-section is an explicit "coming next" callout. `metadata.html`. | minor | P1 | #51 | yes — provider chain (Audible/Audnexus order) is audiobook-specific; Starr has no analog. Drag-to-reorder for provider priority would still be good Starr-pattern UX (list reordering is a common Starr convention) — currently missing. |
| Settings — Tags | List/chip management of tags with color picker, add/delete. | `#tags-list` + label input + color `<input type="color">` + add button (`tags.html`). Close structural parity with Starr's Tags page. | none | — | — | no |
| Settings — General | Host info (read-only app name/port), Security (auth method/user/password/API key + copy/regenerate), Updates, Backups, Danger Zone. | All present under one page in subsections (`general.html:11-95`) rather than Starr's separate top-level "Security" section — Sonarr/Radarr have Security as its own settings tab, not nested under General. | minor | P2 | #51 | no |
| Settings — Security as own page | Dedicated Security settings section (own nav entry). | Nested subsection inside General (`general.html:19-42`), not its own `/settings/security` route — confirmed absent from `SETTINGS_SECTIONS` in `routes.py:35-102`. | major | P1 | #51 | no |
| Settings — UI | Theme, date/time format, first-day-of-week, show relative dates, language. | Language selector (functional) + theme/date-format shown as **read-only summary text**, explicit placeholder callout (`settings_ui_placeholder_note`). `ui.html`. | major | P1 | #51 | no — theme/date-format are standard Starr settings, not audiobook-specific; currently non-functional placeholders |
| Settings — "planned" sections | N/A — Starr sections are always functional. | Audiarr has an explicit `status: "planned"` mechanism (`routes.py:30-34`) that badges a section and hides its save bar/advanced toggle when there's nothing to save yet (`shell.html:9-12,20,35`). This is an honest, deliberate anti-pattern-avoidance choice, not a bug. | none (by design) | — | — | no |
| Login page | Standalone dark-themed login card, centered, app branding, username/password, inline error. | Standalone login page, own inlined `<style>` (not `style.css`), same color palette values duplicated (`login.html:8-19` vs `style.css` root vars) rather than shared. Functionally matches Starr; the **duplicated CSS variables** are a maintainability nit, not a visual gap. | minor | P2 | #52 | no |
| Error / empty states (general) | Consistent empty-state pattern across all list pages; 404/error pages styled to match the shell. | Shared `.empty-state` CSS component confirmed (`style.css:386-423`). No dedicated 404/error page template found (`Glob` of `templates/` shows no `404.html`/`error.html`) — FastAPI's default `HTTPException` path is used for e.g. unknown settings section (`routes.py:194-196`), which likely renders a bare JSON/default error page, not shell-styled. | major | P1 | #52 | no |
| i18n EN/DE parity of shell strings | N/A (Starr apps use community Transifex translations, not a relevant comparison). | `en.json` and `de.json` both have exactly 628 keys, zero missing on either side (verified via direct diff, 2026-09-26). | none | — | — | n/a |
| Keyboard-friendly flows | Global search shortcut (e.g. focus search on `/` or a hotkey), Escape closes modals/overlays consistently. | Escape closes the mobile sidebar drawer and the global search overlay (`common.js:161-166,257-259`). No keyboard shortcut to open global search (e.g. `/`) found. Other modals (import-match overlay, calendar day modal) not confirmed to have Escape handling — only checked common.js, not import.js/calendar.js individually for their own overlay Escape handling. | minor | P2 | #52 | no |

Row count: **31**.

## (a) Prioritized Top-10

1. **Add New flow has no root folder / quality profile / monitor-mode picker at add time** (metadata search results row → direct POST). This is the single largest structural gap vs. Starr's Add modal. — P0, #50
2. **System page is missing Tasks and Events tabs entirely**, and has no Log Files viewer — only Health/About/Updates/Backup/Logging cards on one scrolling page. — P0, #49
3. **Activity Queue is read-only**: no remove/pause/priority-change actions on in-flight downloads. — P0, #50
4. **Library table has no clickable sortable column headers**, only a 2-option (title/author) sort dropdown; no column filters. — P0, #50
5. **Book detail toolbar is missing Refresh/Rescan and manual "Search" actions** — only Back and a disabled Delete. — P0, #50
6. **Security has no dedicated settings section/route** — it's nested inside General, unlike Starr's own Security tab. — P1, #51
7. **Settings → UI is mostly non-functional placeholders** (theme, date format shown as static read-only text) even though the page implies they're settable. — P1, #51
8. **Activity History has no per-row retry/remove/mark-failed actions.** — P1, #50
9. **No dashboard/global health banner** — health/warnings only live on `/system/status`, invisible from the rest of the app. — P1, #49
10. **No shell-styled 404/error page** — unhandled routes/sections likely fall through to FastAPI's bare default error response instead of matching the dark-theme shell. — P1, #52

Structural/product-level item to flag separately (not purely a UI bug): Audiarr's standalone **Dashboard landing page** has no equivalent in Radarr/Sonarr/Lidarr, which land directly on their library list. Worth an explicit product decision before #49 lands: keep it as an intentional Audiarr addition, or converge `/` to redirect to `/library` for closer parity.

## (b) Smoke-test targets for Hermes (post #49–#52)

**After #49 (shell/nav):**
- Confirm sidebar nav, off-canvas drawer (mobile width), Escape-to-close, and active-page highlighting still work for every nav item.
- If System gains Tasks/Events tabs: verify tab switching, and that Health/About/Updates/Backup/Logging content still renders correctly under the new tab structure.
- If a global health banner is added: trigger a real failure condition (e.g. disable Audiobookshelf connection) and confirm the banner appears outside `/system/status` too.
- If the Dashboard-vs-Library landing decision changes `/`: confirm `/` still resolves correctly and old bookmarks/nav links to `/` aren't broken.

**After #50 (data pages):**
- Add New: walk the full add flow end-to-end (search → pick root folder/quality profile/monitor mode → add → confirm book appears in Library with correct settings applied).
- Library: click column headers to confirm sort direction toggles and persists across a page reload; confirm grid/table toggle still renders narrator/tag/badge data correctly in both views.
- Book detail: exercise any new Refresh/Rescan/Search toolbar actions against a real book; confirm the Organize preview/apply flow still works unchanged.
- Activity Queue: trigger a real SABnzbd queue item, then exercise remove/pause/priority actions from the UI and confirm the backend call succeeds (check SABnzbd state after).
- Activity History: exercise any new retry/remove per-row action against a real history entry.
- Wanted/Missing: confirm Missing and Cutoff Unmet still both load correctly if converted from stacked panels to tabs.

**After #51 (settings):**
- If Security becomes its own `/settings/security` page: confirm the auth method/username/password/API key fields still save correctly and the settings sub-nav includes it without breaking the horizontal pill layout on narrow viewports.
- Settings → UI: if theme/date-format become functional, change each and confirm the shell actually re-renders with the new value (not just a saved-but-unused setting).
- Re-verify the dirty-state save bar (`settings_unsaved_changes` vs `settings_no_changes`) on every section you touch — this is shared logic (`settings.js:745-748`) and easy to break across all 11 sections at once.
- Confirm EN/DE parity (`en.json`/`de.json` key counts equal) is still intact for any new/changed strings — re-run the key-diff check, not just a visual DE toggle click.

**After #52 (polish):**
- Add a shell-styled 404/error page: hit an unknown route and an unknown `/settings/<slug>` and confirm both render inside the dark-theme shell, not FastAPI's bare default.
- Login page: if its CSS is deduplicated into `style.css`, confirm the login page still renders correctly with **no** authenticated session/sidebar assets loading (it must stay a fully standalone, pre-auth page).
- Global search: if a keyboard shortcut is added, confirm it doesn't conflict with typing in any existing text input across all pages.
- Calendar: if an ICS/webcal link is added, confirm it authenticates correctly (API key in URL) or is explicitly documented as requiring the app to be reachable from the calendar client's network.

## (c) Keep audiobook-specific (do not converge to Starr)

- **Narrator field/chips** on Library table, Book detail hero, and Import match results — Starr has no narrator concept. (`book_detail.js:91-92,128`, `library.js:200,217`)
- **m4b conversion status/jobs** — Dashboard conversion-jobs widget and the backend conversion pipeline; recommend *adding* (not removing) a per-book conversion badge on Book detail, since this is currently a gap even by audiobook standards, not something to trim toward Starr.
- **Import strategy badges (hardlink/copy/move)** per root folder — audiobook/homelab storage semantics tied to Audiarr's conservative import design (`docs/design/architecture.md`), no direct Starr equivalent.
- **Manual "dry-run import" with locale selection** on the Library page — folder-name parsing across Audible marketplace locales (de/us/uk) is audiobook-specific.
- **Provider chain / provider-order display** in Settings → Metadata (Audible-first, Audnexus fallback) — no Starr analog; Starr's metadata section (TheTVDB/TMDb single-provider or Skyhook) doesn't have a user-facing chain concept the same way.
- **Ignored-imports list** on the Import page — de-noising repeated false-positive folder matches is an audiobook-import-quality feature, not a standard Starr manual-import concept.
- **EN/DE bilingual shell** (topbar language switch, `en.json`/`de.json`) — a deliberate Audiarr/household requirement, not present in Starr apps' own UI chrome.

## Open question for product (not a UI bug, needs a decision before #49)

Should `/` keep its dedicated Dashboard (quick actions + stat cards), or
converge to Starr convention and land directly on `/library`? This shapes
scope for #49 and is called out above rather than assumed either way.
