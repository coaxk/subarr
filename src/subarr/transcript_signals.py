"""#65 — reference-free transcript quality signals.

The discriminating judges for the Whisper-tuning tournament. Per the research
synthesis (#65), configs TIE on clean dialogue and DIVERGE on the failure
modes below — so these, not readability, are what actually rank entrants. All
are reference-free (no ground-truth transcript needed):

  - silence_text_ratio: subtitle text sitting where there's NO speech (the
    highest-value hallucination signal) — rides on the silero VAD ranges (#111).
  - repeated_line_ratio: looping / stuck-decoder output.
  - canned_phrase_hits: the canonical non-speech hallucinations
    ("thanks for watching", "subtitles by amara.org", …).

Cross-config CONSENSUS (segments most entrants agree on) is the other big
signal, but it's inherently cross-entrant so it lives in the tournament
runner, not here.
"""
from __future__ import annotations

from collections import Counter

# Canonical Whisper non-speech hallucinations (lower-cased substring match).
# These appear over music/silence/credits across countless real outputs.
_CANNED_PATTERNS = (
    "thank you for watching",
    "thanks for watching",
    "thank you for your watching",
    "subtitles by",
    "amara.org",
    "subscribe to",
    "please subscribe",
    "like and subscribe",
    "see you in the next",
    "thanks for listening",
)


def _cue_text(cue) -> str:
    return " ".join(getattr(cue, "lines", []) or []).strip()


def silence_text_ratio(cues, speech_ranges) -> float:
    """Fraction of total cue time that falls OUTSIDE detected speech — i.e.
    subtitle text where the audio has no voice (hallucination). 0.0 when
    there's no cue time or no speech data (don't penalize on missing VAD)."""
    if not cues or not speech_ranges:
        return 0.0
    total = 0.0
    outside = 0.0
    for c in cues:
        start = c.start_ms / 1000.0
        end = c.end_ms / 1000.0
        dur = end - start
        if dur <= 0:
            continue
        total += dur
        overlap = 0.0
        for s, e in speech_ranges:
            lo = max(start, s)
            hi = min(end, e)
            if hi > lo:
                overlap += hi - lo
        outside += max(0.0, dur - overlap)
    return outside / total if total > 0 else 0.0


def uncovered_speech_ratio(cues, speech_ranges) -> float:
    """Fraction of total SPEECH time that has NO cue overlapping it — i.e.
    detected dialogue left WITHOUT a subtitle (an incomplete sub). The base-camp
    complement of silence_text_ratio: that flags text over silence
    (hallucination), this flags silence-of-text over speech (dropped dialogue).
    A terse/truncated output that omits cues scores high here. 0.0 when there's
    no speech data (don't penalize on missing VAD)."""
    if not speech_ranges:
        return 0.0
    total = 0.0
    covered = 0.0
    spans = [(c.start_ms / 1000.0, c.end_ms / 1000.0) for c in cues]
    for s, e in speech_ranges:
        dur = e - s
        if dur <= 0:
            continue
        total += dur
        for cs, ce in spans:
            lo = max(s, cs)
            hi = min(e, ce)
            if hi > lo:
                covered += hi - lo
    if total <= 0:
        return 0.0
    return max(0.0, (total - min(covered, total)) / total)


def repeated_line_ratio(cues) -> float:
    """Fraction of cue lines that are duplicates of an earlier line. A stuck
    decoder repeats the same phrase; varied dialogue scores near 0."""
    texts = [t.lower() for t in (_cue_text(c) for c in cues) if t]
    if len(texts) <= 1:
        return 0.0
    counts = Counter(texts)
    duplicates = sum(n - 1 for n in counts.values())
    return duplicates / len(texts)


def canned_phrase_hits(cues) -> int:
    """Count cues containing a known non-speech hallucination phrase."""
    hits = 0
    for c in cues:
        t = _cue_text(c).lower()
        if any(p in t for p in _CANNED_PATTERNS):
            hits += 1
    return hits
