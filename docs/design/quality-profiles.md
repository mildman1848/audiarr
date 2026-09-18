# Profiles and Quality for audiobooks

Issue #13. This is the design note the acceptance criteria ask for: what
"quality" means for an audiobook manager, what's modeled and editable now,
and what's explicitly deferred.

## Why not just copy Radarr/Sonarr quality definitions

Video quality ladders (Radarr/Sonarr) are built around **resolution and
source** (SDTV → 1080p → 2160p Remux), because that's what actually changes
the viewing experience and file size. None of that applies to audiobooks:
there is no resolution, and "source" (WEB-DL vs. Remux) has no audiobook
analogue.

What actually changes the *listening* experience and file size for an
audiobook is:

- **Container/codec** — `.m4b` (AAC, chapters, single file) vs. `.mp3`
  (often multi-file, no native chapter support) vs. `.flac` (lossless,
  large).
- **Bitrate** — spoken word compresses far better than music. A 64 kbps
  mono AAC track is often perceptually transparent for speech, where the
  same bitrate would be unlistenable for music. Audiobook bitrate bands are
  therefore much lower than video/music quality ladders would suggest.
- **Chapter support** — chapter markers are how audiobook apps (and
  Audiobookshelf) let a listener jump between chapters and resume
  playback. A file with the "right" bitrate but no chapters is a worse
  audiobook than a slightly lower-bitrate file that has them. This has no
  video-quality equivalent at all.
- **Lossless vs. lossy** — relevant mostly for archival/preservation
  (FLAC rips), not for day-to-day listening.

So Audiarr's quality model is built around **container, codec, bitrate
band, chapter expectation, and losslessness** — not resolution/source.

## Data model

`app/models/settings.py`:

- `QualityDefinition` — one named quality tier: `container` (e.g. `m4b`,
  `mp3`, `flac`), `codec` (e.g. `aac`, `mp3`, `flac`), `lossless` flag,
  `min_bitrate_kbps` / `preferred_bitrate_kbps` / `max_bitrate_kbps`, and
  `chapters` (`required` / `preferred` / `not_required`).
- `Settings.quality_definitions` — the list of tiers available to build
  profiles from. Four sensible audiobook-shaped defaults ship out of the
  box (low/high bitrate M4B AAC, MP3 320, FLAC lossless) — not a copy of
  any video quality pack.
- `QualityProfile` — a named, **ordered** preference list of quality tiers
  (`quality_ids`, best first) plus a `cutoff_quality_id` (the tier at which
  Audiarr would stop seeking upgrades) and `upgrade_allowed`.

### Backwards compatibility

`QualityProfile` keeps its original MVP fields, `allowed_formats` (bare
file extensions) and `cutoff_format`, unchanged. Older `settings.json`
documents that only have those two fields still load correctly — the new
fields (`quality_ids`, `cutoff_quality_id`, `upgrade_allowed`) simply take
their defaults via Pydantic. No settings migration is needed; this is a
flat JSON document, not a database schema (see
`tests/test_settings.py::test_legacy_quality_profile_json_still_parses`).

## What's wired up in this slice

- The settings model persists real `quality_definitions` and richer
  `quality_profiles` through the existing `GET`/`PUT /api/v1/settings`
  endpoints (full-document replace, same as every other settings section).
- The **Quality** settings page (`/settings/quality`) is an editable list
  of quality definitions (name, container, codec, lossless, bitrate band,
  chapter expectation), with add/remove rows.
- The **Profiles** settings page (`/settings/profiles`) is an editable list
  of quality profiles (name, allowed formats, cutoff format, ordered
  quality-tier preference, cutoff tier, upgrade-allowed), with add/remove
  rows.
- Both pages use the same GET → merge edited fields → PUT whole-document
  flow as every other active settings page (`app/web/static/js/settings.js`).

## What's explicitly deferred (not wired in this slice)

Import matching (`app/library/importer.py`, `app/library/matcher.py`) and
conversion job dispatch (`app/conversion/worker.py`) do **not** yet read
`quality_definitions` or `quality_profiles` to make decisions. Today,
imports and conversions proceed independently of profile/quality settings.

Next slice (not part of #13): use a book's assigned quality profile to (a)
decide whether an import candidate meets the profile's allowed formats /
minimum quality tier, and (b) decide whether an already-imported book
should be queued for a conversion upgrade toward its cutoff tier. This
requires books to carry an assigned profile (a library-model change) and is
intentionally out of scope here — this slice only makes the settings
model and editor real.

## Wired behavior (#17)

Issue #17 wires the settings model from the previous section into two
read paths. Per-book profile assignment is still deferred — every
decision below uses the **first configured quality profile**
(`Settings.quality_profiles[0]`) as the default/only profile.

### Inference: `app/quality.py`

A new, pure/deterministic module (no I/O, no video-quality logic copied
from Radarr/Sonarr) turns a release/folder/file name into an
`InferredQuality` (container, codec, bitrate_kbps, chapters) and matches
it against a profile's ordered `quality_ids` to produce a `QualityFit`
(`matched_quality_id`, `status`, `reason`). `status` is one of
`preferred` (top-preference tier), `accepted` (within cutoff, or no tier
matched but the container is in the profile's legacy `allowed_formats`),
`below_cutoff` (matched a tier ranked after the cutoff), `rejected`
(container not allowed at all), or `unknown` (no container signal could
be extracted from the name at all).

Missing signals never cause a rejection: an unknown bitrate or codec is
treated as "no evidence against", not "fails to match". A tier with
`chapters: "required"` is the one exception — it needs an explicit
chapter/cue hint in the name to match.

### Release search: `app/api/routes_releases.py`

`GET /api/v1/releases/search` evaluates every release's title against the
default quality profile and adds `quality_container`, `quality_codec`,
`quality_bitrate_kbps`, `quality_chapters`, `matched_quality_id`,
`quality_status`, and `quality_reason` to each `ReleaseRow`. All
pre-existing fields are unchanged.

### Import → conversion enqueue: `app/library/importer.py`

The old hardcoded rule ("auto-enqueue conversion when
`candidate.dominant_format` is `mp3` or `m4a`") is replaced by
`should_convert_candidate()`: it reads the target container off the
default profile's top-preference quality tier (falling back to
`cutoff_format` if the tier can't be resolved) and only offers mp3/m4a
sources to the conversion backend when that target is `m4b`. An already-
`m4b` candidate is never re-offered (this slice has no upgrade-seeking
re-conversion), and a profile whose top tier targets something other than
m4b (e.g. an all-mp3 profile) never triggers conversion. This only changes
the *conversion enqueue* decision — importing itself never depends on
quality profile fit, and no file is ever moved/renamed/deleted here.
