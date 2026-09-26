# Audiarr

Servarr-style audiobook manager with an LSIO/s6 Docker image.

Audiarr manages an audiobook library end to end: metadata search, import
matching, quality/profile settings, and outbound integrations (Audiobookshelf,
Prowlarr/SABnzbd, m4b-convertarr) around an existing media stack. It is
pre-1.0 and still growing, but the core workflows below are implemented and
tested, not just modeled.

## What works today

- FastAPI backend with `/health`, `/api/v1/system/status`, `/api/v1/settings`,
  and the library/import/metadata/releases/connections/webhooks APIs.
- Metadata provider chain: Audible's public catalog API first (`us`/`de` and
  other marketplaces), Audnexus as fallback.
- Library domain model and persistence: root folders, books, editions,
  narrators, series, files, with provider-ID attribution.
- Import pipeline: scans root folders, matches by ASIN/ISBN hint or
  fuzzy title/author, dry-run preview before any real import, manual
  match/ignore review for unmatched folders.
- Calendar and Wanted/Missing views over monitored books.
- Audiobookshelf connection (URL/API key, connection test, scan trigger).
- m4b-convertarr connection for external MP3/M4B conversion job tracking.
- Prowlarr indexer + SABnzbd download client: release search, grab, and an
  Activity page with live queue/history.
- Forms login and API-key authentication middleware (Radarr/Sonarr-style
  Security settings), not just a modeled setting.
- Sonarr-style settings: dedicated pages per section (Media Management,
  Indexers, Download Clients, Metadata, General/Security, UI, Conversion are
  fully editable; Profiles, Quality, Connect, and Tags are read-only/planned
  and clearly marked as such in the UI).
- Server-rendered web UI (Jinja2 + vanilla JS, no build step) with a dark
  Servarr-like layout, Audible-orange accent, and full English/German i18n.
- LSIO/s6-style Docker image using `/config`, `/data`, `PUID`, `PGID`, `TZ`,
  `UMASK`, and `FILE__` secrets.

## What is intentionally not done yet

- No personal Audible account/library sync — catalog search uses Audible's
  public, no-login API only.
- No editable Profiles/Quality/Tags/Connect UI yet; those settings sections
  are read-only or placeholder and marked "Planned" (see `ROADMAP.md`).
- No embedded audio converter; Audiarr delegates to `m4b-convertarr`.
- Update checks are display-only (GitHub releases API, disableable); Audiarr
  never auto-updates.

This is deliberate. Shipping a fake Arr clone is easy. Maintaining it is how
people discover quiet despair.

## Quick start: local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
make validate
uvicorn app.main:app --reload --port 8787
```

Open: <http://127.0.0.1:8787>

## Docker Compose

```bash
make compose-config
make build DOCKER='sudo docker'
make smoke DOCKER='sudo docker'
```

If Docker is unavailable, `make validate` still verifies Python, tests, static checks, and workflow syntax.

## Image targets

- `ghcr.io/mildman1848/audiarr:0.6.1`
- `docker.io/mildman1848/audiarr:0.6.1`

Optional GitLab/Codeberg container registry targets are supported by CI secrets, but should only be enabled after the first GHCR/Docker Hub build is verified.

See [`docs/release-hardening.md`](docs/release-hardening.md) for the full
publishing model, local validation commands, Trivy scan, and Dependabot
hygiene rule.

## Environment

See `.env.example` and `docker-compose.yml`.

Important defaults:

| Setting | Default |
|---|---|
| Port | `8787` |
| Config | `/config/audiarr` |
| Media root | `/data/audiobooks` |
| Audible locale | `us` |
| UI language | `en`, with `de` available |
| Optional translation backend | `none` by default; LibreTranslate-compatible backend optional |
| Version | `0.6.1` |

Versioning follows the Audiarr roadmap, not the household/fork `mldm<N>` suffix:

```text
0.<phase>.<completed item within that phase>
```

Examples: after the second item in Phase 3 is complete, the image version is `0.3.2`; after the first item in Phase 4 is complete, it is `0.4.1`.

## Backups and restore

Audiarr takes automatic (and on-demand) backups of `audiarr.db` and
`settings.json` into a ZIP archive with a manifest, with retention rotation.
Restoring is documented and has been tested against a disposable instance.

See [`docs/backup-restore-runbook.md`](docs/backup-restore-runbook.md) for
what backups contain, restore prerequisites/procedures (Docker Compose and
local/dev), rollback, a verification checklist, and the real drill result.

## Publishing policy

This repository is public-ready, but publishing must happen only after:

1. `make validate` passes.
2. GitHub repository exists and Actions syntax is valid.
3. Registry secrets are configured explicitly.
4. A Docker build/smoke test has passed on a host with a running Docker daemon.

See `docs/publishing.md` and [`docs/release-hardening.md`](docs/release-hardening.md).
