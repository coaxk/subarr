"""#65 — reference-free transcript quality signals (the tournament's real
discriminators, per the research synthesis on #65).

These need no ground truth: they flag the failure modes Whisper configs
diverge on — text over silence (hallucination), looping repetition, canned
"thanks for watching"-style phrases. The hallucination signal rides on the
silero speech ranges from #111.

Tests pin behaviour (a clean cue scores ~0, a hallucinated/looping one scores
high), not exact magnitudes.
"""
from __future__ import annotations


def _sig():
    from subarr import transcript_signals as s
    return s


def _cues(srt):
    from subarr.subtitle_readability import parse_srt
    return parse_srt(srt)


# --- silence_text_ratio: fraction of cue time with no underlying speech ---

def test_silence_text_ratio_zero_when_cues_sit_in_speech():
    s = _sig()
    cues = _cues("1\n00:00:00,000 --> 00:00:02,000\nreal dialogue\n")
    # speech covers the whole cue → not hallucinated
    assert s.silence_text_ratio(cues, [(0.0, 2.0)]) == 0.0


def test_silence_text_ratio_one_when_text_over_silence():
    s = _sig()
    cues = _cues("1\n00:00:10,000 --> 00:00:12,000\nThank you for watching\n")
    # speech is elsewhere; the cue sits entirely in a silent span → hallucination
    assert s.silence_text_ratio(cues, [(0.0, 2.0)]) == 1.0


# --- uncovered_speech_ratio: speech that has NO subtitle (incomplete sub) ---
# The base-camp complement of silence_text_ratio: a sub that drops dialogue
# leaves speech unsubtitled. A terse/truncated output that captures fewer cues
# scores high here (incomplete), even if everything it DID write is clean.

def test_uncovered_speech_ratio_zero_when_speech_fully_subtitled():
    s = _sig()
    cues = _cues("1\n00:00:00,000 --> 00:00:06,000\nfull dialogue across the clip\n")
    assert s.uncovered_speech_ratio(cues, [(0.0, 6.0)]) == 0.0


def test_uncovered_speech_ratio_high_when_dialogue_dropped():
    s = _sig()
    # speech spans 0-6s but the sub only covers the first 2s → 4s of dialogue
    # left with no subtitle (an incomplete sub) → ~0.67 uncovered.
    cues = _cues("1\n00:00:00,000 --> 00:00:02,000\nonly the start\n")
    r = s.uncovered_speech_ratio(cues, [(0.0, 6.0)])
    assert 0.6 < r < 0.72


def test_uncovered_speech_ratio_zero_without_speech_data():
    s = _sig()
    cues = _cues("1\n00:00:00,000 --> 00:00:02,000\nhi\n")
    # no VAD → don't penalize (mirrors silence_text_ratio)
    assert s.uncovered_speech_ratio(cues, None) == 0.0
    assert s.uncovered_speech_ratio(cues, []) == 0.0


def test_silence_text_ratio_partial_overlap():
    s = _sig()
    cues = _cues("1\n00:00:00,000 --> 00:00:04,000\nhalf over silence\n")
    # 2s of the 4s cue overlaps speech → 0.5 outside
    assert abs(s.silence_text_ratio(cues, [(0.0, 2.0)]) - 0.5) < 1e-6


def test_silence_text_ratio_no_speech_data_returns_zero():
    s = _sig()
    cues = _cues("1\n00:00:00,000 --> 00:00:02,000\nx\n")
    # no VAD ranges → can't judge → 0.0 (don't penalize on missing data)
    assert s.silence_text_ratio(cues, None) == 0.0
    assert s.silence_text_ratio(cues, []) == 0.0


# --- repeated_line_ratio: looping / stuck-decoder detection ----------------

def test_repeated_line_ratio_flags_loops():
    s = _sig()
    srt = ""
    for i in range(1, 6):
        srt += f"{i}\n00:00:0{i-1},000 --> 00:00:0{i},000\nyou\n\n"
    # 5 identical lines → highly repetitive
    assert s.repeated_line_ratio(_cues(srt)) >= 0.7


def test_repeated_line_ratio_low_for_varied_dialogue():
    s = _sig()
    srt = (
        "1\n00:00:00,000 --> 00:00:02,000\nWhere are you going?\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nTo the market.\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\nI'll come with you.\n"
    )
    assert s.repeated_line_ratio(_cues(srt)) < 0.4


# --- canned_phrase_hits: known non-speech hallucination phrases ------------

def test_canned_phrase_hits_detects_known_phrases():
    s = _sig()
    srt = (
        "1\n00:00:00,000 --> 00:00:02,000\nThanks for watching!\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nSubtitles by amara.org\n"
    )
    assert s.canned_phrase_hits(_cues(srt)) >= 2


def test_canned_phrase_hits_zero_for_real_dialogue():
    s = _sig()
    cues = _cues("1\n00:00:00,000 --> 00:00:02,000\nThe reactor is overheating.\n")
    assert s.canned_phrase_hits(cues) == 0
