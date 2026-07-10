"""#364 slice 1 — the gate: English-tagged, not #357-multi, no existing forced,
duration floor. Pure predicate over a file's known metadata."""

from __future__ import annotations

from subarr.forced_segment import ForcedSegmentParams, qualifies_for_forced_segment


def _ok(**over):
    base = dict(
        audio_langs=["en"],
        embedded_en=None,
        lang_class="single",
        has_forced_sidecar=False,
        duration_s=3600.0,
    )
    base.update(over)
    return qualifies_for_forced_segment(params=ForcedSegmentParams(), **base)


def test_english_single_no_forced_qualifies():
    ok, reason = _ok()
    assert ok is True
    assert reason == "ok"


def test_non_english_audio_excluded():
    ok, reason = _ok(audio_langs=["fr"])
    assert ok is False and reason == "not_english_audio"


def test_multilingual_357_excluded():
    ok, reason = _ok(lang_class="multi")
    assert ok is False and reason == "multilingual"


def test_existing_embedded_forced_english_excluded():
    ok, reason = _ok(embedded_en="EN(forced)")
    assert ok is False and reason == "existing_forced"


def test_existing_forced_sidecar_excluded():
    ok, reason = _ok(has_forced_sidecar=True)
    assert ok is False and reason == "existing_forced"


def test_too_short_excluded():
    ok, reason = _ok(duration_s=30.0)
    assert ok is False and reason == "too_short"


def test_eng_three_letter_tag_and_full_en_sub_still_qualify():
    # 'eng' counts as English; a full EN sub (EN) does NOT block a forced sub.
    ok, reason = _ok(audio_langs=["eng"], embedded_en="EN")
    assert ok is True and reason == "ok"
