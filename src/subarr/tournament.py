"""#65 — tournament judging engine.

The scoring/ranking HEART of the Whisper-tuning tournament. Given several
config "entrants" (each a candidate SRT output for the SAME source audio), it
judges each objectively and ranks them so the winner can be adopted.

This is the UNBLOCKED half. The "arena" — running the same audio through each
config variant live via subgen — is blocked on #88 (per-request kwargs; today
SUBGEN_KWARGS is global-only). The judges here operate on already-produced
outputs, so they're fully buildable + testable now.

Judges (composable):
  - readability (#92, deterministic): CPS/CPL/timing/overlap.
  - SRT integrity: did it parse to real cues at all? A config that produces
    garbage / no cues is DISQUALIFIED, not merely low-scored.
  - reference-free QE (#94): HOOK only — wire when the QE experiment lands.
  - speed: wall-clock, used as a tiebreak.

The RUBRIC (weights/penalties below) is the v1 PROPOSAL — tune with Judd. The
*shape* is intentional and what the tests pin: integrity is a gate,
readability dominates, speed breaks ties, QE slots in as an advisory
contributor later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from .subtitle_readability import ReadabilityReport, analyze_srt, parse_srt
from .transcript_signals import (
    canned_phrase_hits,
    repeated_line_ratio,
    silence_text_ratio,
    uncovered_speech_ratio,
)

# --- v1 proposed rubric (TUNABLE) ---------------------------------------
CRITICAL_PENALTY = 2.0   # weight of a 'critical' readability issue
WARN_PENALTY = 1.0       # weight of a 'warn' readability issue
QUALITY_WEIGHT = 0.85    # quality (100 − QE − readability penalties) vs speed
SPEED_WEIGHT = 0.15

# Readability is the SECONDARY judge (#65 research: QE signals are the real
# discriminators; readability is advisory). It can shave at most
# READABILITY_CAP points off an otherwise-good transcript — never floor it.
# Tier-B 2026-06-04 showed the old "readability IS the base" rubric scored
# accurate, speech-aligned transcripts of rapid dialogue at ~0 (high CPS trips
# the linter), masking the QE signals that decide a tournament.
READABILITY_K = 25.0     # readability penalty = load_per_cue × K …
READABILITY_CAP = 20.0   # … capped here so readability stays secondary

# QE (reference-free) penalties — the real discriminators (#65 research). A
# quality score starts from readability (0-100) and these subtract from it, so
# a hallucinating/looping entrant tanks regardless of how "readable" its
# fabricated text is. Tuned so a fully-hallucinated or fully-looping output
# drops to ~0.
SILENCE_TEXT_PENALTY = 100.0   # × fraction of text over silence (hallucination)
REPEAT_PENALTY = 100.0         # × fraction of duplicate lines (looping)
CANNED_PENALTY = 40.0          # × canned-phrase cues (capped)
CANNED_CAP = 3
# Base-camp completeness: speech with no subtitle (dropped dialogue). Weighted
# below the catastrophic-failure tier (silence/repeat = 100) but high enough to
# stop an incomplete sub from winning — an incomplete sub is a journey defect.
COVERAGE_PENALTY = 50.0        # × fraction of speech left unsubtitled

# CONSENSUS judge (#65) — the cross-config pseudo-reference. The per-entrant
# judges above score each output in isolation, so they miss a fluent, well-formed
# but DIVERGENT output (a unique mistranslation, or a hallucinated end-card no
# other config produced). With ≥2 entrants on the SAME clip, agreement across
# them stands in for ground truth: build a majority content-word vocabulary, then
# score each entrant on precision (only says agreed things — low = divergence/
# hallucination) and recall (covers the agreed content — low = dropout/loop
# collapse). The penalty subtracts from the composite by (1 − f1). No ground
# truth required; robust to translation wording/timing variance (content-word
# sets, not cue alignment).
CONSENSUS_PENALTY = 40.0   # × (1 − consensus F1)


@dataclass
class Entrant:
    label: str                  # config name, e.g. "large-v3 / beam5 / vad"
    srt_text: str               # candidate output for the shared source
    gen_time_s: float | None = None
    config: dict[str, Any] = field(default_factory=dict)
    # silero speech ranges (s) for the SOURCE audio (#111). Shared across
    # entrants of one tournament (same source); enables the text-over-silence
    # hallucination judge. None → that judge is skipped (no penalty).
    speech_ranges: list[tuple[float, float]] | None = None


@dataclass
class Scorecard:
    entrant_label: str
    disqualified: bool
    composite: float            # 0-100 (0 if disqualified)
    readability_score: float    # 0-100
    cue_count: int
    gen_time_s: float | None
    readability: dict[str, Any] | None   # the #92 report.to_dict()
    signals: dict[str, Any] | None = None  # QE signals (silence/repeat/canned)
    consensus: dict[str, Any] | None = None  # vs-majority precision/recall/f1
    notes: str = ""


@dataclass
class TournamentResult:
    scorecards: list[Scorecard]           # ranked best-first
    winner_label: str | None
    # mean pairwise content-word agreement across entrants (0-1). Low → the
    # configs disagree about what was said → flag the clip for human review.
    # None when fewer than 2 scorable entrants (no consensus possible).
    clip_agreement: float | None = None


def _readability_load(report: ReadabilityReport) -> float:
    """Issue load PER CUE so a config that produces more (or longer) cues
    isn't unfairly punished — entrants share a source but segment it
    differently, so absolute issue counts aren't comparable."""
    critical = sum(1 for i in report.issues if i.severity == "critical")
    warn = sum(1 for i in report.issues if i.severity == "warn")
    cues = max(report.cue_count, 1)
    return (critical * CRITICAL_PENALTY + warn * WARN_PENALTY) / cues


