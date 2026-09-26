# Backup & Restore Runbook

Issue: #34 (Phase 5 item 3). This runbook documents what an Audiarr backup
contains, how to restore one, and records the outcome of a real restore
drill performed against a disposable instance.

## What a backup contains

Each backup is a single ZIP archive (see `app/backup_service.py`) with:

- `audiarr.db` — a consistent SQLite snapshot taken via the sqlite3 backup
  API (safe against a concurrently open, in-use database).
- `settings.json` — the full application settings file.
- `manifest.json` — `created_at`, app `version`, DB `schema_version`, and a
  per-file `size_bytes`/`sha256` entry for `audiarr.db` and `settings.json`.

## What a backup does NOT contain

- Media files under `/data` (audiobooks are not duplicated into backups).
- State held by external services (Audiobookshelf, Prowlarr, SABnzbd,
  m4b-convertarr) — only the connection settings Audiarr stores locally.
- The container image or Python environment.
- Secrets that live outside `settings.json`, environment variables, or
  `FILE__*` secret files (e.g. anything injected purely via Docker secrets
  mounted elsewhere).

## Restore prerequisites

- **Stop the container/app first.** Never restore into a config directory
  a running instance is writing to.
- **Keep a copy of the current config** (`/config/audiarr` in Docker) before
  replacing anything, so the pre-restore state isn't lost if something goes
  wrong.
- **Verify the backup archive** where possible: check it unzips cleanly and
  that `manifest.json`'s recorded `sha256` values match the extracted
  `audiarr.db` and `settings.json` (see verification checklist below).
- **Know the real config path.** `get_config_dir()` (`app/config.py`) appends
  `audiarr/` to the configured base directory. In Docker production this
  means the actual app config lives at `/config/audiarr`, not `/config`
  itself. Locally it is `${AUDIARR_CONFIG_DIR:-./config}/audiarr`.

## Restore procedure — Docker Compose

1. Stop the container:
   ```bash
   docker compose stop audiarr
   ```
2. Copy the current config aside as a rollback point:
   ```bash
   cp -a /config/audiarr /config/audiarr.pre-restore-$(date -u +%Y%m%d-%H%M%S)
   ```
3. Extract `audiarr.db` and `settings.json` from the chosen backup ZIP
   directly into `/config/audiarr`, overwriting the existing files:
   ```bash
   unzip -o /path/to/audiarr-backup-YYYYMMDD-HHMMSS.zip \
     audiarr.db settings.json -d /config/audiarr
   ```
4. Ensure ownership/permissions match what the container expects (the
   LSIO base image runs as `PUID`/`PGID`; fix ownership if the extraction
   ran as a different user):
   ```bash
   chown "${PUID:-1000}:${PGID:-1000}" /config/audiarr/audiarr.db /config/audiarr/settings.json
   ```
5. Start the container:
   ```bash
   docker compose start audiarr
   ```
6. Verify (see checklist below): `/health`, `/api/v1/settings`,
   `/api/v1/library/stats`, and a UI smoke pass.

## Restore procedure — local/dev (`AUDIARR_CONFIG_DIR`)

1. Stop the running dev server.
2. Copy aside the current config directory:
   ```bash
   cp -a "${AUDIARR_CONFIG_DIR:-./config}/audiarr" "${AUDIARR_CONFIG_DIR:-./config}/audiarr.pre-restore"
   ```
3. Extract `audiarr.db` and `settings.json` from the backup ZIP into
   `${AUDIARR_CONFIG_DIR:-./config}/audiarr`.
4. Start the app (`uvicorn app.main:app --reload --port 8787`) with the same
   `AUDIARR_CONFIG_DIR` the backup came from.
5. Verify using the checklist below.

## Rollback plan

If a restore turns out to be wrong (bad archive, wrong point in time):

1. Stop the app/container.
2. Delete the restored `audiarr.db`/`settings.json` and put back the
   pre-restore config copy made in step 2 of the restore procedure.
3. Start the app/container again.

## Verification checklist

After starting the app on restored config, confirm all of the following:

- [ ] `GET /health` returns `{"status": "ok"}`.
- [ ] `GET /api/v1/settings` reflects the expected (restored) settings
      values, not defaults.
- [ ] `GET /api/v1/library/stats` shows the expected book/root-folder/
      author/narrator counts.
- [ ] Spot-check `GET /api/v1/library/books` and
      `GET /api/v1/library/root-folders` for expected entries.
- [ ] UI smoke: log in, load the dashboard and Library page, confirm no
      errors in the browser console or server logs.

## Real restore drill (2026-09-26) — PASS

A real restore drill was performed against a disposable instance to close
out issue #34.

- **Date (UTC):** 2026-09-26
- **Source config:** disposable directory seeded with test data
- **Destination config:** separate disposable directory (fresh instance)
- **Backup archive:** `audiarr-backup-20260926-154345.zip` (4912 bytes)
- **Seed data:** 1 root folder ("Restore Drill Root"); 1 book ("Restore
  Drill Hörbuch", author "Audiarr QA", narrator "Hermes"); settings
  `ui.language=de`, `updates.check_enabled=false`,
  `backup.retention_copies=3`.
- **Method:** extracted `audiarr.db` and `settings.json` from the backup
  ZIP into a fresh `AUDIARR_CONFIG_DIR`'s `audiarr/` directory, then started
  a disposable app instance from that restored config.
- **Verified after restore:**
  - `/health` → `{"status": "ok"}`
  - Settings: `ui.language=de`, updates disabled, `backup.retention_copies=3`
  - `/api/v1/library/stats` → `book_count=1`, `root_folder_count=1`,
    `author_count=1`, `narrator_count=1`
  - `/api/v1/library/books` contained "Restore Drill Hörbuch"
  - `/api/v1/library/root-folders` contained "Restore Drill Root"
- **Result:** **PASS** — restore from a backup ZIP alone reproduces the
  full application state (data + settings).

## Security & privacy notes

- Backup archives include `settings.json`, which can contain connection
  URLs, API keys, and other credentials for integrated services. Treat
  every backup ZIP as sensitive.
- Do not upload backup archives to public locations (issue trackers, public
  repos, public paste/file-sharing tools).
- Restrict filesystem permissions on the backup folder to the container
  user only (backups are written with `0600`/`0700` — see
  `app/backup_service.py`).
