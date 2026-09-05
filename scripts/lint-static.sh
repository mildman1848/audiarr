#!/usr/bin/env bash
set -euo pipefail
fail=0

python3 -m compileall -q app tests || fail=1
python3 -m ruff check app tests || fail=1

while IFS= read -r -d '' file; do
  case "$file" in
    */dependencies.d/*|*/contents.d/*) continue ;;
  esac
  if [[ ! -s "$file" ]]; then
    echo "ERROR: empty file: $file" >&2
    fail=1
  fi
done < <(find . -type f   ! -path './.git/*' ! -path './.venv/*' ! -path './__pycache__/*'   ! -path './config/*' ! -path './data/*' ! -path './logs/*' ! -path './secrets/*'   -print0)

bash -n root/usr/local/bin/start-audiarr-api   root/etc/s6-overlay/s6-rc.d/init-audiarr/run   root/etc/s6-overlay/s6-rc.d/audiarr-api/run   scripts/buildx-build.sh scripts/smoke.sh

if command -v hadolint >/dev/null 2>&1; then
  hadolint -c .hadolint.yaml Dockerfile || fail=1
else
  echo 'WARN: hadolint missing; skipped' >&2
fi

exit "$fail"
