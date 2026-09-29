# Release preferences for audiobooks

Issue #70. This is the design note the acceptance criteria ask for: what
"release preferences" mean for an audiobook manager, what's modeled and
editable now, and what's explicitly deferred.

## Why not just copy Radarr/Sonarr Custom Formats

Radarr/Sonarr's Custom Formats are a general condition DSL (regex against
release title, size, source, resolution, language, indexer flags, ...)
combined into named formats that feed a scoring table per quality profile,
plus separate Release Profiles (must-contain/must-not-contain/preferred
terms) and Delay Profiles (protocol-specific wait windows before grabbing).
That's three separate, fairly heavy features.

An audiobook manager doesn't need most of that surface: there's no
resolution/source/language-flag matrix to condition on, and an audiobook
household's actual preferences are almost always expressible as "does this
term appear in the title" (unabridged vs. abridged, a preferred narrator's
name, "full cast" vs. not) plus a hard "never grab this" block list. So
Audiarr's MVP collapses all three Radarr/Sonarr concepts into one small
settings section: two lists of terms (preferred, blocked) and one score
threshold — not a condition DSL, not a second delay-profile page.

## Data model

`app/models/settings.py`:

- `ReleasePreferenceTerm` — one positive-scoring signal: `term` (matched
  case-insensitively, whole-word, against a release title), `score`, a
  `category` label (`general`/`narrator`/`publisher`/`language`/`edition`
  — display/organizational only, every category is matched the same way),
  and `enabled`.
- `ReleaseBlockedTerm` — one `term` that rejects a release outright if
  matched, plus `enabled`.
- `ReleasePreferencesSettings.minimum_preference_score` — a release whose
  total preferred-term score falls below this is also rejected. This is
  the MVP's stand-in for Radarr/Sonarr's Delay Profiles ("wait for a
  better release") without a time-based wait window or a second settings
  page: an operator who wants to be pickier just raises the threshold.
- `Settings.release_preferences` — the persisted document, defaults to
  audiobook-shaped starter data: `unabridged` preferred (+10, category
  `edition`), `abridged` and `dramatized` blocked. These defaults are
  deliberately audiobook-specific examples, not a copy of any video
  custom-format pack.
- Existing `quality_definitions`/`quality_profiles` are untouched — release
  preferences are a fully independent, additive settings section, so
  existing quality profile behavior keeps working exactly as before.

### Word-boundary matching, not substring

Terms are matched with `\bterm\b` (case-insensitive), not a plain
substring search. This matters for the default data itself: `abridged` is
a substring of `unabridged`, but the word-boundary regex correctly treats
them as different words, so a release titled "... Unabridged ..." is not
accidentally blocked by the "abridged" blocked-term default.

## Scoring: `app/release_preferences.py`

A new, pure/deterministic module (no I/O, no dependency on
`app/quality.py`) turns a release title into a `ReleasePreferenceScore`
(`score`, `status`, `reasons`):

1. Any enabled blocked term matching the title rejects immediately —
   `status="rejected"`, `reasons` explains which term.
2. Otherwise every enabled preferred term matching the title adds its
   score and a reason string.
3. If the total score is below `minimum_preference_score`, the release is
   also rejected (reason names the threshold).
4. Otherwise the release is accepted, with a reason noting "no
   preferred/blocked terms matched" when the score is 0 and nothing
   matched at all.

This module is intentionally independent of `app/quality.py`'s container/
codec/bitrate fit — a release's quality fit and its preference score are
two separate axes that callers combine, not one merged engine. This keeps
`app/quality.py` and its existing tests completely unchanged (acceptance
criterion: "existing quality profiles continue to work").

## Wired behavior

### Interactive release search: `app/api/routes_releases.py`

`GET /api/v1/releases/search` evaluates every release's title against
`Settings.release_preferences` in addition to the existing quality fit,
and adds `preference_score`, `preference_status`
(`"accepted"`/`"rejected"`), and `preference_reasons` to each `ReleaseRow`.
All pre-existing fields (including the `quality_*` ones from issue #17)
are unchanged. This is deliberately display-only here: a human looking at
the Releases page can still choose to grab a "rejected" release (e.g. they
know the blocked term is a false positive for this particular book) — the
UI just makes the reasoning visible (`search.js`'s new "Preference"
column/badge, tooltip = the reasons list).

### Unattended Wanted upgrade search: `app/api/routes_wanted.py`,
`app/wanted_scheduler.py`

`find_fitting_release()` (shared by the manual `POST
/api/v1/wanted/cutoff/{book_id}/search` action and the periodic wanted
scheduler) now also scores each Prowlarr result with `score_release()` and
**drops** any release whose preference status is `"rejected"` before
considering it — a blocked-term/below-minimum-score release is never
grabbed automatically, matching the acceptance criterion "blocked_terms:
release becomes rejected / not grabbable if term appears". Among the
remaining fitting releases, ties are broken by preference score (higher
first), then quality tier, then seeders — same shape as the existing
tier/seeder sort, just with one more sort key.

This is the one place where "rejected" actually blocks an action rather
than just being surfaced; the interactive search/grab endpoint
(`POST /api/v1/releases/grab`) is unchanged and still lets a human grab
anything they want.

## What's explicitly deferred (not wired in this slice)

- **Real narrator/publisher/language metadata matching.** `category` on a
  preferred term is a label today; matching is always "does this term
  appear in the release title", not "does this release's actual narrator
  metadata equal X". Audiarr doesn't have per-release narrator/publisher/
  language metadata available at search time (only a title string from
  Prowlarr) — wiring real metadata-driven matching would need release
  metadata Audiarr doesn't fetch today, and is a larger slice than this
  MVP.
- **Time-based delay profiles.** `minimum_preference_score` is the only
  "wait for a better release" mechanism. There is no per-protocol wait
  window, no "wait N hours then grab the best available anyway" logic —
  deliberately, per the issue's explicit guidance not to clone Radarr/
  Sonarr's Delay Profiles page-for-page.
- **Manual grab enforcement.** `POST /api/v1/releases/grab` (interactive,
  human-initiated) does not check release preferences at all; only the
  unattended Wanted upgrade path does. A human who sees the "Rejected"
  badge and grabs anyway is trusted to know what they're doing, same as
  Radarr/Sonarr's own interactive search.
- **A condition DSL.** No regex/size/protocol conditions, no custom-format
  scoring table per quality profile — term + score + enabled is the whole
  model, per the issue's explicit "do not build a giant custom-format
  DSL" guidance.
