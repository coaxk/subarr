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
from .subtitle_readability import parse_srt

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
# lid_fn(clip_path, subgen_clip_path|None, span) -> (lang|None, conf). subgen_path
# is threaded so the real subgen_lid can select Branch A (path detect) vs B (upload).
LidFn = Callable[
    [str, "str | None", "tuple[float, float]"],
    "Awaitable[tuple[str | None, float]] | tuple[str | None, float]",
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
        none, bailed, scanned, vad-unavailable, exists, error. NEVER raises — a
        traversal, disk, store, VAD or subgen failure is caught, LOGGED, recorded
        and returned so the walker / at-import hook can never crash (#416)."""
        try:
            return await self._run(canonical_path)
        except PathOutsideRootError:
            # Path-containment (#13): a traversal/unresolvable canonical writes
            # nothing outside the media root and is surfaced, not swallowed.
            log.warning("forced-segment: unresolvable/traversal canonical %s", canonical_path)
            return {"status": "error", "reason": "unresolvable", "n_spans": 0, "total_ms": 0}
        except Exception as e:  # noqa: BLE001 - best-effort: never crash the caller
            log.warning("forced-segment: process failed for %s: %s", canonical_path, e, exc_info=True)
            self._try_record_error(canonical_path)
            return {"status": "error", "reason": "exception", "n_spans": 0, "total_ms": 0}

    async def _run(self, canonical_path: str) -> dict:
        fs_path = canonical_to_fs(canonical_path)  # may raise PathOutsideRootError (handled above)
        if not fs_path.exists():
            return {"status": "error", "reason": "missing", "n_spans": 0, "total_ms": 0}

        # Cache identity: mtime AND size come from the SAME source (stat) so the
        # key is self-consistent across runs. The gate still resolves size but the
        # cache never mixes it with a stat mtime.
        st = fs_path.stat()
        mtime, size = st.st_mtime, st.st_size
        ok, reason, _dur, _gate_size = self._gate(canonical_path)

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
                canonical_path=canonical_path, mtime=mtime, size=size, status="exists", n_spans=0, total_ms=0
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

            cues = await self._build_cues(str(fs_path), spans, tmp)

        srt = build_forced_srt(cues)
        if not srt.strip():
            self._store.upsert(
                canonical_path=canonical_path, mtime=mtime, size=size, status="none", n_spans=0, total_ms=0
            )
            return {"status": "none", "n_spans": 0, "total_ms": 0}

        # Path-contained write (canonical_to_fs already guarded traversal); atomic
        # replace so a partial write is never observed as the sidecar, and the
        # scratch .tmp is cleaned up whether the replace succeeds or fails.
        tmp_out = sidecar.with_name(sidecar.name + ".tmp")
        try:
            tmp_out.write_text(srt, encoding="utf-8")
            os.replace(tmp_out, sidecar)
        finally:
            if tmp_out.exists():
                try:
                    tmp_out.unlink()
                except OSError:
                    pass
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
            # Thread subgen_path so the real subgen_lid can select Branch A (cheap
            # path detect) over Branch B (upload); a sync fake ignores it. Both
            # return shapes are normalised through _maybe_await.
            lang, conf = await _maybe_await(self._lid(clip, subgen_path, (s, e)))
            one = classify_utterances([(s, e)], lambda _u, _l=lang, _c=conf: (_l, _c), self._params)
            classified.append(one[0])
        return classified

    async def _build_cues(self, fs_path: str, spans: "list[Span]", tmp) -> list:
        """Translate each merged foreign span ONCE (the span clip) and unpack the
        TIMESTAMPED SRT subgen returns into SEPARATE cues, each offset to absolute
        file time by the span start. A long foreign scene therefore becomes many
        readable cues, never one unreadable multi-minute subtitle. Clipping is
        once per span (LID already clipped per utterance); a bad clip or empty
        translation drops that span, never the file."""
        cues: list[tuple[int, int, str]] = []
        for i, sp in enumerate(spans):
            clip = os.path.join(tmp, f"tr-{i}.wav")
            try:
                self._clip(fs_path, sp.start_ms / 1000.0, sp.end_ms / 1000.0, clip)
            except Exception as exc:  # noqa: BLE001 - one bad clip drops that span, never fatal
                log.warning("forced-segment: translate clip failed for span %s: %s", sp, exc)
                continue
            text = await _maybe_await(self._translate(clip, (sp.start_ms / 1000.0, sp.end_ms / 1000.0)))
            if not (text and text.strip()):
                continue
            for c in parse_srt(text):
                start_ms = sp.start_ms + int(c.start_ms)
                end_ms = sp.start_ms + int(c.end_ms)
                cues.append((start_ms, end_ms, "\n".join(c.lines)))
        cues.sort(key=lambda t: (t[0], t[1]))
        return cues

    def _to_subgen(self, clip_path: str) -> str | None:
        """Map a local clip path to a subgen-visible path (Branch A) when a
        scratch prefix is configured; else None (Branch B upload)."""
        if not self._subgen_scratch_prefix:
            return None
        return self._subgen_scratch_prefix.rstrip("/") + "/" + Path(clip_path).name

    def _try_record_error(self, canonical_path: str) -> None:
        """Record an 'error' verdict so an unexpected failure is surfaced in the
        cache, not silent. Best-effort — recording an error must never itself
        raise (the caller already logged the original)."""
        try:
            st = canonical_to_fs(canonical_path).stat()
            self._store.upsert(
                canonical_path=canonical_path,
                mtime=st.st_mtime,
                size=st.st_size,
                status="error",
                n_spans=0,
                total_ms=0,
            )
        except Exception:  # noqa: BLE001 - the error path must never raise
            pass

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
