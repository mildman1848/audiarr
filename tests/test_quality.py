"""Tests for the deterministic audiobook quality helpers (app/quality.py)."""

from __future__ import annotations

from app.models.settings import QualityProfile, Settings
from app.quality import (
    InferredQuality,
    evaluate_quality_for_profile,
    infer_quality_from_name,
    should_convert_candidate,
)

# ---------------------------------------------------------------------------
# infer_quality_from_name
# ---------------------------------------------------------------------------


def test_infer_recognizes_container_codec_bitrate_and_chapters():
    inferred = infer_quality_from_name("Some Audiobook M4B AAC 128kbps Chaptered")
    assert inferred.container == "m4b"
    assert inferred.codec == "aac"
    assert inferred.bitrate_kbps == 128
    assert inferred.chapters is True


def test_infer_recognizes_mp3_bitrate_with_space_and_k_suffix():
    inferred = infer_quality_from_name("Some Book MP3 320k")
    assert inferred.container == "mp3"
    assert inferred.codec == "mp3"
    assert inferred.bitrate_kbps == 320

    inferred2 = infer_quality_from_name("Another Book 64 kbps MP3")
    assert inferred2.bitrate_kbps == 64


def test_infer_derives_codec_from_container_when_no_explicit_codec_token():
    inferred = infer_quality_from_name("Some Book.m4b")
    assert inferred.container == "m4b"
    assert inferred.codec == "aac"
    assert inferred.bitrate_kbps is None
    assert inferred.chapters is None


def test_infer_does_not_confuse_sample_rate_or_bit_depth_with_bitrate():
    inferred = infer_quality_from_name("Some Book FLAC 44.1kHz 24bit")
    assert inferred.container == "flac"
    assert inferred.bitrate_kbps is None


def test_infer_extension_only_still_produces_a_useful_result():
    inferred = infer_quality_from_name("track01.mp3")
    assert inferred.container == "mp3"
    assert inferred.codec == "mp3"


def test_infer_unknown_name_yields_no_container():
    inferred = infer_quality_from_name("Completely Unrecognizable Release Name")
    assert inferred.container is None
    assert inferred.codec is None


# ---------------------------------------------------------------------------
# evaluate_quality_for_profile
# ---------------------------------------------------------------------------


def _default_settings() -> Settings:
    return Settings()


def test_evaluate_unknown_container_is_unknown_status():
    settings = _default_settings()
    profile = settings.quality_profiles[0]
    inferred = InferredQuality()

    fit = evaluate_quality_for_profile(inferred, profile, settings.quality_definitions)

    assert fit.status == "unknown"
    assert fit.matched_quality_id is None


def test_evaluate_top_tier_match_is_preferred():
    settings = _default_settings()
    profile = settings.quality_profiles[0]
    assert profile.quality_ids[0] == "m4b-aac-128"
    # m4b-aac-128 requires chapters="required" -- an explicit chapter hint
    # is needed to actually match it.
    inferred = InferredQuality(container="m4b", codec="aac", bitrate_kbps=128, chapters=True)

    fit = evaluate_quality_for_profile(inferred, profile, settings.quality_definitions)

    assert fit.status == "preferred"
    assert fit.matched_quality_id == "m4b-aac-128"


def test_evaluate_below_top_tier_is_accepted_or_below_cutoff():
    settings = _default_settings()
    profile = settings.quality_profiles[0]
    # cutoff_quality_id is "m4b-aac-128" (index 0), so any match after index
    # 0 is below the cutoff by construction of the default profile.
    inferred = InferredQuality(container="m4b", codec="aac", bitrate_kbps=64, chapters=None)

    fit = evaluate_quality_for_profile(inferred, profile, settings.quality_definitions)

    assert fit.status == "below_cutoff"
    assert fit.matched_quality_id == "m4b-aac-64"


def test_evaluate_no_tier_match_falls_back_to_allowed_formats():
    settings = _default_settings()
    profile = settings.quality_profiles[0]
    # bitrate outside every mp3 tier's band, but mp3 is in allowed_formats.
    inferred = InferredQuality(container="mp3", codec="mp3", bitrate_kbps=32, chapters=None)

    fit = evaluate_quality_for_profile(inferred, profile, settings.quality_definitions)

    assert fit.status == "accepted"
    assert fit.matched_quality_id is None


def test_evaluate_container_not_allowed_is_rejected():
    settings = _default_settings()
    profile = QualityProfile(name="M4B only", allowed_formats=["m4b"], quality_ids=["m4b-aac-128"])
    inferred = InferredQuality(container="flac", codec="flac", bitrate_kbps=1000, chapters=None)

    fit = evaluate_quality_for_profile(inferred, profile, settings.quality_definitions)

    assert fit.status == "rejected"
    assert fit.matched_quality_id is None


def test_evaluate_missing_bitrate_signal_does_not_block_a_match():
    settings = _default_settings()
    profile = settings.quality_profiles[0]
    inferred = InferredQuality(container="mp3", codec="mp3", bitrate_kbps=None, chapters=None)

    fit = evaluate_quality_for_profile(inferred, profile, settings.quality_definitions)

    assert fit.matched_quality_id == "mp3-320"


# ---------------------------------------------------------------------------
# should_convert_candidate
# ---------------------------------------------------------------------------


def test_should_convert_mp3_and_m4a_toward_default_m4b_profile():
    settings = _default_settings()
    profile = settings.quality_profiles[0]

    assert should_convert_candidate("mp3", profile, settings.quality_definitions) is True
    assert should_convert_candidate("m4a", profile, settings.quality_definitions) is True


def test_should_not_convert_an_already_m4b_candidate():
    settings = _default_settings()
    profile = settings.quality_profiles[0]

    assert should_convert_candidate("m4b", profile, settings.quality_definitions) is False


def test_should_not_convert_flac_toward_m4b_profile():
    settings = _default_settings()
    profile = settings.quality_profiles[0]

    # FLAC isn't a source format this slice converts (only mp3/m4a are).
    assert should_convert_candidate("flac", profile, settings.quality_definitions) is False


def test_should_not_convert_when_profiles_top_tier_does_not_target_m4b():
    settings = _default_settings()
    profile = QualityProfile(
        name="MP3 shop",
        allowed_formats=["mp3"],
        cutoff_format="mp3",
        quality_ids=["mp3-320"],
        cutoff_quality_id="mp3-320",
    )

    assert should_convert_candidate("mp3", profile, settings.quality_definitions) is False
    assert should_convert_candidate("m4a", profile, settings.quality_definitions) is False
