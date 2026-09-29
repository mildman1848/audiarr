"""Audiobook release preference scoring (issue #70).

Adapts Radarr/Sonarr's Custom Formats / Release Profiles concept for
audiobooks: instead of a full condition DSL, a release title is scored by
matching plain terms (see app/models/settings.py::ReleasePreferencesSettings).
This module is independent of app/quality.py (container/codec/bitrate fit)
-- callers combine the two, not this module. Every function here is a pure
function of its inputs -- no I/O, no network, no database access -- so
release search and the Wanted upgrade search can call it synchronously and
test it cheaply. See docs/design/release-preferences.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.settings import ReleasePreferencesSettings

PreferenceStatus = str  # "accepted" | "rejected"


@dataclass
class ReleasePreferenceScore:
    """The result of scoring a release title against release preferences."""

    score: int
    status: PreferenceStatus
    reasons: list[str] = field(default_factory=list)


def _term_matches(title: str, term: str) -> bool:
    """Whole-word, case-insensitive match, e.g. "abridged" does not match
    inside "unabridged" (no word boundary between the shared substring)."""
    return re.search(rf"\b{re.escape(term)}\b", title, re.IGNORECASE) is not None


def score_release(title: str | None, preferences: ReleasePreferencesSettings) -> ReleasePreferenceScore:
    """Score one release title against configured preferred/blocked terms.

    A blocked-term match rejects the release outright, regardless of any
    preferred-term score (mirrors "blocked_terms: release becomes rejected /
    not grabbable if term appears" from issue #70). Otherwise, every
    matching enabled preferred term adds its score and a human-readable
    reason; a total score below ``minimum_preference_score`` also rejects
    (the MVP's "wait for a better release" rule) -- everything else is
    accepted.
    """
    title = title or ""
    reasons: list[str] = []

    for blocked in preferences.blocked_terms:
        if blocked.enabled and blocked.term and _term_matches(title, blocked.term):
            reasons.append(f"blocked term {blocked.term!r} found in release title")
            return ReleasePreferenceScore(score=0, status="rejected", reasons=reasons)

    score = 0
    for preferred in preferences.preferred_terms:
        if preferred.enabled and preferred.term and _term_matches(title, preferred.term):
            score += preferred.score
            reasons.append(f"preferred term {preferred.term!r} matched (+{preferred.score})")

    if score < preferences.minimum_preference_score:
        reasons.append(
            f"score {score} is below the minimum preference score "
            f"{preferences.minimum_preference_score}"
        )
        return ReleasePreferenceScore(score=score, status="rejected", reasons=reasons)

    if not reasons:
        reasons.append("no preferred or blocked terms matched")

    return ReleasePreferenceScore(score=score, status="accepted", reasons=reasons)
