# Changelog

## 0.1.0-mldm2

- Update FastAPI, Starlette, Pydantic, Uvicorn, and Jinja2 pins for security.
- Remove pip, setuptools, wheel, and system Python build helpers from the runtime image after dependency installation.
- Keep the image LSIO/s6-compatible while reducing runtime scanner noise and attack surface.

## 0.1.0-mldm1

- Initial Audiarr scaffold.
- FastAPI backend, settings API, metadata provider chain, Audiobookshelf and m4b-convertarr clients.
- Minimal Servarr-inspired UI with Audible-orange accent.
- LSIO/s6 Docker image scaffold.
- GitHub Actions for lint/test, Docker build/publish, security scan, and mirror placeholders.
