"""Book file organizer: preview/apply safe, opt-in renames per file_name_pattern.

Issue #29 (roadmap Phase 4, item 1): a conservative MVP for moving/renaming a
book's already-imported ``library_files`` into a pattern-driven folder
structure under a configured root folder. This module only ever touches
files already recorded against one book -- it never scans arbitrary paths,
never deletes originals, and never cleans up empty source folders.

Design, in short:

- ``render_pattern`` renders ``file_name_pattern`` (e.g.
  ``"{author}/{series}/{title} ({year})"``) into a list of sanitized,
  non-empty path components. Token substitution happens per rendered
  segment, then the *whole rendered segment* is sanitized (slashes,
  backslashes, NUL, control chars, and other unsafe characters are
  stripped) -- this guarantees a token's value (e.g. an author name) can
  never inject an extra path component, escape via ``..``, or smuggle an
  absolute path.
- The target file keeps the original extension and, for the filename
  itself, the original stem (sanitized) -- this is what avoids collisions
  between multiple files belonging to one book (e.g. ``disc1.m4b`` /
  ``disc2.m4b``) without inventing a new naming scheme.
- ``build_preview`` never touches the filesystem beyond ``exists()``
  checks; ``apply_preview`` is the only function that moves files, and
  only ever after a preview reported every item ``ready`` or ``unchanged``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.library import get_book

log = logging.getLogger("audiarr.library.organizer")

_TOKEN = re.compile(r"\{(\w+)\}")
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")

# Item statuses (see OrganizeItem.status):
#   ready        -- safe to move, target does not exist yet
#   unchanged    -- source already resolves to the rendered target
#   missing      -- source file no longer exists on disk
#   conflict     -- target already exists, or two files render to the same target
#   outside_root -- rendered target escapes the configured root folder
#   error        -- rendering/resolving the target raised an exception
SAFE_STATUSES = ("ready", "unchanged")


def sanitize_component(value: str) -> str:
    """Sanitize a single path component.

    Strips path separators, NUL, and other filesystem-unsafe characters,
    collapses repeated whitespace, and trims. A component that reduces to
    empty, ``"."``, or ``".."`` is rejected (returns ``""``) so callers can
    drop it -- this is what prevents a token's rendered value from
    producing a directory-traversal segment.
    """
    cleaned = _UNSAFE_CHARS.sub("", value)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if cleaned in ("", ".", ".."):
        return ""
    return cleaned


def build_context(book: dict[str, Any], book_id: int, quality: str) -> dict[str, str]:
    """Build the token -> value map for one library file of one book.

    ``book`` is the dict shape returned by ``app.library.get_book`` (comma-
    joined ``authors``/``narrators`` strings, ``series_name``).
    """
    authors = [a for a in (book.get("authors") or "").split(", ") if a]
    narrators = [n for n in (book.get("narrators") or "").split(", ") if n]
    release_date = book.get("release_date") or ""
    series_position = book.get("series_position")
    return {
        "title": book.get("title") or "",
        "author": authors[0] if authors else "Unknown Author",
        "authors": ", ".join(authors),
        "narrator": narrators[0] if narrators else "Unknown Narrator",
        "narrators": ", ".join(narrators),
        "series": book.get("series_name") or "",
        "series_position": "" if series_position in (None, "") else str(series_position),
        "year": release_date[:4] if release_date else "",
        "quality": quality or "",
        "book_id": str(book_id),
    }


def render_pattern(pattern: str, context: dict[str, str]) -> list[str]:
    """Render ``pattern`` into a list of sanitized, non-empty path components.

    Unknown tokens render as an empty string rather than raising, since the
    pattern may be a free-form user setting.
    """
    components: list[str] = []
    for segment in pattern.split("/"):
        rendered = _TOKEN.sub(lambda m: context.get(m.group(1), ""), segment)
        sanitized = sanitize_component(rendered)
        if sanitized:
            components.append(sanitized)
    return components


def render_target_relative_path(pattern: str, context: dict[str, str], source_path: str) -> Path:
    """Render the full relative target path (folder components + filename).

    The filename is always the sanitized original stem plus the original
    extension -- the pattern itself only controls the folder structure.
    """
    components = render_pattern(pattern, context)
    source = Path(source_path)
    stem = sanitize_component(source.stem) or f"file-{context.get('book_id', '0')}"
    filename = f"{stem}{source.suffix}"
    return Path(*components, filename) if components else Path(filename)


@dataclass
class OrganizeItem:
    file_id: int
    source_path: str
    target_path: str
    status: str
    reason: str = ""


@dataclass
class OrganizePreview:
    book_id: int
    root_folder: str
    pattern: str
    safe_to_apply: bool
    items: list[OrganizeItem] = field(default_factory=list)


@dataclass
class OrganizeApplyResult:
    success: bool
    moved: list[OrganizeItem] = field(default_factory=list)
    items: list[OrganizeItem] = field(default_factory=list)
    error: str = ""
    rollback: list[dict[str, Any]] = field(default_factory=list)


def _book_files(conn: sqlite3.Connection, book_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT lf.id, lf.path, lf.format
             FROM library_files lf
             JOIN editions e ON e.id = lf.edition_id
            WHERE e.book_id = ?
            ORDER BY lf.id""",
        (book_id,),
    ).fetchall()


