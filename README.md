# Audiarr

Servarr-style audiobook manager scaffold with an LSIO/s6 Docker image.

Audiarr is an early MVP for managing audiobook metadata, root folders, quality/profile settings, and outbound integrations around an existing media stack. The current goal is a clean foundation, not fake feature parity.

## MVP scope

- FastAPI backend with `/health`, `/api/v1/system/status`, `/api/v1/settings`, `/api/v1/metadata/search`.
- Servarr-inspired settings shape: host, auth, media management, quality profiles, root folders, download clients, indexers, connect, metadata, UI, logging, updates, backup.
- Metadata provider chain: Audible-first abstraction with `de` marketplace support and Audnexus fallback.
- Audiobookshelf connection scaffold.
- m4b-convertarr connection scaffold for external MP3/M4B conversion.
- Minimal server-rendered web UI with a dark Servarr-like layout and Audible-orange accent.
- LSIO/s6-style Docker image using `/config`, `/data`, `PUID`, `PGID`, `TZ`, `UMASK`, and `FILE__` secrets.

## What is intentionally not done yet

- No real Audible account/device registration yet.
- No real downloader/indexer implementation yet.
- No embedded converter; Audiarr delegates to `m4b-convertarr` first.
- Auth settings are modeled, but form login/API-key enforcement is not implemented yet.

This is deliberate. Shipping a fake Arr clone is easy. Maintaining it is how people discover quiet despair.

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

- `ghcr.io/mildman1848/audiarr:0.1.0-mldm1`
- `docker.io/mildman1848/audiarr:0.1.0-mldm1`

Optional GitLab/Codeberg container registry targets are supported by CI secrets, but should only be enabled after the first GHCR/Docker Hub build is verified.

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
| Version | `0.1.0-mldm1` |

## Publishing policy

This repository is public-ready, but publishing must happen only after:

1. `make validate` passes.
2. GitHub repository exists and Actions syntax is valid.
3. Registry secrets are configured explicitly.
4. A Docker build/smoke test has passed on a host with a running Docker daemon.

See `docs/publishing.md`.
