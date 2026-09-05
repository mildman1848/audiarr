#!/usr/bin/env bash
set -euo pipefail
DOCKER="${DOCKER:-docker}"
IMAGE="${IMAGE:-local/audiarr:0.1.0-mldm2}"
NAME="audiarr-smoke"

if ! ${DOCKER} info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon unavailable for ${DOCKER}; cannot run container smoke test." >&2
  exit 2
fi
${DOCKER} image inspect "${IMAGE}" >/dev/null
${DOCKER} rm -f "${NAME}" >/dev/null 2>&1 || true
${DOCKER} run -d --name "${NAME}" -e PUID=1000 -e PGID=1000 -p 127.0.0.1:18787:8787 "${IMAGE}" >/dev/null
trap '${DOCKER} rm -f "${NAME}" >/dev/null 2>&1 || true' EXIT
for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:18787/health >/dev/null; then
    ${DOCKER} exec "${NAME}" sh -lc 'id abc; ps -eo user,args | grep uvicorn | grep -v grep'
    echo 'OK: smoke passed'
    exit 0
  fi
  sleep 1
done
${DOCKER} logs "${NAME}"
exit 1