def _readability_score(report: ReadabilityReport) -> float:
    """0-100, for display on the scorecard. NOTE: this is informational only —
    the composite uses a CAPPED readability *penalty* (see score_entrant), not
    this score as its base. Kept so the UI can still show a readability grade."""
    return max(0.0, 100.0 - _readability_load(report) * 100.0)


def score_entrant(entrant: Entrant, fastest_time_s: float | None = None) -> Scorecard:
    cues = parse_srt(entrant.srt_text)
    if not cues:
        return Scorecard(
            entrant_label=entrant.label, disqualified=True, composite=0.0,
            readability_score=0.0, cue_count=0, gen_time_s=entrant.gen_time_s,
            readability=None, signals=None,
            notes="no parseable subtitle cues (disqualified)",
        )
    report = analyze_srt(entrant.srt_text)
    r_score = _readability_score(report)

    # QE-PRIMARY scoring (#65 research). Quality starts at 100 and the
    # reference-free QE signals — the real discriminators — subtract from it,
    # so a hallucinating/looping/canned entrant tanks regardless of how
    # "readable" its fabricated text is. Readability contributes only a CAPPED
    # secondary penalty, so an accurate transcript of fast dialogue (high CPS)
    # stays a high score instead of being floored.
    sil = silence_text_ratio(cues, entrant.speech_ranges)
    rep = repeated_line_ratio(cues)
    canned = canned_phrase_hits(cues)
    # Base-camp COMPLETENESS (#65): speech left without a subtitle = dropped
    # dialogue = an incomplete sub. The complement of silence_text_ratio, and
    # the fix for the terseness artifact (Tier-B 2026-06-04: the judge rewarded
    # short outputs that omit dialogue; chrF-vs-pro-reference showed those are
    # LESS faithful). Only fires when VAD speech_ranges are supplied.
    uncov = uncovered_speech_ratio(cues, entrant.speech_ranges)
    qe_penalty = (
        sil * SILENCE_TEXT_PENALTY
        + rep * REPEAT_PENALTY
        + min(canned, CANNED_CAP) * CANNED_PENALTY
        + uncov * COVERAGE_PENALTY
    )
    readability_penalty = min(_readability_load(report) * READABILITY_K, READABILITY_CAP)
    quality = max(0.0, 100.0 - qe_penalty - readability_penalty)
    signals = {
        "silence_text_ratio": round(sil, 4),
        "repeated_line_ratio": round(rep, 4),
        "canned_phrase_hits": canned,
        "uncovered_speech_ratio": round(uncov, 4),
    }

    if entrant.gen_time_s and fastest_time_s and entrant.gen_time_s > 0:
        speed_score = min(100.0, (fastest_time_s / entrant.gen_time_s) * 100.0)
        composite = quality * QUALITY_WEIGHT + speed_score * SPEED_WEIGHT
    else:
        composite = quality
    return Scorecard(
        entrant_label=entrant.label, disqualified=False,
        composite=round(composite, 2), readability_score=round(r_score, 2),
        cue_count=report.cue_count, gen_time_s=entrant.gen_time_s,
        readability=report.to_dict(), signals=signals,
    )


