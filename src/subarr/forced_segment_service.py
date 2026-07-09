"""#364 slice 1 — forced-segment orchestration + subgen adapters.

The adapters are the ONLY subgen-touching code. LID honours the Task 0 branch;
translate always uploads. The `ForcedSegmentGenerator` orchestrates the full
per-file pipeline (gate -> VAD -> per-utterance LID -> mostly-foreign bail BEFORE
merge -> merge foreign spans -> translate -> forced SRT -> path-contained,
no-clobber write -> scan-cache record -> aftercare note). VAD/clip/LID/translate
and the gate resolver are all injected so the pipeline is testable with fakes —
no audio, no ffmpeg, no real subgen.
"""

from __future__ import annotations

import inspect
import logging
import os
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from .arena import parse_robust_detect
from .forced_segment import (
    ForcedSegmentParams,
    Span,
    build_forced_srt,
    classify_utterances,
    clip_audio,
    detect_utterances,
    is_mostly_foreign,
    merge_foreign_spans,
)
from .paths import PathOutsideRootError, canonical_to_fs
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


# Injectable signatures (LID/translate take a clip path + the source span so the
# fake can key off the span; the real wiring binds subgen_lid/subgen_translate.
# Either a sync fake or an async adapter is accepted — see _maybe_await).
VadFn = Callable[..., "list[tuple[float, float]]"]
ClipFn = Callable[..., None]
LidFn = Callable[
    [str, "tuple[float, float]"], "Awaitable[tuple[str | None, float]] | tuple[str | None, float]"
]
TranslateFn = Callable[[str, "tuple[float, float]"], "Awaitable[str] | str"]
# gate_fn(canonical) -> (qualifies, reason, duration_s, size)
GateFn = Callable[[str], "tuple[bool, str, float | None, int | None]"]


async def _maybe_await(value):
    """Await the value if it is awaitable, else return it as-is. Lets the same
    orchestrator drive a sync fake (tests) and an async subgen adapter (real
    wiring) through one code path."""
    if inspect.isawaitable(value):
        return await value
    return value


