"""#131 — strata window selection for the auto-sampler.

The whole point: the sample must include a SILENCE/music stratum, not just
dense speech — clean dialogue doesn't discriminate configs; hallucination over
silence does. These tests pin that behaviour (pure function, no ffmpeg/VAD).
"""
from __future__ import annotations

from subarr.arena_sampler import WINDOW_S, select_windows


def test_includes_a_silence_window_for_a_long_gap():
    # speech early, a long silent gap in the middle, speech late.
    ranges = [(10.0, 70.0), (400.0, 460.0)]   # ~330s silence gap between them
    wins = select_windows(ranges, duration=500.0)
    kinds = {w["kind"] for w in wins}
    assert "silence" in kinds, f"expected a silence stratum, got {kinds}"
    assert "speech" in kinds
    # the silence window must actually sit inside the gap (pure dead air)
    sil = next(w for w in wins if w["kind"] == "silence")
    assert 70.0 <= sil["start"] <= 460.0 - WINDOW_S


def test_boundary_window_captures_a_speech_to_silence_transition():
    ranges = [(100.0, 200.0)]   # one big speech region, silence after
    wins = select_windows(ranges, duration=400.0)
    kinds = [w["kind"] for w in wins]
    assert "boundary" in kinds
    b = next(w for w in wins if w["kind"] == "boundary")
    # boundary window leads with the tail of speech (starts before 200) then
    # runs into the silence after it.
    assert b["start"] < 200.0 and b["start"] + b["len"] > 200.0


def test_windows_are_deduped_when_they_would_overlap():
    # a single short speech region → speech + boundary collapse to ~same place;
    # we must not emit near-duplicate windows.
    ranges = [(50.0, 60.0)]
    wins = select_windows(ranges, duration=300.0)
    starts = sorted(w["start"] for w in wins)
    for a, b in zip(starts, starts[1:]):
        assert b - a >= WINDOW_S / 2, "windows overlap — dedup failed"


def test_no_speech_falls_back_to_a_single_window():
    wins = select_windows([], duration=120.0)
    assert len(wins) == 1 and wins[0]["kind"] == "fallback"


def test_windows_clamped_within_file():
    ranges = [(0.0, 5.0), (115.0, 120.0)]
    wins = select_windows(ranges, duration=120.0)
    for w in wins:
        assert 0.0 <= w["start"] <= 120.0 - WINDOW_S
