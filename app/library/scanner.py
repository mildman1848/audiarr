"""Filesystem scanner for audiobook root folders.

Walks a root folder, groups audio files into book candidates (one folder
= one candidate), and extracts basic metadata: audio file list, total
size, dominant format, and ASIN hints found in folder/file names.

Deliberately conservative: the scanner never writes anything and never
touches the network — matching is a separate step.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("audiarr.library.scanner")

AUDIO_EXTENSIONS = {".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".wma"}
METADATA_EXTENSIONS = {".json", ".nfo", ".xml", ".txt"}

# Audible ASIN pattern: 10 chars starting with B0 (older ones differ, but
# B0 covers the vast majority of audiobooks; numeric ASINs are ambiguous).
ASIN_PATTERN = re.compile(r"\b(B0[A-Z0-9]{8})\b", re.IGNORECASE)
ISBN_PATTERN = re.compile(r"\b(97[89][- ]?\d{1,5}[- ]?\d{1,7}[- ]?\d{1,7}[- ]?\d)\b")


@dataclass
class ScannedFile:
    path: str
    size_bytes: int
    format: str


@dataclass
class BookCandidate:
    """One folder (or loose-file group) that looks like a single audiobook."""

    folder_path: str
    folder_name: str
    files: list[ScannedFile] = field(default_factory=list)
    total_size_bytes: int = 0
    dominant_format: str = ""
    asin_hints: list[str] = field(default_factory=list)
    isbn_hints: list[str] = field(default_factory=list)
    # Guesses parsed from the folder name, e.g. "Author - Title" or
    # "Title (Author)" — used as fallback matching hints, never as truth.
    guessed_title: str = ""
    guessed_author: str = ""


def _extract_asin_hints(candidate_names: list[str]) -> list[str]:
    hints: list[str] = []
    for name in candidate_names:
        for match in ASIN_PATTERN.finditer(name):
            asin = match.group(1).upper()
            if asin not in hints:
                hints.append(asin)
    return hints


def _extract_isbn_hints(candidate_names: list[str]) -> list[str]:
    hints: list[str] = []
    for name in candidate_names:
        for match in ISBN_PATTERN.finditer(name):
            isbn = re.sub(r"[- ]", "", match.group(1))
            if isbn not in hints:
                hints.append(isbn)
    return hints


def _guess_title_author(folder_name: str) -> tuple[str, str]:
    """Best-effort parse of common folder naming schemes.

    Supported patterns (checked in order):
      "Author - Title"       -> (title, author)
      "Title (Author)"       -> (title, author)
      anything else          -> (folder_name, "")
    """
    # Strip trailing ASIN/bracket noise like "[B0XXXXXXXX]" first.
    cleaned = re.sub(r"\s*\[?[Bb]0[A-Za-z0-9]{8}\]?\s*$", "", folder_name).strip()

    if " - " in cleaned:
        left, _, right = cleaned.partition(" - ")
        # "Author - Title" is the dominant convention in audiobook libraries.
        return right.strip(), left.strip()

    paren = re.search(r"^(.*?)\s*\(([^)]+)\)$", cleaned)
    if paren:
        return paren.group(1).strip(), paren.group(2).strip()

    return cleaned, ""


def scan_folder(root: Path) -> list[BookCandidate]:
    """Walk ``root`` and return one BookCandidate per folder containing audio.

    Top-level loose audio files (no subfolder) are grouped as one candidate
    per root itself — pragmatic for single-book folders.
    """
    if not root.is_dir():
        log.warning("scan_folder: %s is not a directory", root)
        return []

    candidates: list[BookCandidate] = []
    log.info("Scanning root folder %s", root)

    # Collect folders containing audio files (root itself first).
    folders = [root]
    for dirpath, dirnames, filenames in _walk_sorted(root):
        folder = Path(dirpath)
        if folder == root:
            continue
        # Only consider folders that directly contain audio files.
        if any(Path(f).suffix.lower() in AUDIO_EXTENSIONS for f in filenames):
            folders.append(folder)
        # Do not descend into nested book folders; one folder = one book.
        dirnames[:] = [d for d in dirnames if not _looks_like_book_folder(folder / d)]

    for folder in folders:
        audio_files = sorted(
            f
            for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        )
        if not audio_files:
            continue

        scanned_files = [
            ScannedFile(
                path=str(f.relative_to(root)),
                size_bytes=f.stat().st_size,
                format=f.suffix.lower().lstrip("."),
            )
            for f in audio_files
        ]
        total_size = sum(sf.size_bytes for sf in scanned_files)
        formats = [sf.format for sf in scanned_files]
        dominant = max(set(formats), key=formats.count)

        names = [folder.name] + [f.name for f in audio_files]
        title, author = _guess_title_author(folder.name)

        candidates.append(
            BookCandidate(
                folder_path=str(folder),
                folder_name=folder.name,
                files=scanned_files,
                total_size_bytes=total_size,
                dominant_format=dominant,
                asin_hints=_extract_asin_hints(names),
                isbn_hints=_extract_isbn_hints(names),
                guessed_title=title,
                guessed_author=author,
            )
        )
        log.debug(
            "candidate %r: %d file(s), %s, asin_hints=%s",
            folder.name,
            len(scanned_files),
            dominant,
            _extract_asin_hints(names),
        )

    log.info("Scan of %s found %d candidate(s)", root, len(candidates))
    return candidates


def _walk_sorted(root: Path):
    """os.walk with sorted entries for deterministic scans."""
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        yield dirpath, dirnames, filenames


def _looks_like_book_folder(folder: Path) -> bool:
    """Heuristic: a folder directly containing audio files is a book folder."""
    try:
        return any(
            f.suffix.lower() in AUDIO_EXTENSIONS for f in folder.iterdir() if f.is_file()
        )
    except OSError:
        return False
