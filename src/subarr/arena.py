"""#131 — tuning-lab arena orchestrator (multi-clip, aggregated).

Implements the validated Tier-B methodology exactly: a config is only
trustworthy if it wins ACROSS SEPARATE clips, not on one. So per sweep we:

  1. Preflight — runner checks the connected subgen can do this.
  2. Auto-sample the file into N SEPARATE strata clips (dense-speech +
     speech→silence boundary + a silence/music stretch). The silence stratum
     is essential — on clean dialogue every config near-ties; they only
     separate on hallucination-bait.
  3. For EACH clip independently: source-transcribe once, run every recipe,
     judge with `judge_candidates` (the clip's VAD ranges drive the silence/
     coverage signals).
  4. AGGREGATE across clips: mean composite per recipe, cross-clip winner
     consistency, and mean `clip_agreement` → a confidence read. The honest
     output is "top pick + what to avoid + how much to trust it", NOT a
     trophy (single-clip winners are noise; the judge is a failure-mode
     detector, only a rough accuracy ranker — ρ~0.46).

subgen is behind ONE injected seam, `CandidateRunner`. The production runner
`AsrRunner` cuts the clips, then runs every recipe on each via subgen's v4.10
`/asr` UPLOAD mode (short clip → tiny upload, no shared mount).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .paths import canonical_to_fs
from .subtitle_readability import parse_srt
from .tournament_harness import judge_candidates


class ArenaUnsupported(RuntimeError):
    """The connected subgen can't run the arena (missing the v4.10 /asr channel)."""


@dataclass
class ConfigVariant:
    """One candidate Whisper config to trial. `kwargs` is the per-request
    SUBGEN_KWARGS override sent for this variant only."""
    label: str
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class VariantOutcome:
    label: str
    srt_text: str | None       # "" if it produced a sub on some clip; None if never
    error: str | None = None


@dataclass
class ClipResult:
    kind: str                  # speech | boundary | silence | fallback
    winner: str | None
    agreement: float | None    # clip_agreement (cross-config vocab overlap)
    scorecards: list[dict]     # serialized Scorecard per recipe


@dataclass
class AggregateRow:
    label: str
    mean_composite: float
    mean_qe: float | None
    clips_won: int             # clips this recipe topped
    clips_scored: int          # clips it produced a usable sub on
    disqualified_in: int       # clips it was disqualified on


@dataclass
class ArenaResult:
    outcomes: list[VariantOutcome]    # per-recipe produced-or-not (live progress)
    aggregate: list[AggregateRow]     # ranked best-first by mean composite
    per_clip: list[ClipResult]
    winner: str | None                # top mean composite (or None)
    confidence: str | None            # high | moderate | low
    consistency: float | None         # fraction of clips the winner also topped
    agreement_mean: float | None      # mean clip_agreement across clips


def _confidence(consistency: float | None, agreement_mean: float | None, n_clips: int) -> str:
    """Calibrated against Tier-B: trust needs the winner to hold across clips
    AND decent cross-config agreement (European ~0.75–0.81 trust; CJK ~0.57–
    0.64 flag). One clip can never be 'high'."""
    if n_clips < 2 or consistency is None:
        return "low"
    agr = agreement_mean if agreement_mean is not None else 0.0
    if consistency >= 0.67 and agr >= 0.70:
        return "high"
    if consistency >= 0.50 and agr >= 0.60:
        return "moderate"
    return "low"


def _aggregate(per_clip: list[ClipResult], variants: list[ConfigVariant]):
    """Cross-clip aggregation → (ranked rows, winner, confidence, consistency,
    agreement_mean). A recipe with no sub on a clip scores 0 there (a config
    that silently drops out is worse, not absent)."""
    rows: list[AggregateRow] = []
    for v in variants:
        comps: list[float] = []
        qes: list[float] = []
        won = scored = dq = 0
        for c in per_clip:
            sc = next((s for s in c.scorecards if s.get("entrant_label") == v.label), None)
            if sc is None:
                comps.append(0.0)
                continue
            comps.append(sc.get("composite") or 0.0)
            if sc.get("qe_adequacy") is not None:
                qes.append(sc["qe_adequacy"])
            if sc.get("disqualified"):
                dq += 1
            else:
                scored += 1
            if c.winner == v.label:
                won += 1
        rows.append(AggregateRow(
            label=v.label,
            mean_composite=round(sum(comps) / len(comps), 2) if comps else 0.0,
            mean_qe=round(sum(qes) / len(qes), 4) if qes else None,
            clips_won=won, clips_scored=scored, disqualified_in=dq,
        ))
    rows.sort(key=lambda r: r.mean_composite, reverse=True)
    winner = rows[0].label if rows and rows[0].mean_composite > 0 else None

    clip_winners = [c.winner for c in per_clip if c.winner]
    consistency = (sum(1 for w in clip_winners if w == winner) / len(clip_winners)
                   if winner and clip_winners else None)
    agrs = [c.agreement for c in per_clip if c.agreement is not None]
    agreement_mean = round(sum(agrs) / len(agrs), 3) if agrs else None
    confidence = _confidence(consistency, agreement_mean, len(per_clip))
    return (rows, winner, confidence,
            round(consistency, 2) if consistency is not None else None, agreement_mean)


