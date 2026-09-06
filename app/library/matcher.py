"""Pure matching logic between filesystem candidates and provider hits.

No network calls, no database writes, no API changes: this module only
scores and pairs a :class:`~app.library.scanner.BookCandidate` against a
list of :class:`~app.providers.base.BookQuickInfo` search hits. Callers
(the sync/scan pipeline) are responsible for actually calling providers
and persisting results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.library.scanner import BookCandidate
from app.providers.base import BookQuickInfo

# Leading articles to strip for fuzzy title comparison. English and German
# cover the vast majority of audiobook libraries we expect to scan.
_LEADING_ARTICLES = (
    "the",
    "a",
    "an",
    "der",
    "die",
    "das",
    "ein",
    "eine",
)
_LEADING_ARTICLE_PATTERN = re.compile(
    r"^(?:" + "|".join(_LEADING_ARTICLES) + r")\s+"
)
# Anything that is not a letter, digit, or whitespace is considered noise
# (punctuation, brackets, quotes, etc.).
_NOISE_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")

TITLE_WEIGHT = 0.7
AUTHOR_WEIGHT = 0.3


def normalize_title(s: str) -> str:
    """Lowercase, strip punctuation/brackets noise, and drop a leading article.

    Used to compare titles that differ only by casing, stray punctuation,
    OCR artifacts, or a localized leading article (e.g. "Der Vorleser" vs
    "Vorleser").
    """
    if not s:
        return ""
    lowered = s.lower()
    no_noise = _NOISE_PATTERN.sub(" ", lowered)
    collapsed = _WHITESPACE_PATTERN.sub(" ", no_noise).strip()
    without_article = _LEADING_ARTICLE_PATTERN.sub("", collapsed).strip()
    return without_article


def author_token_set(author: str) -> frozenset[str]:
    """Lowercased, order-insensitive token set for author-name comparison."""
    if not author:
        return frozenset()
    return frozenset(author.lower().split())


def _title_similarity(a: str, b: str) -> float:
    norm_a, norm_b = normalize_title(a), normalize_title(b)
    if not norm_a or not norm_b:
        return 0.0
    return SequenceMatcher(None, norm_a, norm_b).ratio()


def _author_similarity(a: str, b: str) -> float:
    tokens_a, tokens_b = author_token_set(a), author_token_set(b)
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def score_candidate(candidate: BookCandidate, provider_hit: BookQuickInfo) -> float:
    """Score how well ``provider_hit`` matches ``candidate`` in ``[0, 1]``.

    ASIN hints take precedence: an exact (case-insensitive) match is a
    certain match (1.0), while a mismatch means "definitely a different
    book" and short-circuits to 0.0 without falling back to fuzzy scoring.
    Otherwise the score is a weighted blend of normalized-title similarity
    and author-token-set overlap.
    """
    for hint in candidate.asin_hints:
        if provider_hit.asin:
            if hint.strip().lower() == provider_hit.asin.strip().lower():
                return 1.0
            return 0.0

    title_sim = _title_similarity(candidate.guessed_title, provider_hit.title)
    author_sim = _author_similarity(
        candidate.guessed_author, " ".join(provider_hit.authors)
    )
    return TITLE_WEIGHT * title_sim + AUTHOR_WEIGHT * author_sim


@dataclass
class MatchResult:
    """Winning provider hit for a candidate, plus how confident we are."""

    hit: BookQuickInfo
    score: float
    method: str  # "asin" | "fuzzy"


def _matched_via_asin(candidate: BookCandidate, hit: BookQuickInfo) -> bool:
    if not hit.asin:
        return False
    return any(hint.strip().lower() == hit.asin.strip().lower() for hint in candidate.asin_hints)


def match_candidate_to_hits(
    candidate: BookCandidate,
    hits: list[BookQuickInfo],
    threshold: float = 0.75,
) -> MatchResult | None:
    """Pick the best-scoring hit for ``candidate`` above ``threshold``.

    Returns ``None`` if no hit scores above the threshold. Ties keep the
    first (highest-ranked) hit, matching provider search ordering.
    """
    best_hit: BookQuickInfo | None = None
    best_score = -1.0

    for hit in hits:
        score = score_candidate(candidate, hit)
        if score > best_score:
            best_score = score
            best_hit = hit

    if best_hit is None or best_score < threshold:
        return None

    method = "asin" if _matched_via_asin(candidate, best_hit) else "fuzzy"
    return MatchResult(hit=best_hit, score=best_score, method=method)
