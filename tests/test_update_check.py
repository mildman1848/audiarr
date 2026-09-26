"""Version comparison and the display-only update checker (issue #33).

Tests are fully offline: HTTP calls are stubbed with httpx.MockTransport,
never real requests to GitHub.
"""

from __future__ import annotations

import httpx
import pytest

from app import __version__
from app.update_check import check_for_updates, compare_versions


@pytest.fixture()
def isolated_config(tmp_path, monkeypatch):
    """Isolated AUDIARR_CONFIG_DIR so update-check persistence never touches
    the developer's real ./config directory (mirrors test_backup_service.py)."""
    monkeypatch.setenv("AUDIARR_CONFIG_DIR", str(tmp_path))
    from app import config as config_module

    config_module.save_settings(config_module.load_settings())
    return config_module


def _release(tag: str, *, draft: bool = False, prerelease: bool = False) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "tag_name": tag,
            "html_url": f"https://github.com/mildman1848/audiarr/releases/tag/{tag}",
            "name": f"Audiarr {tag}",
            "draft": draft,
            "prerelease": prerelease,
        },
    )


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------- compare_versions


def test_compare_versions_newer():
    assert compare_versions("0.5.1", "0.5.2") == -1


def test_compare_versions_same():
    assert compare_versions("0.5.2", "0.5.2") == 0


def test_compare_versions_older():
    assert compare_versions("0.6.0", "0.5.2") == 1


def test_compare_versions_strips_leading_v():
    assert compare_versions("v0.5.1", "v0.5.2") == -1


def test_compare_versions_handles_different_segment_counts():
    assert compare_versions("1.0", "1.0.1") == -1
    assert compare_versions("1.0.0", "1.0") == 0


def test_compare_versions_unparseable_is_safe():
    assert compare_versions("not-a-version", "0.5.2") == 0
    assert compare_versions("0.5.2", "not-a-version") == 0
    assert compare_versions("", "") == 0


# --------------------------------------------------------------- check_for_updates


@pytest.mark.asyncio
async def test_check_for_updates_newer_marks_available(isolated_config):
    settings = isolated_config.load_settings()
    settings.updates.check_enabled = True
    isolated_config.save_settings(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("user-agent", "").startswith("audiarr-update-check/")
        return _release("v999.0.0")

    result = await check_for_updates(client=_client_for(handler))
    assert result["enabled"] is True
    assert result["current_version"] == __version__
    assert result["latest_version"] == "999.0.0"
    assert result["update_available"] is True
    assert result["error"] == ""

    persisted = isolated_config.load_settings().updates
    assert persisted.latest_version == "999.0.0"
    assert persisted.update_available is True
    assert persisted.last_checked_at
    assert persisted.last_error == ""


@pytest.mark.asyncio
async def test_check_for_updates_same_version_not_available(isolated_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return _release(f"v{__version__}")

    result = await check_for_updates(client=_client_for(handler))
    assert result["update_available"] is False
    assert result["latest_version"] == __version__


@pytest.mark.asyncio
async def test_check_for_updates_older_not_available(isolated_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return _release("v0.0.1")

    result = await check_for_updates(client=_client_for(handler))
    assert result["update_available"] is False
    assert result["latest_version"] == "0.0.1"


@pytest.mark.asyncio
async def test_check_for_updates_unreachable_persists_error(isolated_config):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await check_for_updates(client=_client_for(handler))
    assert result["enabled"] is True
    assert result["update_available"] is False
    assert result["error"]

    persisted = isolated_config.load_settings().updates
    assert persisted.last_error
    assert persisted.last_checked_at


@pytest.mark.asyncio
async def test_check_for_updates_non_200_persists_error(isolated_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    result = await check_for_updates(client=_client_for(handler))
    assert result["error"] == "HTTP 404"


@pytest.mark.asyncio
async def test_check_for_updates_ignores_draft_release(isolated_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return _release("v999.0.0", draft=True)

    result = await check_for_updates(client=_client_for(handler))
    assert result["latest_version"] == ""
    assert result["update_available"] is False
    assert result["error"] == ""


@pytest.mark.asyncio
async def test_check_for_updates_ignores_prerelease(isolated_config):
    def handler(request: httpx.Request) -> httpx.Response:
        return _release("v999.0.0", prerelease=True)

    result = await check_for_updates(client=_client_for(handler))
    assert result["latest_version"] == ""
    assert result["update_available"] is False


@pytest.mark.asyncio
async def test_check_for_updates_disabled_makes_no_request(isolated_config):
    settings = isolated_config.load_settings()
    settings.updates.check_enabled = False
    isolated_config.save_settings(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network request should be made when disabled")

    result = await check_for_updates(client=_client_for(handler))
    assert result["enabled"] is False
    assert result["error"] == ""

    persisted = isolated_config.load_settings().updates
    assert persisted.last_checked_at == ""
