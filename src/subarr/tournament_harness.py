"""#65 — Tier-B tournament harness.

Turns the validated judge (`tournament.run_tournament`) into a runnable
comparison over REAL candidate SRTs for one source clip. Candidates can come
from anywhere — a subgen config sweep, existing library subtitles, whatever —
the harness just judges and ranks them, optionally using the clip's silero
VAD speech ranges (#111) so the text-over-silence hallucination judge fires.

CLI:
    python -m subarr.tournament_harness --srt-dir ./candidates \
        [--speech-ranges ranges.json]   # JSON: [[start,end], ...]

Each *.srt in the dir is one entrant (labelled by filename stem). Prints a
ranked scorecard with the QE signals that decided it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .tournament import Entrant, TournamentResult, run_tournament


def judge_candidates(
    candidates: dict[str, str],
    speech_ranges: list[tuple[float, float]] | None = None,
    gen_times: dict[str, float] | None = None,
    source_text: str | None = None,
) -> TournamentResult:
    """Rank candidate SRTs (label → srt_text) with the validated judge.

    `source_text` is the SHARED source-language transcript of the clip (same
    audio for every entrant); when given, the QE/adequacy judge (#123) fires."""
    gen_times = gen_times or {}
    entrants = [
        Entrant(
            label=label,
            srt_text=srt,
            speech_ranges=speech_ranges,
            gen_time_s=gen_times.get(label),
            source_text=source_text,
        )
        for label, srt in candidates.items()
    ]
    return run_tournament(entrants)


def load_candidates(srt_dir) -> dict[str, str]:
    """Load every *.srt in a directory, labelled by filename stem."""
    d = Path(srt_dir)
    return {p.stem: p.read_text(encoding="utf-8", errors="replace") for p in sorted(d.glob("*.srt"))}


def format_result(result: TournamentResult) -> str:
    lines = [
        f"WINNER: {result.winner_label or '(none — all disqualified)'}",
        "",
        f"{'rank':<5}{'entrant':<24}{'composite':>10}   signals",
        "-" * 72,
    ]
    for i, sc in enumerate(result.scorecards, 1):
        sig = sc.signals or {}
        sigtxt = (
            f"silence={sig.get('silence_text_ratio', '-')} "
            f"repeat={sig.get('repeated_line_ratio', '-')} "
            f"canned={sig.get('canned_phrase_hits', '-')}"
        )
        flag = "  [DISQUALIFIED]" if sc.disqualified else ""
        lines.append(f"{i:<5}{sc.entrant_label:<24}{sc.composite:>10.2f}   {sigtxt}{flag}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tier-B tournament: rank candidate SRTs for one clip.")
    ap.add_argument("--srt-dir", required=True, help="directory of candidate *.srt files")
    ap.add_argument("--speech-ranges", help="JSON file of [[start,end],...] VAD speech ranges (s)")
    args = ap.parse_args(argv)

    candidates = load_candidates(args.srt_dir)
    if not candidates:
        print(f"No *.srt candidates found in {args.srt_dir}")
        return 1
    ranges = None
    if args.speech_ranges:
        ranges = [tuple(r) for r in json.loads(Path(args.speech_ranges).read_text("utf-8"))]
    result = judge_candidates(candidates, speech_ranges=ranges)
    print(format_result(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
