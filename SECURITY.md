# Security Policy

## Secrets

Prefer LSIO-style file secrets:

```yaml
environment:
  FILE__AUDIOBOOKSHELF_API_KEY: /run/secrets/audiobookshelf_api_key
  FILE__M4B_CONVERTARR_API_KEY: /run/secrets/m4b_convertarr_api_key
```

Do not commit `.env`, `/config`, generated DBs, or secret files. Logs must never print token values.

## Supported versions

Audiarr is pre-1.0. Only the `main` branch is supported while the project is in scaffold/MVP stage.

## Reporting

Open a GitHub issue without secrets, private hostnames, private domains, or real API keys.
