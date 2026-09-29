"""Tests for audiobook release preference scoring (app/release_preferences.py, issue #70)."""

from __future__ import annotations

from app.models.settings import (
    ReleaseBlockedTerm,
    ReleasePreferencesSettings,
    ReleasePreferenceTerm,
)
from app.release_preferences import score_release

# ---------------------------------------------------------------------------
# score_release
# ---------------------------------------------------------------------------


def test_default_preferences_score_unabridged_positively():
    prefs = ReleasePreferencesSettings()

    result = score_release("Der Vorleser Unabridged M4B AAC", prefs)

    assert result.status == "accepted"
    assert result.score == 10
    assert any("unabridged" in r for r in result.reasons)


def test_default_preferences_reject_abridged_releases():
    prefs = ReleasePreferencesSettings()

    result = score_release("Der Vorleser Abridged M4B AAC", prefs)

    assert result.status == "rejected"
    assert any("abridged" in r for r in result.reasons)


def test_default_preferences_reject_dramatized_releases():
    prefs = ReleasePreferencesSettings()

    result = score_release("Some Book Dramatized Adaptation", prefs)

    assert result.status == "rejected"


def test_blocked_term_does_not_match_as_a_substring_of_another_word():
    """"abridged" must not match inside "unabridged" -- both a preferred
    and a blocked default term share this substring, so the whole-word
    boundary is what keeps them from colliding."""
    prefs = ReleasePreferencesSettings()

    result = score_release("Der Vorleser Unabridged", prefs)

    assert result.status == "accepted"


def test_preferred_terms_are_additive():
    prefs = ReleasePreferencesSettings(
        preferred_terms=[
            ReleasePreferenceTerm(term="unabridged", score=10),
            ReleasePreferenceTerm(term="Narrated by Jane Doe", score=5, category="narrator"),
        ],
        blocked_terms=[],
    )

    result = score_release("Book Title Unabridged, Narrated by Jane Doe", prefs)

    assert result.status == "accepted"
    assert result.score == 15
    assert len(result.reasons) == 2


def test_disabled_preferred_term_does_not_score():
    prefs = ReleasePreferencesSettings(
        preferred_terms=[ReleasePreferenceTerm(term="unabridged", score=10, enabled=False)],
        blocked_terms=[],
    )

    result = score_release("Book Title Unabridged", prefs)

    assert result.status == "accepted"
    assert result.score == 0


def test_disabled_blocked_term_does_not_reject():
    prefs = ReleasePreferencesSettings(
        preferred_terms=[],
        blocked_terms=[ReleaseBlockedTerm(term="abridged", enabled=False)],
    )

    result = score_release("Book Title Abridged", prefs)

    assert result.status == "accepted"


def test_minimum_preference_score_rejects_low_scoring_releases():
    prefs = ReleasePreferencesSettings(
        preferred_terms=[ReleasePreferenceTerm(term="unabridged", score=5)],
        blocked_terms=[],
        minimum_preference_score=10,
    )

    result = score_release("Book Title Unabridged", prefs)

    assert result.status == "rejected"
    assert result.score == 5
    assert any("minimum preference score" in r for r in result.reasons)


def test_minimum_preference_score_zero_accepts_no_match():
    prefs = ReleasePreferencesSettings(preferred_terms=[], blocked_terms=[])

    result = score_release("Completely Unrelated Title", prefs)

    assert result.status == "accepted"
    assert result.score == 0


def test_score_release_handles_none_title():
    prefs = ReleasePreferencesSettings()

    result = score_release(None, prefs)

    assert result.status == "accepted"
    assert result.score == 0