class ForcedSegmentGenerator:
    def __init__(
        self,
        *,
        subgen,
        scan_store,
        params: ForcedSegmentParams | None = None,
        vad_fn: VadFn = detect_utterances,
        clip_fn: ClipFn = clip_audio,
        lid_fn: LidFn,
        translate_fn: TranslateFn,
        gate_fn: GateFn,
        aftercare_store=None,
        subgen_scratch_prefix: str | None = None,
    ):
        self._subgen = subgen
        self._store = scan_store
        self._params = params or ForcedSegmentParams()
        self._vad = vad_fn
        self._clip = clip_fn
        self._lid = lid_fn
        self._translate = translate_fn
        self._gate = gate_fn
        self._aftercare = aftercare_store
        # When set, clips are written under a subgen-visible scratch mount so LID
        # can use the cheap path-based detect_language_robust (Task 0 Branch A).
        self._subgen_scratch_prefix = subgen_scratch_prefix

    async def process(self, canonical_path: str) -> dict:
        """Run the full pipeline for one file. Returns a summary dict
        {status, reason?, n_spans, total_ms}. status is one of: cached, skipped,
        none, bailed, scanned, vad-unavailable, error. Never raises — records +
        returns (best-effort; the walker/at-import hook must never crash)."""
        try:
            fs_path = canonical_to_fs(canonical_path)
        except PathOutsideRootError:
            # Path-containment (#13): a traversal/unresolvable canonical writes
            # nothing outside the media root and is surfaced, not swallowed.
            log.warning("forced-segment: unresolvable/traversal canonical %s", canonical_path)
            return {"status": "error", "reason": "unresolvable", "n_spans": 0, "total_ms": 0}
        if not fs_path.exists():
            return {"status": "error", "reason": "missing", "n_spans": 0, "total_ms": 0}

        mtime = fs_path.stat().st_mtime
        # The gate resolves the authoritative (duration, size) from the stores;
        # the cache is keyed on that size so a repeat run is a stable hit.
        ok, reason, _dur, size = self._gate(canonical_path)

        # Idempotence: an unchanged (path, mtime, size) is never re-scanned.
        if self._store.get(canonical_path, mtime=mtime, size=size) is not None:
            return {"status": "cached", "n_spans": 0, "total_ms": 0}

        if not ok:
            return {"status": "skipped", "reason": reason, "n_spans": 0, "total_ms": 0}

        # No-clobber (defence in depth over the gate): a forced sidecar already on
        # disk is never overwritten — skip + record so it is not a silent no-op.
        sidecar = fs_path.with_name(fs_path.stem + ".forced.en.srt")
        if sidecar.exists():
            self._store.upsert(
                canonical_path=canonical_path, mtime=mtime, size=size, status="none", n_spans=0, total_ms=0
            )
            return {"status": "skipped", "reason": "existing_forced", "n_spans": 0, "total_ms": 0}

        utterances = self._vad(str(fs_path))
        if not utterances:
            # An empty utterance list is EITHER genuine silence OR VAD being
            # unavailable (no silero model / onnxruntime). #416: never a silent
            # no-op — distinguish, LOG, and record the verdict.
            from . import vad as _vad_mod

            if not _vad_mod.vad_available():
                log.warning(
                    "forced-segment: VAD unavailable for %s (silero model/onnxruntime missing) "
                    "— cannot scan, recorded vad-unavailable",
                    canonical_path,
                )
                self._store.upsert(
                    canonical_path=canonical_path,
                    mtime=mtime,
                    size=size,
                    status="vad-unavailable",
                    n_spans=0,
                    total_ms=0,
                )
                return {"status": "vad-unavailable", "reason": "vad-unavailable", "n_spans": 0, "total_ms": 0}
            log.info("forced-segment: no speech detected in %s — recorded none", canonical_path)
            self._store.upsert(
                canonical_path=canonical_path, mtime=mtime, size=size, status="none", n_spans=0, total_ms=0
            )
            return {"status": "none", "reason": "no_speech", "n_spans": 0, "total_ms": 0}

        with tempfile.TemporaryDirectory(prefix="forced-seg-") as tmp:
            classified = await self._classify(str(fs_path), utterances, tmp)
            # BAIL BEFORE MERGE: a mostly-foreign file must never collapse into
            # one giant "forced" span (that is exactly the case we reject).
            if is_mostly_foreign(classified, self._params):
                self._store.upsert(
                    canonical_path=canonical_path,
                    mtime=mtime,
                    size=size,
                    status="bailed",
                    n_spans=0,
                    total_ms=0,
                )
                log.info("forced-segment: %s is mostly-foreign — bailed (suspect audio lang)", canonical_path)
                return {"status": "bailed", "n_spans": 0, "total_ms": 0}

            spans = merge_foreign_spans(classified, self._params)
            if not spans:
                self._store.upsert(
                    canonical_path=canonical_path,
                    mtime=mtime,
                    size=size,
                    status="none",
                    n_spans=0,
                    total_ms=0,
                )
                return {"status": "none", "n_spans": 0, "total_ms": 0}

            cues = await self._build_cues(str(fs_path), classified, spans, tmp)

        srt = build_forced_srt(cues)
        if not srt.strip():
            self._store.upsert(
                canonical_path=canonical_path, mtime=mtime, size=size, status="none", n_spans=0, total_ms=0
            )
            return {"status": "none", "n_spans": 0, "total_ms": 0}

        # Path-contained write (canonical_to_fs already guarded traversal); atomic
        # replace so a partial write is never observed as the sidecar.
        tmp_out = sidecar.with_name(sidecar.name + ".tmp")
        tmp_out.write_text(srt, encoding="utf-8")
        os.replace(tmp_out, sidecar)
        total_ms = sum(s.duration_ms for s in spans)
        self._store.upsert(
            canonical_path=canonical_path,
            mtime=mtime,
            size=size,
            status="scanned",
            n_spans=len(spans),
            total_ms=total_ms,
        )
        self._record_aftercare_note(canonical_path, srt, spans, total_ms)
        log.info("forced-segment: wrote %s (%d scenes, %dms foreign)", sidecar.name, len(spans), total_ms)
        return {"status": "scanned", "n_spans": len(spans), "total_ms": total_ms}

    async def _classify(self, fs_path: str, utterances, tmp) -> list:
        classified = []
        for i, (s, e) in enumerate(utterances):
            clip = os.path.join(tmp, f"lid-{i}.wav")
            try:
                self._clip(fs_path, s, e, clip)
            except Exception as exc:  # noqa: BLE001 - a bad clip is over-flagged, never fatal
                log.warning("forced-segment: LID clip failed at %.1fs: %s", s, exc)
                classified.append(((s, e), True))  # over-flag on failure (completeness bias)
                continue
            subgen_path = self._to_subgen(clip)
            lang, conf = await self._lid_call(clip, subgen_path, (s, e))
            one = classify_utterances([(s, e)], lambda _u, _l=lang, _c=conf: (_l, _c), self._params)
            classified.append(one[0])
        return classified

    async def _lid_call(self, clip, subgen_path, span):
        # Real wiring passes an async adapter (which uses subgen_path to pick
        # Branch A/B); the test passes a sync lambda keyed off the span. Both are
        # normalised through _maybe_await.
        return await _maybe_await(self._lid(clip, span))

    async def _build_cues(self, fs_path: str, classified: list, spans: "list[Span]", tmp) -> list:
        """One cue per merged foreign span; its text is the translation of the
        constituent foreign utterances, joined. Slice 1 emits one cue per span
        (not per sub-cue), so a span that fuses two adjacent foreign utterances
        carries both lines."""
        foreign_utts = sorted([(s, e) for (s, e), is_f in classified if is_f])
        cues: list[tuple[int, int, str]] = []
        n = 0
        for sp in spans:
            span_s, span_e = sp.start_ms / 1000.0, sp.end_ms / 1000.0
            parts: list[str] = []
            for us, ue in foreign_utts:
                if us < span_s - 1e-6 or ue > span_e + 1e-6:
                    continue
                clip = os.path.join(tmp, f"tr-{n}.wav")
                n += 1
                try:
                    self._clip(fs_path, us, ue, clip)
                except Exception as exc:  # noqa: BLE001 - one bad clip drops that line, never fatal
                    log.warning("forced-segment: translate clip failed at %.1fs: %s", us, exc)
                    continue
                text = await _maybe_await(self._translate(clip, (us, ue)))
                if text and text.strip():
                    parts.append(_srt_text_to_line(text))
            if parts:
                cues.append((sp.start_ms, sp.end_ms, " ".join(parts)))
        return cues

    def _to_subgen(self, clip_path: str) -> str | None:
        """Map a local clip path to a subgen-visible path (Branch A) when a
        scratch prefix is configured; else None (Branch B upload)."""
        if not self._subgen_scratch_prefix:
            return None
        return self._subgen_scratch_prefix.rstrip("/") + "/" + Path(clip_path).name

    def _record_aftercare_note(
        self, canonical_path: str, srt: str, spans: "list[Span]", total_ms: int
    ) -> None:
        """Light surfacing: record the generated sidecar in aftercare with a
        distinct source. Best-effort — LOG on failure, never break the write
        (#416: don't swallow silently)."""
        store = getattr(self, "_aftercare", None)
        if store is None:
            return
        try:
            import time

            from .aftercare import evaluate_subtitle

            ev = evaluate_subtitle(srt, media_duration_s=None)
            store.record(
                canonical_path=canonical_path,
                completed_at=time.time(),
                evaluation=ev,
                source="forced-segment",
            )
        except Exception as e:  # noqa: BLE001 - aftercare note must never break generation
            log.warning("forced-segment aftercare note failed for %s: %s", canonical_path, e)


def _srt_text_to_line(text: str) -> str:
    """subgen /asr returns a full SRT for the clip; collapse its cue text into a
    single line for the merged span cue (slice 1 emits one cue per foreign span,
    not per sub-cue). Strips indices/timestamps."""
    lines = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.isdigit() or "-->" in s:
            continue
        lines.append(s)
    return " ".join(lines)