_WORD = re.compile(r"[0-9a-z]+")


def _content_tokens(srt_text: str) -> set[str]:
    """Lower-cased word set from a candidate's cue text. Word SETS (not counts)
    so a looped phrase doesn't inflate agreement — the repeat judge owns looping;
    consensus is about WHICH words are agreed, not how often."""
    return {w for cue in parse_srt(srt_text) for w in _WORD.findall(cue.text.lower())}


def consensus_scores(
    label_to_srt: dict[str, str],
) -> tuple[dict[str, dict[str, float]], float | None]:
    """Cross-config consensus (#65). Build a majority content-word vocabulary
    (words present in > half the entrants), then score each entrant's precision
    (its words that are agreed), recall (agreed words it covers), and F1 against
    it. Also return clip-level agreement = mean pairwise Jaccard of the vocabs.

    Needs ≥2 entrants; returns ({}, None) otherwise (no consensus possible)."""
    if len(label_to_srt) < 2:
        return {}, None
    vocabs = {label: _content_tokens(srt) for label, srt in label_to_srt.items()}
    n = len(vocabs)
    threshold = n // 2 + 1            # strict majority; for n=2 → both must agree
    doc_freq: dict[str, int] = {}
    for v in vocabs.values():
        for tok in v:
            doc_freq[tok] = doc_freq.get(tok, 0) + 1
    consensus_vocab = {tok for tok, c in doc_freq.items() if c >= threshold}

    reports: dict[str, dict[str, float]] = {}
    for label, v in vocabs.items():
        agreed = len(v & consensus_vocab)
        precision = agreed / len(v) if v else 0.0
        recall = agreed / len(consensus_vocab) if consensus_vocab else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        reports[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }

    # clip agreement: mean pairwise Jaccard of the vocabs (how much the configs
    # say the same things at all).
    pair_sims = []
    for a, b in combinations(vocabs.values(), 2):
        union = a | b
        pair_sims.append(len(a & b) / len(union) if union else 0.0)
    clip_agreement = round(sum(pair_sims) / len(pair_sims), 4) if pair_sims else None
    return reports, clip_agreement


def run_tournament(entrants: list[Entrant]) -> TournamentResult:
    if not entrants:
        return TournamentResult(scorecards=[], winner_label=None)
    times = [e.gen_time_s for e in entrants if e.gen_time_s]
    fastest = min(times) if times else None
    cards = [score_entrant(e, fastest_time_s=fastest) for e in entrants]

    # CONSENSUS pass (#65): the cross-config pseudo-reference. Only scorable
    # (non-disqualified) entrants form the consensus; the divergence penalty
    # then subtracts from each composite by (1 − F1).
    by_label = {e.label: e for e in entrants}
    scorable = {c.entrant_label: by_label[c.entrant_label].srt_text
                for c in cards if not c.disqualified}
    reports, clip_agreement = consensus_scores(scorable)
    for c in cards:
        rep = reports.get(c.entrant_label)
        if rep is None:
            continue
        c.consensus = rep
        penalty = (1.0 - rep["f1"]) * CONSENSUS_PENALTY
        c.composite = round(max(0.0, c.composite - penalty), 2)

    # Disqualified always last; otherwise composite desc, faster breaks ties.
    cards.sort(key=lambda c: (
        c.disqualified,
        -c.composite,
        c.gen_time_s if c.gen_time_s is not None else float("inf"),
    ))
    winner = next((c.entrant_label for c in cards if not c.disqualified), None)
    return TournamentResult(
        scorecards=cards, winner_label=winner, clip_agreement=clip_agreement,
    )
