#!/usr/bin/env bash
set -euo pipefail
DOCKER="${DOCKER:-docker}"
IMAGE_NAME="${IMAGE_NAME:-local/audiarr}"
IMAGE_TAG="${IMAGE_TAG:-0.1.0-mldm1}"
VERSION="${VERSION:-0.1.0-mldm1}"
APP_VERSION="${APP_VERSION:-0.1.0}"
IMAGE_REVISION="${IMAGE_REVISION:-mldm1}"
PLATFORMS="${PLATFORMS:-linux/amd64}"
PUSH_OR_LOAD="${1:---load}"

if ! ${DOCKER} info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon unavailable for ${DOCKER}. Run validation without Docker or fix daemon access." >&2
  exit 2
fi

${DOCKER} buildx build   --platform "${PLATFORMS}"   --build-arg VERSION="${VERSION}"   --build-arg APP_VERSION="${APP_VERSION}"   --build-arg IMAGE_REVISION="${IMAGE_REVISION}"   --build-arg VCS_REF="$(git rev-parse HEAD 2>/dev/null || echo unknown)"   --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"   -t "${IMAGE_NAME}:${IMAGE_TAG}"   "${PUSH_OR_LOAD}" .
