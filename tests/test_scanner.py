"""Tests for app.library.scanner — folder-name normalization and parsing.

No network/DB involved: these exercise ``_guess_title_author`` (and the
release-name normalization it relies on) directly against real unmatched
release-folder names, plus regression coverage for the previously supported
"Author - Title" / "Title (Author)" / ASIN-suffix patterns.
"""

from __future__ import annotations

from pathlib import Path

from app.library.scanner import (
    _extract_asin_hints,
    _extract_isbn_hints,
    _guess_title_author,
    scan_folder,
)

# ---------------------------------------------------------------------------
# The six live release-name patterns that were left unmatched in production
# (119 unmatched folders, all following one of these six shapes).
# ---------------------------------------------------------------------------


def test_dotted_dash_release_name():
    title, author = _guess_title_author(
        "Marc.Elsberg.-.Helix.-.Sie.werden.uns.ersetzen.1"
    )
    assert author == "Marc Elsberg"
    assert title == "Helix - Sie werden uns ersetzen"


def test_dotted_release_name_with_tag_chain_and_scene_group():
    title, author = _guess_title_author(
        "Marc.Elsberg.EDEN.Wenn.das.Sterben.beginnt.2026.German.WEB.MP3.AUDiOBOOK-TSiNT"
    )
    assert author == "Marc Elsberg"
    assert title == "EDEN Wenn das Sterben beginnt"


def test_duplicate_suffix_and_edition_noise_variant():
    title, author = _guess_title_author("Marc Elsberg - C - Celsius (Ungekurzt)")
    assert author == "Marc Elsberg"
    assert title == "C - Celsius"

    # A second copy of the same release, as sabnzbd names re-downloads.
    title2, author2 = _guess_title_author("Marc Elsberg - C - Celsius (Ungekurzt).1")
    assert (title2, author2) == (title, author)


def test_multi_dash_title_kept_intact():
    title, author = _guess_title_author(
        "Marc Elsberg - Black Hole - Blackout - 10 Jahre danach"
    )
    assert author == "Marc Elsberg"
    assert title == "Black Hole - Blackout - 10 Jahre danach"


def test_tight_hyphen_author_with_full_release_tag_chain():
    title, author = _guess_title_author(
        "Ernest Cline-Armada - Nur du kannst die Erde retten "
        "(Ungekuerzte Lesung)-16BIT-44.1KHZ-WEB-FLAC-2017-WALKMAN"
    )
    assert author == "Ernest Cline"
    assert title == "Armada - Nur du kannst die Erde retten"


def test_space_joined_author_title_with_tag_chain():
    title, author = _guess_title_author(
        "Ernest Cline Armada Nur du kannst die Erde retten-AudioBook-DE-WEB-2018-MP3"
    )
    assert author == "Ernest Cline"
    assert title == "Armada Nur du kannst die Erde retten"


# ---------------------------------------------------------------------------
# Regression: previously supported patterns must keep working.
# ---------------------------------------------------------------------------


def test_author_dash_title_pattern():
    title, author = _guess_title_author("Bernhard Schlink - Der Vorleser")
    assert (title, author) == ("Der Vorleser", "Bernhard Schlink")


def test_title_paren_author_pattern():
    title, author = _guess_title_author("Der Vorleser (Bernhard Schlink)")
    assert (title, author) == ("Der Vorleser", "Bernhard Schlink")


def test_asin_suffix_is_stripped_before_parsing():
    title, author = _guess_title_author("Bernhard Schlink - Der Vorleser [B004UWRY6M]")
    assert (title, author) == ("Der Vorleser", "Bernhard Schlink")


def test_plain_title_with_no_separator_stays_conservative():
    # Short/no-separator names must not have a fake author invented.
    assert _guess_title_author("Neuromancer") == ("Neuromancer", "")


def test_leading_article_does_not_get_mistaken_for_a_name():
    title, author = _guess_title_author("The Hitchhikers Guide To The Galaxy")
    assert author == ""
    assert title == "The Hitchhikers Guide To The Galaxy"


# ---------------------------------------------------------------------------
# ASIN / ISBN extraction must be unaffected by the normalization changes.
# ---------------------------------------------------------------------------


def test_asin_hint_extraction_still_works():
    assert _extract_asin_hints(["Bernhard Schlink - Der Vorleser [B004UWRY6M]"]) == [
        "B004UWRY6M"
    ]


def test_isbn_hint_extraction_still_works():
    assert _extract_isbn_hints(["Some Book (ISBN 978-3-16-148410-0)"]) == [
        "9783161484100"
    ]


# ---------------------------------------------------------------------------
# scan_folder integration: guessed_title/guessed_author land on candidates.
# ---------------------------------------------------------------------------


def test_scan_folder_populates_guesses_for_release_style_name(tmp_path: Path):
    root = tmp_path / "audiobooks"
    book = root / "Marc.Elsberg.-.Helix.-.Sie.werden.uns.ersetzen.1"
    book.mkdir(parents=True)
    (book / "01.mp3").write_bytes(b"\x00" * 1024)

    candidates = scan_folder(root)

    assert len(candidates) == 1
    assert candidates[0].guessed_author == "Marc Elsberg"
    assert candidates[0].guessed_title == "Helix - Sie werden uns ersetzen"
