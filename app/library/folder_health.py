"""Root-folder health probe: free space + writability (issue #31).

Pure filesystem checks, no DB/settings access, so it stays trivially
testable with tmp_path and safe to run from a thread via asyncio.to_thread.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass


@dataclass
class FolderHealth:
    exists: bool
    writable: bool
    free_bytes: int | None
    total_bytes: int | None
    error: str | None = None


def _check_writable(path: str) -> tuple[bool, str | None]:
    probe_name = f".audiarr-health-{os.getpid()}-{uuid.uuid4().hex}"
    probe_path = os.path.join(path, probe_name)
    try:
        with open(probe_path, "x"):
            pass
    except OSError as exc:
        return False, str(exc)
    try:
        os.remove(probe_path)
    except OSError:
        pass
    return True, None


def probe_root_folder(path: str) -> FolderHealth:
    if not os.path.exists(path):
        return FolderHealth(exists=False, writable=False, free_bytes=None, total_bytes=None)

    free_bytes: int | None
    total_bytes: int | None
    error: str | None = None
    try:
        usage = shutil.disk_usage(path)
        free_bytes, total_bytes = usage.free, usage.total
    except OSError as exc:
        free_bytes, total_bytes = None, None
        error = str(exc)

    writable, writable_error = _check_writable(path)
    if writable_error and not error:
        error = writable_error

    return FolderHealth(
        exists=True,
        writable=writable,
        free_bytes=free_bytes,
        total_bytes=total_bytes,
        error=error,
    )
