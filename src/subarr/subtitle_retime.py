"""#359: pure, base-agnostic SRT re-timer (the #171-immune CPS lever).

Extends each over-CPS cue's END forward into the available gap before the next
cue, and pads sub-min-duration micro-cues, clamped so it never overlaps the next
cue (min_gap preserved) or exceeds max_cue_ms. Never moves a start, never
shortens a cue, never worsens an existing overlap. Idempotent.

Operates purely on cue timestamps + text, so it is independent of how the cues
were produced (stable-ts regroup or the drop-stable-ts Netflix segmenter) — this
is the lever that survives the #171 crossing and is the eventual Path C
contribution. RetimeParams defaults are placeholders pending arena tuning (#359
follow-on)."""

from __future__ import annotations

from dataclasses import dataclass

from .subtitle_readability import Cue


@dataclass(frozen=True)
class RetimeParams:
    target_cps: float = 17.0  # extend toward this CPS (below MAX_CPS=20 comfort bar)
    min_cue_ms: int = 1000  # pad micro-cues to this (clears the 833ms too_short floor)
    min_gap_ms: int = 100  # inter-cue gap always preserved
    max_cue_ms: int = 7000  # never display longer than this (MAX_DURATION_S)


def retime_cues(cues: list[Cue], params: RetimeParams = RetimeParams()) -> list[Cue]:
    out: list[Cue] = []
    n = len(cues)
    for i, c in enumerate(cues):
        hard_cap = c.start_ms + params.max_cue_ms
        if i + 1 < n:
            bound = min(cues[i + 1].start_ms - params.min_gap_ms, hard_cap)
        else:
            bound = hard_cap
        # No room (gap too small, or the input already overlaps) → leave as-is.
        if bound <= c.end_ms:
            out.append(Cue(index=c.index, start_ms=c.start_ms, end_ms=c.end_ms, lines=list(c.lines)))
            continue
        desired = max(c.end_ms, c.start_ms + params.min_cue_ms)  # min-duration pad
        if params.target_cps > 0 and c.cps > params.target_cps:  # cps extension
            desired = max(desired, c.start_ms + round(c.char_count / params.target_cps * 1000))
        new_end = max(c.end_ms, min(desired, bound))  # clamp to gap/max; never shorten
        out.append(Cue(index=c.index, start_ms=c.start_ms, end_ms=new_end, lines=list(c.lines)))
    return out


def _ms_to_ts(ms: int) -> str:
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_srt(cues: list[Cue]) -> str:
    """Serialize cues to SRT text, re-indexed 1..N (the inverse of parse_srt)."""
    blocks = []
    for i, c in enumerate(cues, start=1):
        stamp = f"{_ms_to_ts(c.start_ms)} --> {_ms_to_ts(c.end_ms)}"
        blocks.append(f"{i}\n{stamp}\n" + "\n".join(c.lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""


def retime_srt(srt_text: str, params: RetimeParams = RetimeParams()) -> str:
    raise NotImplementedError  # Task 3
