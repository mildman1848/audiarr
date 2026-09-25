"""File placement for import strategies: move / copy / hardlink (#30).

Each root folder configures how the import pipeline gets a matched
candidate's files onto disk under that root:

  - ``copy``:     safest default. Preserves the source untouched (keeps a
                   torrent/usenet download seedable) at the cost of double
                   disk usage during import.
  - ``move``:      no extra space needed, but the source is gone afterwards
                   -- do not point a root folder using this strategy at a
                   download client's incomplete/still-seeding directory.
  - ``hardlink``:  zero extra space and instant, but only works when the
                   source and destination live on the same filesystem/
                   volume. Falls back to ``copy`` (logged) whenever the OS
                   refuses the link (cross-device, unsupported filesystem,
                   or no permission) so an import never fails outright just
                   because hardlinking wasn't possible.

See docs/design/import-strategies.md for the full trade-off writeup.

This module only ever places files under the resolved target folder inside
the configured root folder's path (containment is enforced -- see
``resolve_target_path``); it never deletes originals for ``copy``/
``hardlink``, and ``move`` uses ``shutil.move`` (no separate delete step).
Every candidate is placed atomically from the caller's point of view: if
any one file fails, every file already placed for that candidate in this
call is rolled back and the failure is raised, so callers never persist a
candidate with a partially-applied strategy.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("audiarr.library.import_strategy")

STRATEGIES = ("move", "copy", "hardlink")
DEFAULT_STRATEGY = "copy"


class ImportStrategyError(RuntimeError):
    """Raised when a strategy operation cannot complete safely.

    Callers must treat this as "the candidate was not imported" -- no book
    or library_files rows may be persisted for it.
    """


class InsufficientSpaceError(ImportStrategyError):
    """Raised by check_free_space when the destination lacks room."""


class PathTraversalError(ImportStrategyError):
    """Raised when a resolved target would escape the configured root folder."""


@dataclass
class PlacedFile:
    """One file after a strategy ran (or was a no-op because it was already
    in place)."""

    source_path: str
    final_path: str
    size_bytes: int
    format: str
    strategy_used: str  # "copy" | "move" | "hardlink" | "unchanged"


def resolve_target_path(root_path: str, candidate_folder_name: str, file_name: str) -> Path:
    """Resolve the final on-disk path for one file inside ``root_path``.

    Files land under ``root_path/candidate_folder_name/file_name`` -- the
    candidate's own folder name is preserved so a book's files stay
    grouped, but deeper source nesting is intentionally flattened (issue
    #29's organizer owns pretty per-file_name_pattern layout; this issue
    only needs a safe, predictable landing spot). Guards against path
    traversal: the resolved path must stay inside ``root_path``.
    """
    root = Path(root_path).resolve()
    target = (root / candidate_folder_name / file_name).resolve()
    try:
        inside_root = os.path.commonpath([str(root), str(target)]) == str(root)
    except ValueError:
        inside_root = False  # e.g. different drives on Windows
    if not inside_root:
        raise PathTraversalError(f"resolved target {target} escapes root folder {root}")
    return target


def check_free_space(destination_dir: Path, required_bytes: int) -> None:
    """Raise InsufficientSpaceError unless ``destination_dir``'s filesystem
    has ``required_bytes`` free.

    Walks up to the nearest existing ancestor since the target directory
    itself may not exist yet (``shutil.disk_usage`` requires a real path).
    """
    probe = destination_dir
    while not probe.exists():
        parent = probe.parent
        if parent == probe:  # reached filesystem root without finding one
            break
        probe = parent
    usage = shutil.disk_usage(probe)
    if usage.free < required_bytes:
        raise InsufficientSpaceError(
            f"insufficient free space at {probe}: need {required_bytes} bytes, "
            f"have {usage.free} bytes"
        )


def _do_hardlink(source_path: str, target_path: Path, size_bytes: int) -> str:
    """Try os.link; fall back to copy2 (logged) when the OS refuses it.

    Covers cross-device links (EXDEV), unsupported filesystems (ENOTSUP),
    permission refusals (EPERM), and any other OSError a platform might
    raise for hardlinking -- the fallback is intentionally broad per #30's
    acceptance criteria ("EXDEV, ENOTSUP, EPERM, or platform unsupported").
    """
    try:
        os.link(source_path, target_path)
        return "hardlink"
    except (OSError, NotImplementedError) as exc:
        log.warning(
            "hardlink %s -> %s failed (%s: %s); falling back to copy",
            source_path, target_path, type(exc).__name__, exc,
        )
        # The fallback is a real copy, so it must obey the same free-space
        # guard as the explicit copy strategy. Hardlinks normally need no
        # extra data blocks, but an EXDEV/unsupported-filesystem fallback does.
        check_free_space(target_path.parent, size_bytes)
        shutil.copy2(source_path, target_path)
        return "copy"


def place_file(source_path: str, target_path: Path, strategy: str, size_bytes: int) -> str:
    """Execute one strategy for one file; returns the strategy actually used
    (differs from ``strategy`` only for a hardlink fallback)."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown import strategy {strategy!r}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if strategy == "copy":
        shutil.copy2(source_path, target_path)
        return "copy"
    if strategy == "move":
        shutil.move(source_path, str(target_path))
        return "move"
    return _do_hardlink(source_path, target_path, size_bytes)