class CandidateRunner(Protocol):
    """Seam. `prepare()` auto-samples → returns one descriptor per clip
    ({kind, ranges}); `run(clip_idx, ...)` produces a subtitle for that clip;
    `cleanup()` releases temp clips."""
    async def preflight(self) -> None: ...
    async def prepare(self, media_path: str) -> list[dict]: ...
    async def run(self, clip_idx: int, *, task: str, kwargs: dict[str, Any]) -> str | None: ...
    async def cleanup(self) -> None: ...


class AsrRunner:
    """Production runner: auto-samples N strata clips, runs each recipe on each
    clip via subgen's v4.10 `/asr` UPLOAD mode (no shared mount). Gate enforced
    in `preflight()`."""
    def __init__(self, subgen, *, capabilities=None, source_language: str | None = None,
                 to_fs_path=canonical_to_fs, sampler=None):
        self._subgen = subgen
        self._caps = capabilities
        self._source_language = source_language
        self._to_fs_path = to_fs_path
        self._sampler = sampler
        self._clips: list[dict] = []  # [{path, kind, ranges}]

    async def preflight(self) -> None:
        caps = self._caps if self._caps is not None else await self._subgen.probe_capabilities()
        if not getattr(caps, "asr_arena", False):
            raise ArenaUnsupported(
                "tuning-lab arena needs subarr-subgen >=v4.10 — /asr must "
                "advertise capabilities.asr_arena (per-request kwargs + HTTP return)"
            )

    async def prepare(self, media_path: str) -> list[dict]:
        sampler = self._sampler
        if sampler is None:
            from .arena_sampler import build_samples as sampler
        fs_path = str(self._to_fs_path(media_path))
        import asyncio
        self._clips = await asyncio.to_thread(sampler, fs_path)
        return [{"kind": c["kind"], "ranges": c["ranges"]} for c in self._clips]

    async def run(self, clip_idx: int, *, task: str, kwargs: dict[str, Any]) -> str | None:
        clip = self._clips[clip_idx]
        srt = await self._subgen.asr(
            local_file=clip["path"], task=task,
            language=self._source_language, kwargs=kwargs or None,
        )
        return srt or None

    async def cleanup(self) -> None:
        import os
        for c in self._clips:
            try:
                os.remove(c["path"])
            except OSError:
                pass
        self._clips = []


def _srt_to_text(srt_text: str) -> str:
    return " ".join(c.text for c in parse_srt(srt_text))


async def run_arena(
    media_path: str,
    variants: list[ConfigVariant],
    *,
    runner: CandidateRunner,
    judge=judge_candidates,
    on_clip=None,    # on_clip(idx, kind, total) — a clip's passes are starting
    on_step=None,    # on_step() — one transcription (source or recipe) finished
) -> ArenaResult:
    """Run one multi-clip sweep and return the aggregated result."""
    if not variants:
        raise ValueError("run_arena needs at least one config variant")

    await runner.preflight()
    try:
        clips = await runner.prepare(media_path)
        per_clip: list[ClipResult] = []
        produced = {v.label: False for v in variants}

        for ci, clip in enumerate(clips):
            if on_clip is not None:
                on_clip(ci, clip.get("kind", "?"), len(clips))
            source_srt = await runner.run(ci, task="transcribe", kwargs={})
            source_text = _srt_to_text(source_srt) if source_srt else None
            if on_step is not None:
                on_step()

            candidates: dict[str, str] = {}
            for v in variants:
                try:
                    srt = await runner.run(ci, task="translate", kwargs=v.kwargs)
                except Exception:  # one bad recipe on one clip must not sink the sweep
                    srt = None
                if srt:
                    candidates[v.label] = srt
                    produced[v.label] = True
                if on_step is not None:
                    on_step()

            res = judge(candidates, speech_ranges=clip.get("ranges"), source_text=source_text)
            per_clip.append(ClipResult(
                kind=clip.get("kind", "?"), winner=res.winner_label,
                agreement=res.clip_agreement,
                scorecards=[asdict(sc) for sc in res.scorecards],
            ))

        outcomes = [VariantOutcome(v.label, "" if produced[v.label] else None,
                                   None if produced[v.label] else "no subtitle produced on any clip")
                    for v in variants]
        rows, winner, confidence, consistency, agreement_mean = _aggregate(per_clip, variants)
        return ArenaResult(
            outcomes=outcomes, aggregate=rows, per_clip=per_clip,
            winner=winner, confidence=confidence,
            consistency=consistency, agreement_mean=agreement_mean,
        )
    finally:
        await runner.cleanup()
