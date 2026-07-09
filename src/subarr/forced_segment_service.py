"""#364 slice 1 — forced-segment orchestration + subgen adapters.

The adapters are the ONLY subgen-touching code. LID honours the Task 0 branch;
translate always uploads. Both are async and injected into the generator so the
detector and orchestration stay testable with fakes.
"""

from __future__ import annotations

import logging

from .arena import parse_robust_detect
from .subgen_client import SubgenClient, SubgenUnavailable

log = logging.getLogger(__name__)

# DECISION (controller, 2026-07-09): Branch B (LID via /asr upload) is an honest
# but degraded fallback — it transcribes every utterance instead of the cheap
# path-based detect. When it is used we emit ONE cost WARNING (module-level guard
# so it fires once per process, not once per clip). Branch A (a subgen-visible
# scratch dir) never warns.
_warned_branch_b = False
_BRANCH_B_WARNING = (
    "forced-segment: LID is using the /asr upload path — this transcribes every "
    "utterance and is much slower than the cheap detect path. Configure a "
    "subgen-visible scratch dir (SUBARR_FORCED_SEGMENT_SCRATCH_SUBGEN) for the "
    "fast path, or wait for the local-LID upgrade (#364 slice 2)."
)


async def subgen_lid(
    subgen: SubgenClient, clip_path: str, *, subgen_clip_path: str | None
) -> "tuple[str | None, float]":
    """Language-ID one utterance clip -> (lang|None, confidence 0..1).

    Branch A (subgen_clip_path given — the clip is on a subgen-visible mount):
    detect_language_robust(path=...) — cheapest, the spec's tier-1 choice.
    Confidence = n_agreeing / n_total from the robust aggregate.

    Branch B (no shared path): upload via asr(task='transcribe',
    return_language=True) and read the detected-language header; /asr returns no
    probability, so confidence is 1.0 and the detector's over-flag bias covers
    the uncertainty. Emits a one-time cost WARNING. Returns (None, 0.0) when
    subgen could not decide."""
    global _warned_branch_b
    try:
        if subgen_clip_path is not None:
            resp = await subgen.detect_language_robust(subgen_clip_path)
            d = parse_robust_detect(resp)
            if not d or not d.get("language"):
                return None, 0.0
            n_tot = int(d.get("n_total") or 0)
            n_ag = int(d.get("n_agreeing") or 0)
            conf = (n_ag / n_tot) if n_tot else 0.0
            return d["language"], conf
        if not _warned_branch_b:
            _warned_branch_b = True
            log.warning(_BRANCH_B_WARNING)
        text, lang = await subgen.asr(local_file=clip_path, task="transcribe", return_language=True)
        return (lang or None), (1.0 if lang else 0.0)
    except SubgenUnavailable as e:
        log.warning("forced-segment LID failed for a clip: %s", e)
        return None, 0.0


async def subgen_translate(subgen: SubgenClient, clip_path: str) -> str:
    """Transcribe+translate one foreign span clip to English text (uploads the
    clip; no shared fs needed). Returns '' if subgen produced nothing."""
    try:
        return await subgen.asr(local_file=clip_path, task="translate") or ""
    except SubgenUnavailable as e:
        log.warning("forced-segment translate failed for a clip: %s", e)
        return ""