def build_preview(
    conn: sqlite3.Connection, book_id: int, root_path: str, pattern: str
) -> OrganizePreview:
    """Compute (without touching the filesystem beyond existence checks) what
    an apply would do for every one of a book's library files."""
    book = get_book(conn, book_id)
    assert book is not None, f"book {book_id} not found"
    files = _book_files(conn, book_id)
    root = Path(root_path).resolve()

    # First pass: resolve each file's target, filtering out anything that
    # can't be evaluated (error) or is unsafe on its own (outside_root,
    # missing). Everything else becomes a "candidate" pending the
    # cross-file collision check below.
    candidates: list[tuple[sqlite3.Row, Path, Path]] = []
    items: list[OrganizeItem] = []

    for f in files:
        try:
            context = build_context(book, book_id, f["format"])
            relative = render_target_relative_path(pattern, context, f["path"])
            target_resolved = (root / relative).resolve()
        except Exception as exc:  # pragma: no cover -- defensive, no known trigger
            items.append(OrganizeItem(f["id"], f["path"], "", "error", str(exc)))
            continue

        try:
            inside_root = os.path.commonpath([str(root), str(target_resolved)]) == str(root)
        except ValueError:
            inside_root = False  # e.g. different drives on Windows
        if not inside_root:
            items.append(
                OrganizeItem(
                    f["id"],
                    f["path"],
                    str(target_resolved),
                    "outside_root",
                    "Rendered path escapes the configured root folder",
                )
            )
            continue

        source = Path(f["path"])
        if not source.exists():
            items.append(
                OrganizeItem(
                    f["id"],
                    f["path"],
                    str(target_resolved),
                    "missing",
                    "Source file no longer exists on disk",
                )
            )
            continue

        candidates.append((f, source, target_resolved))

    # Second pass: among candidates, resolve unchanged/ready/conflict --
    # collisions (two files rendering to the same target) mark ALL of the
    # colliding items as conflict, not just the later ones.
    target_counts: dict[str, int] = {}
    for _f, source, target_resolved in candidates:
        if source.resolve() == target_resolved:
            continue  # unchanged; doesn't compete for the target
        target_counts[str(target_resolved)] = target_counts.get(str(target_resolved), 0) + 1

    for f, source, target_resolved in candidates:
        source_resolved = source.resolve()
        if source_resolved == target_resolved:
            items.append(OrganizeItem(f["id"], f["path"], str(target_resolved), "unchanged"))
            continue
        if target_counts.get(str(target_resolved), 0) > 1:
            items.append(
                OrganizeItem(
                    f["id"],
                    f["path"],
                    str(target_resolved),
                    "conflict",
                    "Target path collides with another file of this book",
                )
            )
            continue
        if target_resolved.exists():
            items.append(
                OrganizeItem(
                    f["id"], f["path"], str(target_resolved), "conflict", "Target path already exists"
                )
            )
            continue
        items.append(OrganizeItem(f["id"], f["path"], str(target_resolved), "ready"))

    safe = all(item.status in SAFE_STATUSES for item in items)
    return OrganizePreview(
        book_id=book_id, root_folder=str(root), pattern=pattern, safe_to_apply=safe, items=items
    )


def apply_preview(conn: sqlite3.Connection, preview: OrganizePreview) -> OrganizeApplyResult:
    """Move every "ready" item's file and update its DB path.

    Assumes ``preview.safe_to_apply`` -- callers must check that themselves
    (the API route returns 409 without calling this at all). If a move
    fails partway through, every file already moved in this call is
    best-effort moved back to its original location and no DB row is
    touched; the returned result carries per-file rollback outcomes.
    """
    if not preview.safe_to_apply:
        return OrganizeApplyResult(success=False, error="Preview is not safe to apply.")

    to_move = [item for item in preview.items if item.status == "ready"]
    moved: list[OrganizeItem] = []
    try:
        for item in to_move:
            target = Path(item.target_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(item.source_path, str(target))
            moved.append(item)
    except Exception as exc:
        log.error("organize apply failed, rolling back %d moved file(s)", len(moved), exc_info=True)
        rollback: list[dict[str, Any]] = []
        for done in reversed(moved):
            try:
                shutil.move(done.target_path, done.source_path)
                rollback.append({"file_id": done.file_id, "restored": True})
            except Exception as rollback_exc:  # noqa: BLE001 -- report, don't hide
                rollback.append(
                    {"file_id": done.file_id, "restored": False, "error": str(rollback_exc)}
                )
        return OrganizeApplyResult(
            success=False,
            moved=[],
            error=f"Failed to move a file: {exc}",
            rollback=rollback,
        )

    for item in to_move:
        conn.execute("UPDATE library_files SET path = ? WHERE id = ?", (item.target_path, item.file_id))

    return OrganizeApplyResult(success=True, moved=moved, items=preview.items)
