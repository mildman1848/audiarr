# Changelog

## Unreleased

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
