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

from .subtitle_readability import CRITICAL_CPS, Cue, parse_srt


@dataclass(frozen=True)
class RetimeParams:
    target_cps: float = 17.0  # extend toward this CPS (below MAX_CPS=20 comfort bar)
    min_cue_ms: int = 1000  # pad micro-cues to this (clears the 833ms too_short floor)
    min_gap_ms: int = 100  # inter-cue gap always preserved
    max_cue_ms: int = 7000  # never display longer than this (MAX_DURATION_S)
    # [#445] Borrow at most this many ms from the NEXT cue's start to rescue an
    # over-CPS cue with no gap to grow into. 0 disables borrowing entirely.
    #
    # 500ms is the knee of the measured curve on the #171 Phase 2 corpus: it
    # takes our shipped output from 2.81% to 0.85% of cues over 25 CPS, about
    # 70% of the total available gain, while bounding the worst-case delay at
    # half a second. Uncapped reaches 0.55% but delays a cue by up to 1.9s,
    # which is its own defect and one the CPS metric cannot see.
    max_borrow_ms: int = 500


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
    return _borrow_from_next(out, params)


def _borrow_from_next(cues: list[Cue], params: RetimeParams) -> list[Cue]:
    """[#445] Lend an over-CPS cue time by starting the NEXT cue later.

    End-extension cannot help a cue whose neighbour begins immediately, and on
    the #171 Phase 2 corpus that was **every** surviving violation: 120 of 120
    were blocked by no gap, none by max_cue_ms. The median shortfall was 220ms
    while the median real gap to the next cue was 80ms.

    Only ever delays the next cue, never advances one -- text must never be
    displayed before its speech. Lends only what the neighbour can spare while
    staying under CRITICAL_CPS itself, so it relieves a violation rather than
    relocating it, and is bounded by ``max_borrow_ms``.
    """
    if params.max_borrow_ms <= 0 or len(cues) < 2:
        return cues

    out = list(cues)
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        if a.duration_s <= 0 or a.cps <= CRITICAL_CPS:
            continue
        # What A needs to reach the CRITICAL bar -- not target_cps. Borrowing is
        # ALL OR NOTHING against that bar, for two reasons.
        #
        # Idempotency: the module guarantees it. Borrowing toward target_cps
        # leaves A still above CRITICAL when the cap binds, so a second pass
        # borrows another max_borrow_ms, and a third, and so on. Stopping
        # exactly at CRITICAL means the next pass sees `a.cps <= CRITICAL_CPS`
        # and does nothing.
        #
        # Honesty about the trade: the cost here is paid by the VIEWER, in the
        # next cue arriving late. Paying it for a cue that stays unreadable
        # anyway buys nothing, so a borrow that cannot finish the job is not
        # made at all.
        want = (
            min(
                a.start_ms + round(a.char_count / CRITICAL_CPS * 1000),
                a.start_ms + params.max_cue_ms,
            )
            - a.end_ms
        )
        if want <= 0:
            continue
        # What B can give up and still clear the bar. Its own minimum is set by
        # CRITICAL_CPS, not target_cps -- we relieve A without creating a new
        # violation, rather than chasing comfort for A at B's expense.
        b_floor_ms = round(b.char_count / CRITICAL_CPS * 1000) if b.char_count else 0
        slack = (b.end_ms - b.start_ms) - b_floor_ms
        give = max(0, min(want, slack, params.max_borrow_ms))
        if give < want:
            continue  # cannot finish the job -- do not charge the viewer for it
        if give <= 0:
            continue
        out[i] = Cue(index=a.index, start_ms=a.start_ms, end_ms=a.end_ms + give, lines=list(a.lines))
        out[i + 1] = Cue(index=b.index, start_ms=b.start_ms + give, end_ms=b.end_ms, lines=list(b.lines))
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
    """Parse → re-time → re-render. The convenience entry point."""
    return render_srt(retime_cues(parse_srt(srt_text), params))
