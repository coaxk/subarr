"""#92 — deterministic subtitle readability linter.

Pure SRT math against Netflix/BBC-style norms: reading speed (CPS), line
length (CPL), line count, cue duration, and overlaps. No model, no GPU, no
network — it works on any `.srt` already on disk (downloaded, generated, or
translated).

This is the *high-certainty* half of subtitle quality: "this line is
measurably too fast to read" or "this cue overlaps the next one" are facts,
not guesses. It complements (later, separately) reference-free QE for the
fuzzy translation-quality half — see the banked quality-measurement concept.
The whole point: the *arr stack scores subtitles on release-MATCH and never
on whether they're actually readable. This measures the thing nobody else does.

Thresholds follow common professional guidance (Netflix Timed Text General
Requirements / BBC subtitle guidelines):
  - reading speed (CPS): <= 20 comfortable; > 25 critical
  - line length (CPL): <= 42 characters
  - <= 2 lines per cue
  - cue duration: >= 0.833s (5/6 sec) and <= 7s
  - consecutive cues must not overlap
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Named so they're easy to tune or expose in Settings later.
MAX_CPS = 20.0
CRITICAL_CPS = 25.0
MAX_CPL = 42
MAX_LINES = 2
MIN_DURATION_S = 0.833
MAX_DURATION_S = 7.0

# HH:MM:SS,mmm --> HH:MM:SS,mmm  (accepts ',' or '.' as the ms separator)
_TS = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _ts_to_ms(h: str, m: str, s: str, ms: str) -> int:
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms.ljust(3, "0")[:3])


@dataclass
class Cue:
    index: int
    start_ms: int
    end_ms: int
    lines: list[str]

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.end_ms - self.start_ms) / 1000.0)

    @property
    def char_count(self) -> int:
        # Reading load: visible chars per line + the implicit space between
        # lines. Newlines themselves don't count.
        return sum(len(ln) for ln in self.lines) + max(0, len(self.lines) - 1)

    @property
    def max_cpl(self) -> int:
        return max((len(ln) for ln in self.lines), default=0)

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def cps(self) -> float:
        d = self.duration_s
        return (self.char_count / d) if d > 0 else float("inf")


@dataclass
class CueIssue:
    cue_index: int
    kind: str        # cps | cpl | lines | too_short | too_long | overlap
    severity: str    # warn | critical
    detail: str


@dataclass
class ReadabilityReport:
    cue_count: int
    issues: list[CueIssue] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.kind] = out.get(i.kind, 0) + 1
        return out

    @property
    def clean(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "cue_count": self.cue_count,
            "clean": self.clean,
            "counts": self.counts,
            "issues": [
                {"cue": i.cue_index, "kind": i.kind,
                 "severity": i.severity, "detail": i.detail}
                for i in self.issues
            ],
        }


def parse_srt(text: str) -> list[Cue]:
    """Minimal, lenient SRT parser. Blocks split on blank lines; the index
    line is optional. Malformed blocks (no timestamp) are skipped, not raised
    on — a real-world .srt with one junk block still gets analysed."""
    cues: list[Cue] = []
    normalised = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    for block in re.split(r"\n\s*\n", normalised):
        raw = block.split("\n")
        ts_i = next((i for i, ln in enumerate(raw) if _TS.search(ln)), None)
        if ts_i is None:
            continue
        m = _TS.search(raw[ts_i])
        start = _ts_to_ms(*m.group(1, 2, 3, 4))
        end = _ts_to_ms(*m.group(5, 6, 7, 8))
        idx = len(cues) + 1
        if ts_i > 0 and raw[ts_i - 1].strip().isdigit():
            idx = int(raw[ts_i - 1].strip())
        lines = [ln for ln in raw[ts_i + 1:] if ln.strip() != ""]
        cues.append(Cue(index=idx, start_ms=start, end_ms=end, lines=lines))
    return cues


def analyze_cues(cues: list[Cue]) -> ReadabilityReport:
    report = ReadabilityReport(cue_count=len(cues))
    for n, c in enumerate(cues):
        if c.line_count > MAX_LINES:
            report.issues.append(CueIssue(
                c.index, "lines", "warn",
                f"{c.line_count} lines (max {MAX_LINES})"))
        if c.max_cpl > MAX_CPL:
            report.issues.append(CueIssue(
                c.index, "cpl", "warn",
                f"{c.max_cpl} chars on a line (max {MAX_CPL})"))
        if c.duration_s > 0:
            if c.cps > CRITICAL_CPS:
                report.issues.append(CueIssue(
                    c.index, "cps", "critical",
                    f"{c.cps:.0f} chars/sec (critical > {CRITICAL_CPS:.0f})"))
            elif c.cps > MAX_CPS:
                report.issues.append(CueIssue(
                    c.index, "cps", "warn",
                    f"{c.cps:.0f} chars/sec (comfortable <= {MAX_CPS:.0f})"))
        if 0 < c.duration_s < MIN_DURATION_S:
            report.issues.append(CueIssue(
                c.index, "too_short", "warn",
                f"{c.duration_s:.2f}s on screen (min {MIN_DURATION_S:.2f}s)"))
        elif c.duration_s > MAX_DURATION_S:
            report.issues.append(CueIssue(
                c.index, "too_long", "warn",
                f"{c.duration_s:.1f}s on screen (max {MAX_DURATION_S:.0f}s)"))
        if n + 1 < len(cues) and c.end_ms > cues[n + 1].start_ms:
            report.issues.append(CueIssue(
                c.index, "overlap", "critical",
                f"overlaps cue {cues[n + 1].index}"))
    return report


def analyze_srt(text: str) -> ReadabilityReport:
    """Parse + analyse an SRT string in one call."""
    return analyze_cues(parse_srt(text))
