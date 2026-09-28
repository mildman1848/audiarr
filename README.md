# Audiarr

**A Servarr-style audiobook manager — an automation loop bolted around your existing media stack, not a replacement for it.**

Audiarr handles metadata search, import matching, quality/profile decisions,
and outbound integrations (Audiobookshelf, Prowlarr/SABnzbd, m4b-convertarr)
so your audiobook library behaves like the rest of the Arr family. `1.0.0`
has shipped and the project is still actively developed — everything below
is implemented and tested, not just modeled in a settings page.

## 🌟 Features

- 🔍 **Metadata** — Audible's public catalog API (`us`/`de` and other
  marketplaces, no account/device login) with Audnexus as fallback.
- 📚 **Library** — root folders, books/editions/narrators/series with
  provider-ID attribution, plus Calendar and Wanted/Missing views over
  monitored books.
- 📥 **Import** — root-folder scanning, ASIN/ISBN or fuzzy title/author
  matching, dry-run preview before any write, manual match/ignore review for
  unmatched folders, and hardlink/copy/move import strategies (hardlink by
  default).
- 🚀 **Automation** — periodic import scans, SABnzbd auto-import on
  completed downloads, and metadata-refresh / wanted-search schedulers.
- 🎯 **Quality** — audiobook-specific profiles and quality definitions
  (container/codec/bitrate band/lossless/chapter expectations), per-book
  profile assignment, and cutoff-driven upgrade search.
- 🔁 **Integrations** — Prowlarr release search with SABnzbd grab/queue,
  Audiobookshelf connection test + scan trigger, m4b-convertarr conversion
  job tracking, and outbound Connect webhooks for grab/import/health events.
- 🔐 **Security** — forms login and API-key (`X-Api-Key`) authentication
  enforced by middleware, login rate limiting, PBKDF2 password hashing, and
  automatic config backups with retention rotation.
- 🖥️ **UI** — server-rendered, Starr-family dark UI (dense data pages,
  System Status/Tasks/Events, Activity queue/history) with full English and
  German i18n.
- 🐳 **Docker** — LSIO/s6-style image using `/config`, `/data`, `PUID`,
  `PGID`, `TZ`, `UMASK`, and `FILE__`-prefixed secrets.

## 🧭 Out of scope (deliberately)

- No embedded Audible login or account management — catalog search uses
  Audible's public, no-login API, while optional Liberatarr/Libation sync can
  import already-owned library entries as Wanted books.
- No embedded audio converter; Audiarr delegates to `m4b-convertarr`.
- Update checks are display-only (GitHub releases API, disableable); Audiarr
  never auto-updates.

This is deliberate. Shipping a fake Arr clone is easy. Maintaining it is how
people discover quiet despair.

## 📦 Installation

Docker Compose quick start:

```yaml
services:
  audiarr:
    image: ghcr.io/mildman1848/audiarr:1.1.4
    # or: docker.io/mildman1848/audiarr:1.1.4
    container_name: audiarr
    restart: unless-stopped
    ports:
      - "8787:8787"
    environment:
      PUID: "1000"
      PGID: "1000"
      TZ: Etc/UTC
      UMASK: "002"
    volumes:
      - ./config:/config
      - ./data:/data/audiobooks
```

See [`.env.example`](.env.example) and [`docker-compose.yml`](docker-compose.yml)
for the full variable list, `FILE__`-secret variants, and a build-from-source
example.

Important defaults:

| Setting | Default |
|---|---|
| Port | `8787` |
| Config | `/config/audiarr` |
| Media root | `/data/audiobooks` |
| Audible locale | `us` |
| UI language | `en`, with `de` available |
| Optional translation backend | `none` by default; LibreTranslate-compatible backend optional |
| Version | `1.1.4` |

Versioning follows the Audiarr roadmap, not the household/fork `mldm<N>`
suffix used for image revisions:

```text
0.<phase>.<completed item within that phase>
```

Examples: after the second item in Phase 3 is complete, the image version is
`0.3.2`; after the first item in Phase 4 is complete, it is `0.4.1`. Audiarr
has since shipped `1.0.0` and moved to standard `1.x` versioning.

## 🚀 Getting Started (development)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
make validate
uvicorn app.main:app --reload --port 8787
```

Open: <http://127.0.0.1:8787>

## 🛣️ Roadmap

See [`ROADMAP.md`](ROADMAP.md) for the full phase-by-phase history.

`1.0.0` shipped with Phases 1–6 complete. Post-1.0 integration work
(Liberatarr as a Wanted source #64, OPDS export #65, Hardcover #66) is
tracked in the "Post-1.0 - Integrations" milestone.

## 🔒 Security & Backups

Forms login and API-key auth are enforced by middleware (not just modeled
settings), with login rate limiting and PBKDF2 password hashing. Automatic
and on-demand backups of `audiarr.db` and `settings.json` ship as a ZIP
archive with a manifest and retention rotation — see
[`docs/backup-restore-runbook.md`](docs/backup-restore-runbook.md) for
contents, restore procedures, rollback, and a verification checklist.
Update checks are display-only (GitHub releases API) and never trigger an
auto-update.

Publishing is gated: `make validate` must pass, registry secrets must be
configured explicitly, and a Docker build/smoke test must succeed on a host
with a running daemon before any image is pushed. See
[`docs/publishing.md`](docs/publishing.md) and
[`docs/release-hardening.md`](docs/release-hardening.md) for the full model.

## 🌍 Translations

English is the source language; German is a first-class, fully translated
locale, not an afterthought. Parity between the two is enforced by
`tests/test_i18n_parity.py`.

## 👨‍💻 Development

Common targets (see `make help` for the full list):

```bash
make validate   # lint + pytest + version consistency + actionlint if present
make build      # local single-platform Docker build
make smoke      # boot the built image and check /health
make security   # Trivy HIGH/CRITICAL config scan
```

`pytest` and `ruff`-based static checks gate `make validate`. The UI is
server-rendered Jinja2 + vanilla JS — there is no frontend build step.

## 🙌 Contributing

Issues are welcome. Open PRs against `main` with conventional commits
(`feat:`, `fix:`, `refactor:`, `docs:`, `chore:`) and a green `make validate`.

## 📄 License

MIT — see [`LICENSE`](LICENSE).
