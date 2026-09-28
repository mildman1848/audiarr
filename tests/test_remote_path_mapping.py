"""Remote path mapping resolution tests (issue #69)."""

from __future__ import annotations

from app.models.settings import RemotePathMapping
from app.remote_path_mapping import resolve_remote_path


def test_no_mappings_returns_path_unchanged():
    assert resolve_remote_path("/downloads/complete/Book", []) == "/downloads/complete/Book"


def test_empty_path_returns_unchanged():
    assert resolve_remote_path("", [RemotePathMapping(remote_path="/downloads", local_path="/data")]) == ""


def test_exact_match():
    mappings = [RemotePathMapping(remote_path="/downloads", local_path="/data/usenet")]
    assert resolve_remote_path("/downloads", mappings) == "/data/usenet"


def test_prefix_match_translates_suffix():
    mappings = [RemotePathMapping(remote_path="/downloads", local_path="/data/usenet")]
    assert (
        resolve_remote_path("/downloads/complete/Some Book", mappings)
        == "/data/usenet/complete/Some Book"
    )


def test_longest_prefix_wins():
    """A more specific mapping must win over a broader one that also matches."""
    mappings = [
        RemotePathMapping(remote_path="/downloads", local_path="/data/generic"),
        RemotePathMapping(remote_path="/downloads/complete", local_path="/data/usenet/complete"),
    ]
    assert (
        resolve_remote_path("/downloads/complete/a/b", mappings)
        == "/data/usenet/complete/a/b"
    )


def test_longest_prefix_wins_regardless_of_list_order():
    mappings = [
        RemotePathMapping(remote_path="/downloads/complete", local_path="/data/usenet/complete"),
        RemotePathMapping(remote_path="/downloads", local_path="/data/generic"),
    ]
    assert (
        resolve_remote_path("/downloads/complete/a/b", mappings)
        == "/data/usenet/complete/a/b"
    )


def test_no_matching_prefix_returns_unchanged():
    mappings = [RemotePathMapping(remote_path="/other", local_path="/data")]
    assert resolve_remote_path("/downloads/complete/Book", mappings) == "/downloads/complete/Book"


def test_partial_directory_name_is_not_a_prefix_match():
    """"/downloads2" must not be treated as matching mapping "/downloads"."""
    mappings = [RemotePathMapping(remote_path="/downloads", local_path="/data")]
    assert resolve_remote_path("/downloads2/Book", mappings) == "/downloads2/Book"


def test_disabled_mapping_is_ignored():
    mappings = [
        RemotePathMapping(remote_path="/downloads", local_path="/data", enabled=False),
    ]
    assert resolve_remote_path("/downloads/Book", mappings) == "/downloads/Book"


def test_trailing_separator_on_remote_path_is_normalized():
    mappings = [RemotePathMapping(remote_path="/downloads/", local_path="/data/usenet")]
    assert resolve_remote_path("/downloads/Book", mappings) == "/data/usenet/Book"


def test_windows_style_remote_path():
    mappings = [RemotePathMapping(remote_path="D:\\Downloads", local_path="/data/usenet")]
    assert resolve_remote_path("D:\\Downloads\\Complete\\Book", mappings) == "/data/usenet\\Complete\\Book"
