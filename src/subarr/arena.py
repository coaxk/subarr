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

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .paths import canonical_to_fs, canonical_to_subgen_batch
from .subtitle_readability import parse_srt
from .tournament_harness import judge_candidates


class ArenaUnsupported(RuntimeError):
    """The connected subgen can't run the arena (missing the v4.10 /asr channel)."""


def parse_robust_detect(resp) -> dict | None:
    """Parse a subgen `/detect_language_robust` response into the RAW per-chunk
    distribution `resolve_source_language` consumes. Shared by the tuning-lab
    sweeps (`AsrRunner.detect_language`) and the #155 audit walker so both feed
    the classifier identical shapes.

    Returns the discriminating SHAPE (not a verdict): the caller tells apart
    unanimous (confident, can override a wrong tag), split-with-a-real-second-
    language (bilingual), and no-majority (Whisper confused → trust the tag) by
    looking at `unanimous` + `n_agreeing` + `languages_heard`, not a single ratio.

    Shape: {language(plurality|None), n_agreeing, n_total, unanimous,
            languages_heard:[iso...]}. Returns None when detection is
    unavailable/failed (no chunks voted)."""
    resp = resp or {}
    agg = resp.get("aggregate") or {}
    lang = agg.get("language")
    n_ag = int(agg.get("n_agreeing") or 0)
    n_tot = int(agg.get("n_total") or 0)
    heard = sorted({
        c.get("language") for c in (resp.get("chunks") or [])
        if c.get("language") and c.get("language") != "und"
    })
    if not n_tot:
        return None
    return {
        "language": lang if (lang and lang != "und") else None,
        "n_agreeing": n_ag,
        "n_total": n_tot,
        "unanimous": n_tot >= 2 and n_ag == n_tot,
        "languages_heard": heard,
    }


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
    tie: bool = False                 # top recipes within noise → no real winner
    source_language: str | None = None  # [#23] robustly-detected source lang (label + per-lang grouping)
    source_language_confidence: float | None = None  # [#23] fraction of chunks agreeing (None when set conservatively)
    # Raw Whisper per-chunk distribution; the SERVICE turns this + the file's
    # known audio tag into the final source_language + mislabel/mixed flags.
    audio_detect: dict | None = None
    explanation: str | None = None    # optional plain-language read (local ollama)


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

    # Tie: top two scoring recipes within noise → no recipe meaningfully beat
    # another (typical on easy audio where sane configs converge). Computed
    # here so the row, guidance, and explainer all agree.
    scored = [r for r in rows if r.clips_scored > 0]
    tie = len(scored) >= 2 and (scored[0].mean_composite - scored[1].mean_composite) < 2.0

    clip_winners = [c.winner for c in per_clip if c.winner]
    consistency = (sum(1 for w in clip_winners if w == winner) / len(clip_winners)
                   if winner and clip_winners else None)
    agrs = [c.agreement for c in per_clip if c.agreement is not None]
    agreement_mean = round(sum(agrs) / len(agrs), 3) if agrs else None
    confidence = _confidence(consistency, agreement_mean, len(per_clip))
    return (rows, winner, confidence,
            round(consistency, 2) if consistency is not None else None, agreement_mean, tie)


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
                 to_fs_path=canonical_to_fs, to_subgen=canonical_to_subgen_batch, sampler=None,
                 track: int = 0):
        self._subgen = subgen
        self._caps = capabilities
        self._source_language = source_language
        self._to_fs_path = to_fs_path
        self._to_subgen = to_subgen
        self._sampler = sampler
        self._track = track   # audio-stream ordinal to sweep (multi-track files)
        self._clips: list[dict] = []  # [{path, kind, ranges}]

    async def preflight(self) -> None:
        caps = self._caps if self._caps is not None else await self._subgen.probe_capabilities()
        self._caps = caps  # remember the resolved caps for base-mode selection in run()
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
        try:
            self._clips = await asyncio.to_thread(sampler, fs_path, track=self._track)
        except TypeError:
            # a sampler that predates the multi-track `track` kwarg → call it the
            # old way (only the default track 0 is reachable for it).
            self._clips = await asyncio.to_thread(sampler, fs_path)
        return [{"kind": c["kind"], "ranges": c["ranges"]} for c in self._clips]

    async def run(self, clip_idx: int, *, task: str, kwargs: dict[str, Any]) -> str | None:
        clip = self._clips[clip_idx]
        # Always test from a canonical VANILLA base when subgen supports it, so
        # results are a property of the recipe — not the operator's tuned env —
        # and comparable across users (federated #124). Old subgen → base=None,
        # which preserves the classic merge-on-global behaviour.
        base = "vanilla" if getattr(self._caps, "asr_vanilla_base", False) else None
        srt = await self._subgen.asr(
            local_file=clip["path"], task=task,
            language=self._source_language, kwargs=kwargs or None, base=base,
        )
        return srt or None

    async def detect_language(self, media_path: str):
        """[#23] Robust source-language detection via subgen /detect_language_robust
        (multi-chunk vote across the MIDDLE of the file — skips intro/credits/
        silence where Whisper hallucinates).

        Returns the RAW per-chunk distribution (not a verdict) so the caller can
        tell three very different situations apart — the discriminator is *how*
        the chunks agree, not a single ratio:
          - unanimous (all chunks one language) → confident; can override a
            wrong arr tag (e.g. The Ring tagged Danish, heard Dutch 3/3).
          - split with a real second language (e.g. 2 en / 1 sr) → BILINGUAL
            content (Besa: English detectives + Serbian crooks), not "English".
          - no majority (1/1/1) → Whisper confused; trust the tag instead.
        Returns None when detection is unavailable/failed.
        Shape: {language(plurality|None), n_agreeing, n_total, unanimous,
                languages_heard:[iso...]}."""
        if not getattr(self._caps, "robust_language_detection", False):
            return None
        try:
            resp = await self._subgen.detect_language_robust(self._to_subgen(media_path))
        except Exception:
            return None
        return parse_robust_detect(resp)

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

            # The judge is heavy (QE = a torch/LaBSE forward pass, ~6s on first
            # load then ~0.1s) — run it off the event loop so the model load
            # can't block the app's SSE/HTTP work.
            res = await asyncio.to_thread(
                judge, candidates, speech_ranges=clip.get("ranges"), source_text=source_text)
            per_clip.append(ClipResult(
                kind=clip.get("kind", "?"), winner=res.winner_label,
                agreement=res.clip_agreement,
                scorecards=[asdict(sc) for sc in res.scorecards],
            ))

        outcomes = [VariantOutcome(v.label, "" if produced[v.label] else None,
                                   None if produced[v.label] else "no subtitle produced on any clip")
                    for v in variants]
        rows, winner, confidence, consistency, agreement_mean, tie = _aggregate(per_clip, variants)
        # [#23] robust source-language detection (per-chunk distribution).
        # Conservative standalone verdict: only assert a language when the chunks
        # are UNANIMOUS. The service refines this with the file's known tag
        # (mislabel override / bilingual detection / tag fallback).
        detect = None
        if hasattr(runner, "detect_language"):
            try:
                detect = await runner.detect_language(media_path)
            except Exception:
                detect = None
        src_lang, src_conf = None, None
        if detect and detect.get("unanimous") and detect.get("language"):
            src_lang = detect["language"]
            n_tot = detect.get("n_total") or 0
            src_conf = round(detect.get("n_agreeing", 0) / n_tot, 2) if n_tot else None
        return ArenaResult(
            outcomes=outcomes, aggregate=rows, per_clip=per_clip,
            winner=winner, confidence=confidence,
            consistency=consistency, agreement_mean=agreement_mean, tie=tie,
            source_language=src_lang, source_language_confidence=src_conf,
            audio_detect=detect,
        )
    finally:
        await runner.cleanup()
