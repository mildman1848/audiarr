"""Remote path mapping resolution (issue #69).

Translates a download client's filesystem path into Audiarr's own view of
that filesystem, for Docker/NAS setups where SABnzbd and Audiarr mount the
same physical storage at different container paths. Mirrors Radarr/Sonarr's
"Remote Path Mappings" settings concept. See
``app.models.settings.RemotePathMapping`` for the persisted shape and
``app.sab_auto_import`` for where this is applied before any filesystem
check.
"""

from __future__ import annotations

from app.models.settings import RemotePathMapping


def resolve_remote_path(path: str, mappings: list[RemotePathMapping]) -> str:
    """Translate ``path`` using the longest matching enabled mapping.

    Matching is a plain prefix comparison against each enabled mapping's
    ``remote_path`` (trailing ``/`` or ``\\`` stripped so a stored mapping
    with or without one behaves the same); when more than one mapping
    matches, the longest ``remote_path`` wins so a more specific mapping
    (e.g. ``/downloads/complete``) takes precedence over a broader one
    (e.g. ``/downloads``). Only a single trailing separator is stripped
    from ``remote_path`` for the comparison -- the rest of ``path`` (and
    any Windows-style backslashes in it) is passed through untouched.
    Returns ``path`` unchanged if nothing matches. Never raises.
    """
    if not path:
        return path

    best: RemotePathMapping | None = None
    best_len = -1
    for mapping in mappings:
        if not mapping.enabled or not mapping.remote_path or not mapping.local_path:
            continue
        remote = mapping.remote_path.rstrip("/\\")
        if not remote:
            continue
        if (
            path == remote
            or path.startswith(remote + "/")
            or path.startswith(remote + "\\")
        ) and len(remote) > best_len:
            best = mapping
            best_len = len(remote)

    if best is None:
        return path

    remote = best.remote_path.rstrip("/\\")
    local = best.local_path.rstrip("/\\")
    return local + path[len(remote):]
