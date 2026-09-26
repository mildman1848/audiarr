"""Automatic config backup service (issue #32, phase 5).

Each backup is a single zip archive containing a consistent DB snapshot, a
copy of settings.json, and a manifest with per-file sha256 (for future
restore-integrity checks; restore itself is a later slice). The DB is
snapshotted via the sqlite3 backup API rather than a raw file copy: copying
a live SQLite file byte-for-byte can capture a half-written page mid-write
and produce a corrupt copy, whereas ``Connection.backup()`` is safe against
a concurrently-open, in-use database.

No FastAPI imports here -- this module is plain sync code so it can run in
a background thread (see app/api/routes_system.py, app/backup_scheduler.py)
without touching the event loop, and so it can be unit tested without an
app instance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import __version__
from app.config import get_db_path, get_settings_path, load_settings

try:
    from app.db import SCHEMA_VERSION
except ImportError:  # pragma: no cover - app.db always ships alongside this module
    SCHEMA_VERSION = None

log = logging.getLogger("audiarr.backup_service")

BACKUP_GLOB = "audiarr-backup-*.zip"
_FILENAME_FORMAT = "audiarr-backup-%Y%m%d-%H%M%S.zip"


class BackupError(RuntimeError):
    """Raised when a backup could not be created."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_db(db_path: Path, dest_path: Path) -> None:
    """Consistent copy of a live SQLite DB via the sqlite3 backup API."""
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def create_backup(reason: str = "scheduled") -> dict:
    """Create one zip backup of the DB + settings.json, then prune old ones.

    Raises BackupError on any failure (folder not writable, zip write
    fails, ...); a partially-written zip is removed before re-raising.
    """
    settings = load_settings()
    backup_dir = Path(settings.backup.folder)
    created_at = datetime.now(UTC)
    backup_path = backup_dir / created_at.strftime(_FILENAME_FORMAT)

    # Manual backups can be triggered twice inside the same second. Keep the
    # documented timestamp-only filename shape, but never overwrite an
    # existing archive; advance to the next free second instead.
    while backup_path.exists():
        created_at += timedelta(seconds=1)
        backup_path = backup_dir / created_at.strftime(_FILENAME_FORMAT)

    try:
        if not backup_dir.exists():
            backup_dir.mkdir(parents=True)
            os.chmod(backup_dir, 0o700)

        db_path = get_db_path()
        settings_path = get_settings_path()

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_db = Path(tmp_dir) / "audiarr.db"
            _snapshot_db(db_path, tmp_db)

            sources = [
                ("audiarr.db", tmp_db),
                ("settings.json", settings_path),
            ]
            file_entries = [
                {
                    "name": name,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
                for name, path in sources
            ]
            manifest = {
                "created_at": created_at.isoformat(),
                "version": __version__,
                "schema_version": SCHEMA_VERSION,
                "files": file_entries,
            }

            with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, path in sources:
                    zf.write(path, arcname=name)
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        os.chmod(backup_path, 0o600)
    except Exception as exc:  # noqa: BLE001 -- any failure becomes a BackupError
        if backup_path.exists():
            backup_path.unlink(missing_ok=True)
        raise BackupError(f"Failed to create backup: {exc}") from exc

    pruned = _prune_backups(backup_dir, settings.backup.retention_copies)
    size_bytes = backup_path.stat().st_size
    log.info(
        "backup created: %s (%d bytes, reason=%s, pruned=%d)",
        backup_path,
        size_bytes,
        reason,
        len(pruned),
    )

    return {
        "path": str(backup_path),
        "size_bytes": size_bytes,
        "files": manifest["files"],
        "created_at": manifest["created_at"],
        "reason": reason,
        "pruned": pruned,
    }


def _prune_backups(backup_dir: Path, keep: int) -> list[str]:
    """Delete the oldest backups beyond ``keep``. ``keep <= 0`` disables pruning."""
    if keep <= 0:
        return []
    # Zero-padded timestamp in the filename sorts lexicographically == chronologically.
    candidates = sorted(backup_dir.glob(BACKUP_GLOB))
    excess = len(candidates) - keep
    if excess <= 0:
        return []

    pruned: list[str] = []
    for path in candidates[:excess]:
        path.unlink(missing_ok=True)
        pruned.append(str(path))
        log.info("backup pruned: %s", path)
    return pruned


def list_backups() -> list[dict]:
    """List existing backups in the configured folder, newest first."""
    settings = load_settings()
    backup_dir = Path(settings.backup.folder)
    if not backup_dir.exists():
        return []

    results = []
    for path in backup_dir.glob(BACKUP_GLOB):
        try:
            created_at = datetime.strptime(
                path.stem, "audiarr-backup-%Y%m%d-%H%M%S"
            ).replace(tzinfo=UTC)
        except ValueError:
            log.debug("backup list: skipping unparseable filename %s", path.name)
            continue
        results.append(
            {
                "path": str(path),
                "name": path.name,
                "size_bytes": path.stat().st_size,
                "created_at": created_at.isoformat(),
            }
        )
    results.sort(key=lambda entry: entry["created_at"], reverse=True)
    return results
