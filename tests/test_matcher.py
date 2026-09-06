"""Tests for app.library.matcher — pure matching logic, no network/DB."""

from __future__ import annotations

from app.library.matcher import (
    MatchResult,
    author_token_set,
    match_candidate_to_hits,
    normalize_title,
    score_candidate,
)
from app.library.scanner import BookCandidate
from app.providers.base import BookQuickInfo


def _candidate(
    *,
    guessed_title: str = "",
    guessed_author: str = "",
    asin_hints: list[str] | None = None,
) -> BookCandidate:
    return BookCandidate(
        folder_path="/library/Some Folder",
        folder_name="Some Folder",
        guessed_title=guessed_title,
        guessed_author=guessed_author,
        asin_hints=asin_hints or [],
    )


def _hit(
    *,
    title: str = "",
    authors: list[str] | None = None,
    asin: str | None = None,
) -> BookQuickInfo:
    return BookQuickInfo(
        provider_uid="prov:1",
        provider_name="audible",
        title=title,
        authors=authors or [],
        asin=asin,
    )


def test_normalize_title_strips_articles_and_punctuation():
    assert normalize_title("The Hobbit!") == "hobbit"
    assert normalize_title("Der Vorleser.") == "vorleser"
    assert normalize_title("  A   Study In Scarlet  ") == "study in scarlet"
    assert normalize_title("") == ""


def test_author_token_set_is_order_insensitive():
    assert author_token_set("Bernhard Schlink") == author_token_set("Schlink Bernhard")
    assert author_token_set("") == frozenset()


def test_ocr_typo_title_scores_above_threshold():
    candidate = _candidate(guessed_title="Der Vorleser", guessed_author="Bernhard Schlink")
    hit = _hit(title="Der Vorleser.", authors=["Bernhard Schlink"])
    score = score_candidate(candidate, hit)
    assert score >= 0.75


def test_asin_exact_match_wins_with_score_one():
    candidate = _candidate(
        guessed_title="Totally Different Title",
        guessed_author="Nobody",
        asin_hints=["B0ABCDEFGH"],
    )
    hit = _hit(title="Der Vorleser", authors=["Bernhard Schlink"], asin="b0abcdefgh")
    assert score_candidate(candidate, hit) == 1.0


def test_asin_mismatch_returns_zero_even_with_similar_title():
    candidate = _candidate(
        guessed_title="Der Vorleser",
        guessed_author="Bernhard Schlink",
        asin_hints=["B0ABCDEFGH"],
    )
    hit = _hit(title="Der Vorleser", authors=["Bernhard Schlink"], asin="B0ZZZZZZZZ")
    assert score_candidate(candidate, hit) == 0.0


def test_author_token_overlap_lifts_fuzzy_score():
    candidate = _candidate(guessed_title="Some Vague Title", guessed_author="Bernhard Schlink")
    hit_same_author = _hit(title="Some Vague Title Extended", authors=["Bernhard Schlink"])
    hit_diff_author = _hit(title="Some Vague Title Extended", authors=["Someone Else"])
    assert score_candidate(candidate, hit_same_author) > score_candidate(
        candidate, hit_diff_author
    )


def test_match_candidate_to_hits_best_above_threshold_wins():
    candidate = _candidate(guessed_title="Der Vorleser", guessed_author="Bernhard Schlink")
    weak_hit = _hit(title="Completely Unrelated Book", authors=["Someone Else"])
    strong_hit = _hit(title="Der Vorleser", authors=["Bernhard Schlink"])
    result = match_candidate_to_hits(candidate, [weak_hit, strong_hit])
    assert isinstance(result, MatchResult)
    assert result.hit is strong_hit
    assert result.method == "fuzzy"
    assert result.score >= 0.75


def test_match_candidate_to_hits_asin_method_reported():
    candidate = _candidate(
        guessed_title="Whatever",
        guessed_author="Whoever",
        asin_hints=["B0ABCDEFGH"],
    )
    hit = _hit(title="Der Vorleser", authors=["Bernhard Schlink"], asin="B0ABCDEFGH")
    result = match_candidate_to_hits(candidate, [hit])
    assert result is not None
    assert result.method == "asin"
    assert result.score == 1.0


def test_match_candidate_to_hits_below_threshold_returns_none():
    candidate = _candidate(guessed_title="Der Vorleser", guessed_author="Bernhard Schlink")
    hit = _hit(title="Completely Unrelated Book", authors=["Someone Else"])
    assert match_candidate_to_hits(candidate, [hit]) is None


def test_match_candidate_to_hits_empty_hits_returns_none():
    candidate = _candidate(guessed_title="Der Vorleser", guessed_author="Bernhard Schlink")
    assert match_candidate_to_hits(candidate, []) is None


def test_robust_against_empty_candidate_and_hit_fields():
    candidate = _candidate()
    hit = _hit()
    # Should never raise, and an all-empty match should not qualify.
    assert score_candidate(candidate, hit) == 0.0
    assert match_candidate_to_hits(candidate, [hit]) is None
