#!/usr/bin/env python3
"""Render Makefile help without awk/sed dependencies."""
from __future__ import annotations
import re, sys
print("Usage: make <target> [VAR=value]\n\nTargets:")
for fn in sys.argv[1:]:
    for line in open(fn, encoding="utf-8"):
        m = re.match(r"^([a-zA-Z0-9_.-]+):.*##\s*(.*)$", line)
        if m:
            print(f"  {m.group(1):22s} {m.group(2)}")
