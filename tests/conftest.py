"""Shared pytest fixtures.

Every test gets an isolated AUDIARR_CONFIG_DIR (a tmp_path) so tests never
read or write the developer's real ./config directory, and never touch
/config (which wouldn't exist/be writable outside the container anyway).
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))

    # Reload modules that read AUDIARR_CONFIG_DIR at import/call time so
    # each test starts with a clean config directory.
    from app import config as config_module

    importlib.reload(config_module)

    from app import main as main_module

    importlib.reload(main_module)

    with TestClient(main_module.app) as client:
        yield client
