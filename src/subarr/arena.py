"""#131 — tuning-lab arena orchestrator.

Drives a config sweep against the LIVE subgen model and ranks the outputs
with the validated tournament judge (`tournament_harness.judge_candidates`).

The flow, per run:
  1. Capability gate — needs subarr-subgen ≥v4.9 (per-request `kwargs` AND
     per-request `task` on POST /batch). Otherwise we can't isolate configs
     or transcribe-vs-translate per call, so we refuse loudly.
  2. Source transcript ONCE — stage the clip into an isolated scratch dir,
     `batch(task="transcribe")`, read the produced `.srt`, extract plain
     text. This is the shared source the QE/adequacy judge (#123) scores
     each candidate translation against.
  3. Candidates — for each config variant, stage the clip into its OWN
     scratch dir (so outputs never clobber each other or the real library
     sub), `batch(task="translate", kwargs=variant)`, read the `.srt`.
  4. Judge — feed `{label → srt}` + the source text + VAD speech ranges to
     `judge_candidates` → a ranked `TournamentResult`.

Everything subgen-facing is behind two injected seams so the orchestration
logic is unit-testable without a real subgen or filesystem:
  - `subgen`     — a SubgenClient (or anything with probe_capabilities/batch).
  - `workspace`  — an `ArenaWorkspace`: stages a clip into an isolated dir and
                   waits for the produced subtitle. The concrete filesystem +
                   path-mapping + queue-polling implementation lives there
                   (and is the part that needs live verification).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .subtitle_readability import parse_srt
from .tournament import TournamentResult
from .tournament_harness import judge_candidates

# Label used for the shared source-transcribe staging dir. Underscored so it
# can't collide with a user-supplied variant label.
SOURCE_LABEL = "__source__"


class ArenaUnsupported(RuntimeError):
    """The connected subgen can't run the arena (missing v4.9 capabilities)."""


@dataclass
class ConfigVariant:
    """One candidate Whisper config to trial. `kwargs` is the per-request
    SUBGEN_KWARGS override sent to /batch for this variant only."""
    label: str
    kwargs: dict[str, Any] = field(default_factory=dict)


@dataclass
class StagedClip:
    """A clip copied into an isolated scratch dir. `subgen_dir` is the
    directory path in SUBGEN's filesystem view (what we pass to /batch)."""
    label: str
    subgen_dir: str


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


class ArenaWorkspace(Protocol):
    """Isolation seam. Implementations stage a clip into a per-label scratch
    dir subgen can read+write and subarr can read, then wait for the produced
    subtitle to land."""
    async def stage(self, media_path: str, label: str) -> StagedClip: ...
    async def await_subtitle(self, clip: StagedClip, *, timeout_s: float = 600) -> str | None: ...
    async def cleanup(self) -> None: ...


def _srt_to_text(srt_text: str) -> str:
    """Join cue text the same way the tournament derives its QE hypothesis,
    so the source transcript and the candidate hyps are extracted identically."""
    return " ".join(c.text for c in parse_srt(srt_text))


async def run_arena(
    media_path: str,
    variants: list[ConfigVariant],
    *,
    subgen,
    workspace: ArenaWorkspace,
    capabilities=None,
    speech_ranges: list[tuple[float, float]] | None = None,
    source_language: str | None = None,
    judge=judge_candidates,
) -> ArenaResult:
    """Run one config sweep and return the ranked result. See module docstring."""
    if not variants:
        raise ValueError("run_arena needs at least one config variant")

    caps = capabilities if capabilities is not None else await subgen.probe_capabilities()
    if not (caps.has_batch and caps.per_request_kwargs and caps.per_request_task):
        raise ArenaUnsupported(
            "tuning-lab arena needs subarr-subgen >=v4.9 — POST /batch must "
            "advertise both per_request_kwargs and per_request_task "
            f"(saw kwargs={caps.per_request_kwargs}, task={caps.per_request_task}, "
            f"batch={caps.has_batch})"
        )

    # 1. Source transcript (once) — task=transcribe, default config.
    source_text: str | None = None
    src_clip = await workspace.stage(media_path, SOURCE_LABEL)
    await subgen.batch(src_clip.subgen_dir, task="transcribe", force_language=source_language)
    src_srt = await workspace.await_subtitle(src_clip)
    if src_srt:
        source_text = _srt_to_text(src_srt)

    # 2. Candidates — one isolated scratch dir + one kwargs variant each.
    outcomes: list[VariantOutcome] = []
    candidates: dict[str, str] = {}
    for v in variants:
        try:
            clip = await workspace.stage(media_path, v.label)
            await subgen.batch(clip.subgen_dir, task="translate", kwargs=v.kwargs)
            srt = await workspace.await_subtitle(clip)
        except Exception as e:  # one bad variant must not sink the whole sweep
            outcomes.append(VariantOutcome(v.label, None, error=str(e)))
            continue
        outcomes.append(VariantOutcome(v.label, srt, None if srt else "no subtitle produced"))
        if srt:
            candidates[v.label] = srt

    # 3. Judge.
    result = judge(candidates, speech_ranges=speech_ranges, source_text=source_text)
    return ArenaResult(source_text=source_text, outcomes=outcomes, tournament=result)
