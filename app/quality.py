"""Deterministic audiobook quality inference and profile-fit evaluation.

This module is intentionally independent of any video quality logic
(Radarr/Sonarr): audiobook "quality" is container/codec/bitrate-band/
chapter-presence, not resolution/source (see
docs/design/quality-profiles.md). Every function here is a pure function
of its inputs -- no I/O, no network, no database access -- so release
search and the importer can call it synchronously and test it cheaply.

Two entry points matter to callers:

- ``infer_quality_from_name`` reads audiobook-ish signals out of a
  release/folder/file name (container, codec, bitrate, chapter hints).
- ``evaluate_quality_for_profile`` matches that inference against a
  QualityProfile's ordered quality tiers and reports a stable
  preferred/accepted/below_cutoff/rejected/unknown verdict.
- ``should_convert_candidate`` answers the one importer-facing question:
  should this already-imported candidate be offered to the conversion
  backend, given the (first configured) quality profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.settings import QualityDefinition, QualityProfile, Settings

# Containers/codecs this slice recognizes from a name. mp3/flac double as
# both a container and a codec token; m4b/m4a imply an AAC codec unless the
# name says otherwise.
_CONTAINER_TOKENS = ("m4b", "m4a", "flac", "mp3")
_CODEC_TOKENS = ("aac", "mp3", "flac")
_DEFAULT_CODEC_FOR_CONTAINER = {"m4b": "aac", "m4a": "aac", "mp3": "mp3", "flac": "flac"}

# "64kbps", "128 kbps", "320k", "96kb/s" -- but NOT "44.1kHz" (sample rate)
# or "16bit"/"24bit", which never have a "k"/"kbps" suffix on 2-4 digits.
_BITRATE_RE = re.compile(r"\b(\d{2,4})\s*-?\s*k(?:bps|b/s)?\b", re.IGNORECASE)

# "chapter"/"chapters"/"chaptered" or a "cue" sheet -- both signal that the
# release carries chapter navigation.
_CHAPTER_RE = re.compile(r"\b(chapters?|chaptered|cue)\b", re.IGNORECASE)

CONVERTIBLE_SOURCE_FORMATS = ("mp3", "m4a")

QualityStatus = str  # "preferred" | "accepted" | "below_cutoff" | "rejected" | "unknown"


@dataclass
class InferredQuality:
    """What could be determined about a release's quality from its name."""

    container: str | None = None
    codec: str | None = None
    bitrate_kbps: int | None = None
    chapters: bool | None = None  # None = no signal either way


@dataclass
class QualityFit:
    """The result of matching an InferredQuality against a quality profile."""

    inferred: InferredQuality
    matched_quality_id: str | None
    status: QualityStatus
    reason: str


def infer_quality_from_name(name: str, settings: Settings | None = None) -> InferredQuality:
    """Extract container/codec/bitrate/chapter signals from a release name.

    ``settings`` is accepted for forward-compatibility (a future slice may
    want locale-specific hints) but this slice's inference is name-only and
    deterministic. If only a container/extension is present, that alone is
    still a useful (partial) result -- codec is then derived from it.
    """
    lowered = (name or "").lower()

    container = next((token for token in _CONTAINER_TOKENS if _has_token(lowered, token)), None)
    codec = next((token for token in _CODEC_TOKENS if _has_token(lowered, token)), None)
    if codec is None and container is not None:
        codec = _DEFAULT_CODEC_FOR_CONTAINER.get(container)

    bitrate_match = _BITRATE_RE.search(lowered)
    bitrate_kbps = int(bitrate_match.group(1)) if bitrate_match else None

    chapters = True if _CHAPTER_RE.search(lowered) else None

    return InferredQuality(
        container=container, codec=codec, bitrate_kbps=bitrate_kbps, chapters=chapters
    )


def _has_token(lowered_name: str, token: str) -> bool:
    return re.search(rf"\b{re.escape(token)}\b", lowered_name) is not None


