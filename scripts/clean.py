#!/usr/bin/env python3
"""Remove generated local artifacts."""
from __future__ import annotations

from pathlib import Path
import shutil

for name in [".pytest_cache", ".ruff_cache", "sbom", ".tmp"]:
    path = Path(name)
    if path.exists():
        shutil.rmtree(path)
