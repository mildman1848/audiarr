# Publishing

Target source repositories:

- GitHub: `mildman1848/audiarr`
- Codeberg: `mildman1848/audiarr`
- GitLab: `mildman1848/audiarr`

Target images:

- GHCR: `ghcr.io/mildman1848/audiarr`
- Docker Hub: `docker.io/mildman1848/audiarr`
- Optional GitLab registry via `GITLAB_REGISTRY_IMAGE`
- Optional Codeberg registry via `CODEBERG_REGISTRY_IMAGE`

Required GitHub secrets for optional publishing/mirroring:

| Secret | Purpose |
|---|---|
| `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` | Docker Hub publish |
| `GITLAB_REGISTRY_IMAGE` / `GITLAB_REGISTRY_USER` / `GITLAB_REGISTRY_TOKEN` | GitLab registry publish |
| `CODEBERG_REGISTRY_IMAGE` / `CODEBERG_REGISTRY_USER` / `CODEBERG_REGISTRY_TOKEN` | Codeberg registry publish |
| `CODEBERG_SSH_KEY` | Git mirror to Codeberg |
| `GITLAB_SSH_KEY` | Git mirror to GitLab |

First release should be manual `workflow_dispatch` with `push=true` after local Docker smoke succeeds.
