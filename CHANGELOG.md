# Changelog

## Unreleased

- Add per-root-folder import strategies (`copy`, `hardlink`, `move`) with free-space checks before copy/move, hardlink fallback to copy on cross-device or unsupported filesystems, and per-file import-method tracking; a real import now places matched media files according to the root folder's strategy while dry-run stays strictly read-only; bump project version to `0.4.2` for Phase 4 item 2 (#30).
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
