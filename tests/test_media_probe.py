"""Tests for media_probe (ffprobe wrapper + classification helpers)."""
from __future__ import annotations



# Canned ffprobe JSON fixtures (real shapes observed against live subgen).
EN_ONLY = {
    "format": {"duration": "2610.00"},
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "hevc",
         "disposition": {"default": 1, "comment": 0, "forced": 0, "hearing_impaired": 0},
         "tags": {}},
        {"index": 1, "codec_type": "audio", "codec_name": "aac",
         "disposition": {"default": 1, "comment": 0, "forced": 0, "hearing_impaired": 0},
         "tags": {"language": "eng", "title": "English"}},
        {"index": 2, "codec_type": "subtitle", "codec_name": "subrip",
         "disposition": {"default": 0, "comment": 0, "forced": 0, "hearing_impaired": 0},
         "tags": {"language": "eng", "title": "English"}},
    ],
}

EN_FORCED_ONLY = {
    "format": {"duration": "1800.00"},
    "streams": [
        {"index": 0, "codec_type": "subtitle", "codec_name": "subrip",
         "disposition": {"default": 0, "comment": 0, "forced": 1, "hearing_impaired": 0},
         "tags": {"language": "eng", "title": "English Forced"}},
    ],
}

EN_SDH_ONLY = {
    "format": {"duration": "1800.00"},
    "streams": [
        {"index": 0, "codec_type": "subtitle", "codec_name": "subrip",
         "disposition": {"default": 0, "comment": 0, "forced": 0, "hearing_impaired": 1},
         "tags": {"language": "eng", "title": "English (SDH)"}},
    ],
}

EN_TITLE_SDH_NO_DISPOSITION = {
    # Common in the wild: hearing_impaired disposition flag missing but title
    # says SDH. We must still classify as SDH.
    "format": {"duration": "1800.00"},
    "streams": [
        {"index": 0, "codec_type": "subtitle", "codec_name": "subrip",
         "disposition": {"default": 0, "comment": 0, "forced": 0, "hearing_impaired": 0},
         "tags": {"language": "eng", "title": "English SDH"}},
    ],
}

NO_EN = {
    "format": {"duration": "1800.00"},
    "streams": [
        {"index": 0, "codec_type": "subtitle", "codec_name": "subrip",
         "disposition": {"default": 0, "comment": 0, "forced": 0, "hearing_impaired": 0},
         "tags": {"language": "fre", "title": "French"}},
    ],
}

MULTI_AUDIO_UND = {
    "format": {"duration": "2610.00"},
    "streams": [
        {"index": 1, "codec_type": "audio", "codec_name": "aac",
         "disposition": {"default": 1, "comment": 0, "forced": 0, "hearing_impaired": 0},
         "tags": {"language": "und"}},
        {"index": 2, "codec_type": "audio", "codec_name": "aac",
         "disposition": {"default": 0, "comment": 0, "forced": 0, "hearing_impaired": 0},
         "tags": {"language": "eng"}},
    ],
}


def test_parse_audio_streams(subarr_env):
    from subarr.media_probe import parse_ffprobe_json
    r = parse_ffprobe_json("X", EN_ONLY)
    assert r.duration_s == 2610.0
    assert len(r.audio) == 1
    assert r.audio[0].language == "eng"
    assert r.audio[0].default is True


def test_classify_full_english_sub_usable(subarr_env):
    from subarr.media_probe import has_usable_embedded_english, parse_ffprobe_json
    r = parse_ffprobe_json("X", EN_ONLY)
    assert has_usable_embedded_english(r) is True


def test_forced_english_is_not_usable(subarr_env):
    from subarr.media_probe import (
        has_forced_or_sdh_english, has_usable_embedded_english, parse_ffprobe_json,
    )
    r = parse_ffprobe_json("X", EN_FORCED_ONLY)
    assert has_usable_embedded_english(r) is False
    assert has_forced_or_sdh_english(r) is True


def test_sdh_by_disposition_is_usable_eng(subarr_env):
    """Post-2026-05-27 collapse: SDH counts as usable English.
    Forced/commentary stay non-usable."""
    from subarr.media_probe import (
        has_forced_or_commentary_english, has_usable_embedded_english, parse_ffprobe_json,
    )
    r = parse_ffprobe_json("X", EN_SDH_ONLY)
    assert has_usable_embedded_english(r) is True
    assert has_forced_or_commentary_english(r) is False
    # SDH metadata still preserved on the stream — Library tab uses it.
    assert r.subtitles[0].sdh is True


def test_sdh_by_title_alone_is_usable_eng(subarr_env):
    from subarr.media_probe import (
        has_forced_or_commentary_english, has_usable_embedded_english, parse_ffprobe_json,
    )
    r = parse_ffprobe_json("X", EN_TITLE_SDH_NO_DISPOSITION)
    assert has_usable_embedded_english(r) is True
    assert has_forced_or_commentary_english(r) is False
    assert r.subtitles[0].sdh is True


def test_no_english_track(subarr_env):
    from subarr.media_probe import (
        has_forced_or_commentary_english, has_usable_embedded_english, parse_ffprobe_json,
    )
    r = parse_ffprobe_json("X", NO_EN)
    assert has_usable_embedded_english(r) is False
    assert has_forced_or_commentary_english(r) is False


def test_forced_only_stays_partial(subarr_env):
    """Forced is not collapsed — that classification stays partial."""
    from subarr.media_probe import (
        has_forced_or_commentary_english, has_usable_embedded_english, parse_ffprobe_json,
    )
    r = parse_ffprobe_json("X", EN_FORCED_ONLY)
    assert has_usable_embedded_english(r) is False
    assert has_forced_or_commentary_english(r) is True


def test_track_summary_labels(subarr_env):
    from subarr.media_probe import english_track_summary, parse_ffprobe_json
    assert english_track_summary(parse_ffprobe_json("X", EN_ONLY)) == "EN"
    assert english_track_summary(parse_ffprobe_json("X", EN_FORCED_ONLY)) == "EN(forced)"
    # Post-collapse: SDH label preserved for UI honesty but classified usable.
    assert english_track_summary(parse_ffprobe_json("X", EN_SDH_ONLY)) == "EN(SDH)"
    assert english_track_summary(parse_ffprobe_json("X", NO_EN)) is None


def test_audio_lang_summary_dedupes_and_keeps_order(subarr_env):
    from subarr.media_probe import audio_lang_summary, parse_ffprobe_json
    r = parse_ffprobe_json("X", MULTI_AUDIO_UND)
    assert audio_lang_summary(r) == ["und", "eng"]
