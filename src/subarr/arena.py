"""#131 — tuning-lab arena orchestrator.

Drives a config sweep against the LIVE subgen model and ranks the outputs
with the validated tournament judge (`tournament_harness.judge_candidates`).

The flow, per run:
  1. Preflight — the runner checks the connected subgen can actually do this.
  2. Source transcript ONCE (`task="transcribe"`) → extract plain text. This
     is the shared source the QE/adequacy judge (#123) scores each candidate
     translation against.
  3. Candidates — for each config variant, run `task="translate"` with that
     variant's per-request `kwargs`.
  4. Judge — feed `{label → srt}` + source text + VAD speech ranges to
     `judge_candidates` → a ranked `TournamentResult`.

Everything subgen-facing is behind ONE injected seam, `CandidateRunner`, so
the orchestration logic is unit-testable without a real subgen.

The production runner is `AsrRunner` — it drives subgen's v4.10 `/asr` path-
input channel: subgen reads the media off the shared mount (no upload) and
returns the subtitle over HTTP (no shared writable scratch, no library
pollution, no output-isolation problem). That's why there's no scratch-dir /
filesystem machinery here at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .paths import canonical_to_fs
from .subtitle_readability import parse_srt
from .tournament import TournamentResult
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
    srt_text: str | None
    error: str | None = None


@dataclass
class ArenaResult:
    source_text: str | None
    outcomes: list[VariantOutcome]
    tournament: TournamentResult


class CandidateRunner(Protocol):
    """Seam for the arena. `prepare()` does one-time setup (auto-sampling) and
    returns the speech ranges the judge should use; `run()` produces one
    subtitle under a task+kwargs on the prepared sample; `cleanup()` releases
    any temp artifacts."""
    async def preflight(self) -> None: ...
    async def prepare(self, media_path: str) -> list[tuple[float, float]] | None: ...
    async def run(self, *, task: str, kwargs: dict[str, Any]) -> str | None: ...
    async def cleanup(self) -> None: ...


class AsrRunner:
    """Production runner over subgen's v4.10 `/asr` channel.

    `prepare()` auto-samples the file into a short strata clip (speech +
    boundary + silence — see arena_sampler) and every recipe is run on THAT
    clip via `/asr` upload mode: no whole-episode passes, no shared mount, and
    the clip's VAD ranges drive the judge's silence/coverage signals. Gate is
    enforced in `preflight()`.
    """
    def __init__(self, subgen, *, capabilities=None, source_language: str | None = None,
                 to_fs_path=canonical_to_fs, sampler=None):
        self._subgen = subgen
        self._caps = capabilities
        self._source_language = source_language
        self._to_fs_path = to_fs_path
        self._sampler = sampler  # injected for tests; default resolved lazily
        self._clip: str | None = None

    async def preflight(self) -> None:
        caps = self._caps if self._caps is not None else await self._subgen.probe_capabilities()
        if not getattr(caps, "asr_arena", False):
            raise ArenaUnsupported(
                "tuning-lab arena needs subarr-subgen >=v4.10 — /asr must "
                "advertise capabilities.asr_arena (path-input + per-request "
                "kwargs + HTTP return)"
            )

    async def prepare(self, media_path: str) -> list[tuple[float, float]] | None:
        sampler = self._sampler
        if sampler is None:
            from .arena_sampler import build_sample as sampler
        fs_path = str(self._to_fs_path(media_path))
        # ffmpeg/VAD are blocking CPU work — keep the event loop free.
        import asyncio
        self._clip, ranges = await asyncio.to_thread(sampler, fs_path)
        return ranges

    async def run(self, *, task: str, kwargs: dict[str, Any]) -> str | None:
        if not self._clip:
            raise RuntimeError("AsrRunner.run() called before prepare()")
        srt = await self._subgen.asr(
            local_file=self._clip, task=task,
            language=self._source_language, kwargs=kwargs or None,
        )
        return srt or None

    async def cleanup(self) -> None:
        if self._clip:
            import os
            try:
                os.remove(self._clip)
            except OSError:
                pass
            self._clip = None


def _srt_to_text(srt_text: str) -> str:
    """Join cue text the same way the tournament derives its QE hypothesis,
    so the source transcript and the candidate hyps are extracted identically."""
    return " ".join(c.text for c in parse_srt(srt_text))


async def run_arena(
    media_path: str,
    variants: list[ConfigVariant],
    *,
    runner: CandidateRunner,
    judge=judge_candidates,
    on_source=None,
    on_variant=None,
) -> ArenaResult:
    """Run one config sweep and return the ranked result. See module docstring.

    `on_source(text)` fires once the source transcript is ready; `on_variant(
    outcome)` fires after each variant completes. Both are optional sync hooks
    the run service uses to push live SSE progress without blocking the sweep.
    """
    if not variants:
        raise ValueError("run_arena needs at least one config variant")

    await runner.preflight()
    try:
        # 0. Auto-sample the file into a short strata clip; its VAD speech
        # ranges drive the judge's silence/coverage signals.
        speech_ranges = await runner.prepare(media_path)

        # 1. Source transcript (once) — task=transcribe, default config.
        source_srt = await runner.run(task="transcribe", kwargs={})
        source_text = _srt_to_text(source_srt) if source_srt else None
        if on_source is not None:
            on_source(source_text)

        # 2. Candidates — one translate per variant, SEQUENTIALLY (await each
        # before the next). subgen's /asr is a blocking one-at-a-time call and
        # its single worker serializes the GPU anyway, so concurrency only
        # risks later requests waiting past the client timeout (which judged
        # sweeps early with partial results). Sequential = the sweep finishes
        # only when every recipe is truly done. on_variant fires per recipe.
        outcomes: list[VariantOutcome] = []
        candidates: dict[str, str] = {}
        for v in variants:
            try:
                srt = await runner.run(task="translate", kwargs=v.kwargs)
                outcome = VariantOutcome(v.label, srt, None if srt else "no subtitle produced")
            except Exception as e:  # one bad variant must not sink the whole sweep
                outcome = VariantOutcome(v.label, None, error=str(e))
            outcomes.append(outcome)
            if outcome.srt_text:
                candidates[v.label] = outcome.srt_text
            if on_variant is not None:
                on_variant(outcome)

        # 3. Judge — the clip's VAD ranges make the silence/coverage signals fire.
        result = judge(candidates, speech_ranges=speech_ranges, source_text=source_text)
        return ArenaResult(source_text=source_text, outcomes=outcomes, tournament=result)
    finally:
        await runner.cleanup()
