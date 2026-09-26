# Release hardening and dependency hygiene

Tracks the acceptance criteria in issue #8: keep published images
reproducible, secure, and low-noise.

## Release image publishing model

- `Dockerfile` `ARG VERSION` and `ARG APP_VERSION` are the single source of
  truth for the published image version. `.github/workflows/docker.yml`
  parses these `ARG` defaults directly from the Dockerfile at build time
  (`Resolve version from Dockerfile` step) instead of duplicating the
  version anywhere in CI.
- On `push` to `main` and on manual `workflow_dispatch` with `push=true`,
  the workflow logs into GHCR always, and into Docker Hub (and optionally
  GitLab/Codeberg registries) when the matching secrets are configured.
- Published tags, via `docker/metadata-action`:
  - `<version>` (e.g. `0.5.4`)
  - `sha-<short-sha>`
  - `latest`, only on the default branch
- Verify manifests after a release:

  ```bash
  docker manifest inspect ghcr.io/mildman1848/audiarr:0.5.4
  docker manifest inspect docker.io/mildman1848/audiarr:0.5.4
  ```

  Both must return a valid manifest (multi-arch index) before the release
  is considered published.

## Local validation commands

Run before tagging a release, or any time to sanity-check the working
tree:

```bash
make validate   # lint + pytest + VERSION/APP_VERSION consistency + actionlint if present
make build      # single-platform local Docker build (scripts/buildx-build.sh --load)
make smoke      # boots the built image and checks /health
```

`make validate` does not require Docker; `make build` and `make smoke` do.

### Trivy HIGH/CRITICAL scan

CI runs this on every PR via `.github/workflows/security.yml`
(`trivy-config` job, `severity: HIGH,CRITICAL`, IaC/config scan of the repo).
It is a real gate: `exit-code: '1'`, so any unignored HIGH/CRITICAL finding
fails the workflow. The only findings allowed through are ones explicitly
triaged in `.trivyignore` with a recorded rationale.

To run the same scan locally:

```bash
make security
```

which runs, if `trivy` is installed:

```bash
trivy config --severity HIGH,CRITICAL --exit-code 1 .
```

#### DS-0002 triage (LSIO/s6 base image)

`.trivyignore` triages `DS-0002` ("Specify at least 1 USER command in
Dockerfile with non-root user as argument"). This is intentional scanner
noise, not an unfixed gap:

- The final Dockerfile is LSIO/s6-overlay based
  (`ghcr.io/linuxserver/baseimage-ubuntu`) and must not set `USER abc`.
  `/init` (s6-overlay's PID 1) and the s6-rc service tree under
  `root/etc/s6-overlay/s6-rc.d/` need root to start, and LSIO's own
  PUID/PGID handling needs root too.
- Privilege drop happens where it matters: the long-running Audiarr API.
  `root/usr/local/bin/start-audiarr-api` execs `uvicorn` via
  `s6-setuidgid abc`, so the application process itself never runs as root.
- This is verified, not just asserted: `make smoke`
  (`scripts/smoke.sh`) execs into the running container and checks that
  the `uvicorn` process is owned by `abc`
  (`ps -eo user,args | grep uvicorn`).

Any *new* HIGH/CRITICAL Trivy finding must be fixed, not added to
`.trivyignore`, unless it is genuine scanner noise — and if so, the ignore
entry must record the same kind of rationale and verification path as
`DS-0002` above.

## Dependabot hygiene

List currently open alerts:

```bash
gh api repos/mildman1848/audiarr/dependabot/alerts --paginate \
  --jq '.[] | select(.state=="open")'
```

Rule: zero open alerts, or an explicit triage note (accepted risk, false
positive, or scheduled fix) recorded in the relevant issue/PR.

Status observed at the time issue #8 was closed (2026-09-26): zero open
Dependabot alerts.

## Runtime image noise / build tooling

`Dockerfile` installs `python3-pip`/`python3-venv` only to build the
virtualenv, then:

- uninstalls `pip`, `setuptools`, and `wheel` from `/app/venv` itself, and
- purges the apt-installed `python3-pip`/`python3-venv`/`python3-wheel`
  build tooling from the runtime layer.

Verify after `make build`/`make smoke` that the runtime image carries no
pip/setuptools/wheel:

```bash
docker run --rm local/audiarr:0.5.4 sh -lc \
  'test ! -e /app/venv/bin/pip && ! /app/venv/bin/python3 -m pip --version'
```

The command should exit `0` with no `pip` version printed.

## Risk notes

- Registry publish can lag CI green by roughly a minute; do not run the
  `docker manifest inspect` verification, or declare a release done,
  until GHCR/Docker Hub availability has actually been confirmed.
- Docker Hub (and GitLab/Codeberg) publishing is conditional on secrets
  being configured; a missing secret silently skips that target rather
  than failing the build (see `.github/workflows/docker.yml`).
