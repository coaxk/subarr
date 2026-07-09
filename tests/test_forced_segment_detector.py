"""#364 slice 1 — pure detector core. A stub LID (no subgen, no audio) drives
utterance classification, span merge (min-duration + merge-gap), the over-flag
bias, and the mostly-foreign bail."""

from __future__ import annotations

from subarr.forced_segment import (
    ForcedSegmentParams,
    Span,
    assemble_foreign_spans,
    classify_utterances,
    foreign_fraction,
    is_mostly_foreign,
)

# Utterances are (start_s, end_s). LID stub returns (lang, confidence) per utterance.
UTTS = [(0.0, 3.0), (3.2, 6.0), (10.0, 14.0), (14.5, 18.0)]


def _lid(mapping):
    return lambda utt: mapping[utt]


def test_confident_english_is_not_foreign():
    p = ForcedSegmentParams()
    lid = _lid({UTTS[0]: ("en", 0.95)})
    classified = classify_utterances([UTTS[0]], lid, p)
    assert classified == [(UTTS[0], False)]


def test_non_english_is_foreign_at_any_confidence():
    p = ForcedSegmentParams()
    lid = _lid({UTTS[0]: ("fr", 0.55)})
    assert classify_utterances([UTTS[0]], lid, p) == [(UTTS[0], True)]


def test_primary_lang_is_case_insensitive_against_utterance_lang():
    # slice-3 forward-compat: primary_lang set to the file's real audio language
    # (e.g. "PT") must match "pt" utterances as primary (not foreign).
    p = ForcedSegmentParams(primary_lang="PT")
    lid = _lid({UTTS[0]: ("pt", 0.95)})
    assert classify_utterances([UTTS[0]], lid, p) == [(UTTS[0], False)]


def test_low_confidence_over_flags_to_foreign():
    p = ForcedSegmentParams(conf_floor=0.6, over_flag_low_confidence=True)
    lid = _lid({UTTS[0]: ("en", 0.2)})  # uncertain English -> over-flagged
    assert classify_utterances([UTTS[0]], lid, p) == [(UTTS[0], True)]
    p2 = ForcedSegmentParams(conf_floor=0.6, over_flag_low_confidence=False)
    assert classify_utterances([UTTS[0]], lid, p2) == [(UTTS[0], False)]


def test_contiguous_foreign_merge_within_gap_and_min_duration_floor():
    # Two adjacent French utterances (gap 0.2s) merge into one span >= floor;
    # an isolated 0.5s French blip is dropped by the min-duration floor.
    p = ForcedSegmentParams(min_span_s=2.5, merge_gap_s=1.5)
    utts = [(0.0, 3.0), (3.2, 6.0), (20.0, 20.5)]
    lid = _lid({utts[0]: ("fr", 0.9), utts[1]: ("fr", 0.9), utts[2]: ("es", 0.9)})
    spans = assemble_foreign_spans(utts, lid, p)
    assert spans == [Span(start_ms=0, end_ms=6000)]  # merged; blip dropped


def test_gap_larger_than_merge_gap_stays_separate():
    p = ForcedSegmentParams(min_span_s=2.0, merge_gap_s=1.0)
    utts = [(0.0, 3.0), (10.0, 13.0)]
    lid = _lid({utts[0]: ("fr", 0.9), utts[1]: ("fr", 0.9)})
    spans = assemble_foreign_spans(utts, lid, p)
    assert spans == [Span(0, 3000), Span(10000, 13000)]


def test_mostly_foreign_bail_predicate():
    p = ForcedSegmentParams(mostly_foreign_fraction=0.5)
    utts = [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)]
    lid = _lid({utts[0]: ("fr", 0.9), utts[1]: ("fr", 0.9), utts[2]: ("en", 0.9)})
    classified = classify_utterances(utts, lid, p)
    assert round(foreign_fraction(classified), 3) == round(6.0 / 9.0, 3)
    assert is_mostly_foreign(classified, p) is True
    # A single short foreign scene in a long English file does NOT bail.
    utts2 = [(0.0, 60.0), (60.0, 63.0)]
    lid2 = _lid({utts2[0]: ("en", 0.9), utts2[1]: ("fr", 0.9)})
    assert is_mostly_foreign(classify_utterances(utts2, lid2, p), p) is False
