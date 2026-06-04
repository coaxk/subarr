"""Audio-language verification SOURCE classification (coverage badge tiers).

The coverage row exposes audio_source so the UI can show HOW confident it is:
user (you) > whisper (machine detect) > plex (your pick) > ffprobe (file tag).
"""
from __future__ import annotations

from subarr.coverage_engine import (
    CoverageItem,
    _classify_audio_label,
    _refine_audio_sources,
)


def _item(**kw):
    base = dict(media_type="episode", title="Show", canonical_path="TV/Show/S01E01")
    base.update(kw)
    return CoverageItem(**base)


def test_ffprobe_tag_is_lowest_trust_source():
    it = _item(audio_langs=["eng"], original_language="English")
    _classify_audio_label(it)
    assert it.audio_source == "ffprobe"


def test_user_verification_is_user_source():
    it = _item(audio_langs=["eng"], file_canonical_path="TV/Show/S01E01.mkv")
    _classify_audio_label(it, user_verifications={"TV/Show/S01E01.mkv": "fra"})
    assert it.audio_source == "user"
    assert it.audio_verified is True


def test_plex_pick_is_plex_source():
    it = _item(audio_langs=["eng"], original_language="French")
    _classify_audio_label(it, tautulli_hints={"show": "fra"})
    assert it.audio_source == "plex"


def test_no_audio_metadata_has_no_source():
    it = _item(audio_langs=["und"])
    _classify_audio_label(it)
    assert it.audio_label_unknown is True
    assert it.audio_source is None


def test_suspect_label_has_no_confident_source():
    # foreign original but all-English audio tags → suspect, not a trusted source
    it = _item(audio_langs=["eng"], original_language="Korean")
    _classify_audio_label(it)
    assert it.audio_label_suspect is True
    assert it.audio_source is None


def test_refine_maps_store_source_to_badge_tier():
    items = [
        _item(file_canonical_path="a.mkv", audio_source="user"),
        _item(file_canonical_path="b.mkv", audio_source="user"),
        _item(file_canonical_path="c.mkv", audio_source="user"),
        _item(file_canonical_path="d.mkv", audio_source="ffprobe"),  # not store-verified
    ]
    sources = {
        "a.mkv": "user",
        "b.mkv": "whisper-robust",
        "c.mkv": "auto-high-conf",
        # d.mkv intentionally absent
    }
    _refine_audio_sources(items, sources)
    assert items[0].audio_source == "user"
    assert items[1].audio_source == "whisper"
    assert items[2].audio_source == "whisper"   # auto-high-conf = a machine detect
    assert items[3].audio_source == "ffprobe"   # untouched (not in store)


def test_refine_series_intent_counts_as_user():
    items = [_item(file_canonical_path="e.mkv", audio_source="user")]
    _refine_audio_sources(items, {"e.mkv": "series_intent:user"})
    assert items[0].audio_source == "user"