def _definition_fits(inferred: InferredQuality, definition: QualityDefinition) -> bool:
    """Whether an inference is consistent with a quality tier.

    Missing signals (bitrate/codec unknown) never cause a rejection --
    absence of information is not evidence against a fit. A "required"
    chapter expectation, though, needs a positive chapter signal to match;
    "preferred"/"not_required" never block a match on chapters.
    """
    if inferred.container != definition.container:
        return False
    if inferred.codec is not None and inferred.codec != definition.codec:
        return False
    if inferred.bitrate_kbps is not None and not (
        definition.min_bitrate_kbps <= inferred.bitrate_kbps <= definition.max_bitrate_kbps
    ):
        return False
    if definition.chapters == "required" and inferred.chapters is not True:
        return False
    return True


def evaluate_quality_for_profile(
    inferred: InferredQuality,
    profile: QualityProfile,
    definitions: list[QualityDefinition],
) -> QualityFit:
    """Match an inference against a profile's ordered quality tiers.

    ``profile.quality_ids`` is best-tier-first. A match at index 0 is
    "preferred"; a match at or before ``cutoff_quality_id`` is "accepted";
    a match after the cutoff is "below_cutoff". No tier match falls back to
    the legacy bare ``allowed_formats`` list ("accepted", no matched tier)
    before being "rejected". No container signal at all is "unknown".
    """
    if inferred.container is None:
        return QualityFit(
            inferred=inferred,
            matched_quality_id=None,
            status="unknown",
            reason="could not infer audiobook quality from the release name",
        )

    definitions_by_id = {d.id: d for d in definitions}
    cutoff_index = (
        profile.quality_ids.index(profile.cutoff_quality_id)
        if profile.cutoff_quality_id in profile.quality_ids
        else len(profile.quality_ids) - 1
    )

    for index, quality_id in enumerate(profile.quality_ids):
        definition = definitions_by_id.get(quality_id)
        if definition is None or not _definition_fits(inferred, definition):
            continue
        if index == 0:
            status: QualityStatus = "preferred"
            reason = f"matches top-preference tier {definition.name!r}"
        elif index <= cutoff_index:
            status = "accepted"
            reason = f"matches tier {definition.name!r} (within profile cutoff)"
        else:
            status = "below_cutoff"
            reason = f"matches tier {definition.name!r}, below profile cutoff"
        return QualityFit(
            inferred=inferred, matched_quality_id=quality_id, status=status, reason=reason
        )

    if inferred.container in profile.allowed_formats:
        return QualityFit(
            inferred=inferred,
            matched_quality_id=None,
            status="accepted",
            reason=f"container {inferred.container!r} allowed by profile, no quality tier matched",
        )

    return QualityFit(
        inferred=inferred,
        matched_quality_id=None,
        status="rejected",
        reason=f"container {inferred.container!r} is not allowed by this quality profile",
    )


def should_convert_candidate(
    format_or_inferred: str | InferredQuality,
    profile: QualityProfile,
    definitions: list[QualityDefinition],
) -> bool:
    """Whether an imported candidate should be offered to the conversion backend.

    This slice only knows how to convert lossy mp3/m4a sources toward an
    m4b target -- it never re-encodes an m4b (no upgrade-seeking
    conversion exists yet). The target container is read from the
    profile's top-preference quality tier (falling back to
    ``cutoff_format``), so a profile that doesn't target m4b at all simply
    never triggers conversion here.
    """
    inferred = (
        format_or_inferred
        if isinstance(format_or_inferred, InferredQuality)
        else InferredQuality(container=format_or_inferred or None)
    )

    definitions_by_id = {d.id: d for d in definitions}
    top_definition = definitions_by_id.get(profile.quality_ids[0]) if profile.quality_ids else None
    target_container = top_definition.container if top_definition else profile.cutoff_format

    if target_container != "m4b":
        return False
    if inferred.container == "m4b":
        return False
    return inferred.container in CONVERTIBLE_SOURCE_FORMATS