def place_candidate_files(
    files: list[tuple[str, str, int, str]],
    root_path: str,
    candidate_folder_name: str,
    strategy: str,
) -> list[PlacedFile]:
    """Resolve targets, sanity-check free space, and place every file of one
    candidate.

    ``files`` is a list of ``(source_path, file_name, size_bytes, format)``
    tuples -- plain data, not scanner types, so this module stays
    independently testable. All-or-nothing per candidate: if any file's
    placement fails partway through, every file already placed in this call
    is rolled back (a copy/hardlink target is deleted; a move is moved back)
    and an ``ImportStrategyError`` is raised, so the caller never persists a
    candidate with a partially-applied strategy.
    """
    resolved: list[tuple[str, Path, int, str]] = []
    for source_path, file_name, size_bytes, fmt in files:
        target = resolve_target_path(root_path, candidate_folder_name, file_name)
        resolved.append((source_path, target, size_bytes, fmt))

    # Free-space sanity check is required before copy/move (#30 acceptance
    # criteria). Hardlink needs no extra space in the common case; if it
    # falls back to copy, _do_hardlink performs the copy guard immediately
    # before the fallback write.
    to_transfer = [r for r in resolved if Path(r[0]).resolve() != r[1]]
    if strategy in ("copy", "move") and to_transfer:
        total_bytes = sum(r[2] for r in to_transfer)
        check_free_space(Path(root_path).resolve(), total_bytes)

    placed: list[PlacedFile] = []
    done: list[tuple[str, str, Path]] = []  # (strategy_used, source_path, target)
    try:
        for source_path, target, size_bytes, fmt in resolved:
            if Path(source_path).resolve() == target:
                placed.append(PlacedFile(source_path, str(target), size_bytes, fmt, "unchanged"))
                continue
            if target.exists():
                raise ImportStrategyError(f"target already exists: {target}")
            used = place_file(source_path, target, strategy, size_bytes)
            done.append((used, source_path, target))
            placed.append(PlacedFile(source_path, str(target), size_bytes, fmt, used))
    except Exception as exc:  # noqa: BLE001 -- rollback then re-raise as ImportStrategyError
        log.error(
            "import strategy %r failed for candidate folder %r; rolling back %d placed file(s)",
            strategy, candidate_folder_name, len(done), exc_info=True,
        )
        for used, source_path, target in reversed(done):
            try:
                if used == "move":
                    shutil.move(str(target), source_path)
                else:  # copy or hardlink -> undo by removing the created target
                    target.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 -- best-effort rollback, report don't hide
                log.error("rollback failed for %s", target, exc_info=True)
        if isinstance(exc, ImportStrategyError):
            raise
        raise ImportStrategyError(f"failed to place file: {exc}") from exc

    return placed
