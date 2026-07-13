"""#155 phase 2 — library-wide audio-language audit walker.

A throttled, opt-in, resumable background pass that runs subgen's robust
language detection over files the tuning lab NEVER swept, so the audio-language
audit covers the whole library — not just what happened to be swept (phase 1).

Design (mirrors probe_walker's lifecycle, but single-flight + GPU-polite):

  * OPT-IN — nothing runs until `start()` is called. The walker is created at
    boot but idle.
  * THROTTLED — one detect at a time, with `_PER_FILE_SLEEP_S` between files, so
    a full-library audit trickles instead of a GPU storm.
  * GPU-POLITE — `busy_check()` reports when live tuning-lab sweeps are running.
    While busy, the walker sleeps `_BUSY_SLEEP_S` and fires NO detect, yielding
    the GPU to the user-facing sweeps. It resumes on its own when they finish.
  * RESUMABLE — each file's verdict is persisted with its mtime. On a re-run (or
    after a restart) a file whose stored mtime still matches on disk is skipped,
    so an interrupted audit picks up where it left off and unchanged files aren't
    re-detected.
  * CLASSIFIES via the SAME `resolve_source_language` the sweeps use, so the
    audit's verdicts and the per-sweep flags stay coherent.

The classification is pure reuse: `parse_robust_detect(resp)` shapes the subgen
response, then `resolve_source_language(detect, tag, None, multitrack=...)` makes
the call. We only DERIVE a status string from its (mixed, mislabel) flags +
multitrack + whether anything was detected.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .arena import parse_robust_detect
from .arena_service import resolve_source_language
from .log_safe import scrub
from .paths import canonical_to_subgen_batch

log = logging.getLogger(__name__)

# One detect at a time + this gap between files: the audit is a background
# trickle, never a burst. ~1s lets the dashboard/SSE polls breathe between the
# heavy (multi-chunk Whisper) detects.
_PER_FILE_SLEEP_S = 1.0
# When live sweeps are running, back off harder and fire NO detect — the GPU
# belongs to the user-facing tuning lab.
_BUSY_SLEEP_S = 5.0

# Tier 2 feedback: a unanimous mislabel writes a `whisper-robust` audio-lang
# verification so coverage + the override gate can use it (without it being
# treated as user ground truth). Non-risky languages get a confidence ABOVE the
# override gate's 0.5 floor (informs decisions); risky non-Latin scripts get one
# BELOW it (shows in coverage, but a wrong auto-guess can't change output until
# the user confirms).
_RISKY_LANGS = {"ja", "ko", "zh"}
_TIER2_CONF = 0.7
_TIER2_CONF_RISKY = 0.45


@dataclass
class AuditState:
    status: str = "running"  # running | done | cancelled | error
    total: int = 0
    processed: int = 0  # files visited (incl. resumed-skips)
    found: int = 0  # actionable findings (mislabel/bilingual/multitrack/multilingual)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str | None = None
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total": self.total,
            "processed": self.processed,
            "found": self.found,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "errors": self.errors[:10],
        }


def _derive_status(
    detect: dict | None,
    mixed: bool,
    mislabel: bool,
    multitrack: bool,
    multilingual_langs: list[str] | None = None,
) -> str:
    """Map the classifier's flags onto an audit bucket. Precedence:
    multitrack (the file is genuinely multi-track — the detect only saw one
    stream) → mislabel (Whisper unanimously disagrees with the tag) → bilingual
    (a real second language heard) → multilingual (#357: ≥2 HIGH-confidence
    distinct chunk languages, e.g. The Beasts) → undetermined (nothing detected)
    → confused (detected, but no agreement and no tag mismatch) → agrees."""
    if multitrack:
        return "multitrack"
    if mislabel:
        return "mislabel"
    if mixed:
        return "bilingual"
    # #357: a would-be-confused split that is actually ≥2 HIGH-confidence distinct
    # languages is multilingual (The Beasts), not confusion. Placed after the
    # bilingual heuristic (a mixed 2-of-3 stays bilingual) but ahead of
    # undetermined/confused so it is caught even when there is no plurality.
    if multilingual_langs and len(multilingual_langs) >= 2:
        return "multilingual"
    if not detect or not detect.get("language"):
        return "undetermined"
    if not detect.get("unanimous") and int(detect.get("n_agreeing") or 0) < 2:
        return "confused"
    return "agrees"


class AudioAuditWalker:
    def __init__(
        self,
        subgen,
        audit_store,
        *,
        worklist: Callable[..., list],
        probe_store=None,
        busy_check: Callable[[], bool] | None = None,
        audio_lang=None,
        to_subgen: Callable[[str], str] = canonical_to_subgen_batch,
    ):
        self._subgen = subgen
        self._store = audit_store
        # worklist(scope) -> [(canonical_path, tag_lang, mtime), ...]. Resolved
        # lazily at start() so it reflects the latest coverage snapshot. Legacy
        # zero-arg worklists are supported via a TypeError fallback.
        self._worklist = worklist
        self._probe_store = probe_store
        # busy_check() -> True when live sweeps are running (yield the GPU).
        self._busy_check = busy_check
        # audio_lang verification store — Tier 2 feedback writes here.
        self._audio_lang = audio_lang
        self._to_subgen = to_subgen
        self._state: AuditState | None = None
        self._task: asyncio.Task | None = None

    def get_state(self) -> AuditState | None:
        return self._state

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _resolve_worklist(self, scope: str) -> list:
        # Scope-aware worklist with a fallback for legacy zero-arg callables
        # (the test fakes). Runs OFF the event loop (see start()) because the
        # 'library' scope walks the whole media root + stats every file.
        try:
            return list(self._worklist(scope) or [])
        except TypeError:
            return list(self._worklist() or [])

    async def start(self, scope: str = "coverage") -> AuditState:
        if self.is_running():
            raise RuntimeError("audio-language audit already running")
        try:
            # Resolve off-thread: coverage scope stats hundreds of files and
            # library scope walks the entire media root — neither should block
            # the event loop during the start request.
            worklist = await asyncio.to_thread(self._resolve_worklist, scope)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("audio-audit: worklist resolution failed: %s", e)
            worklist = []
        state = AuditState(total=len(worklist))
        self._state = state
        self._task = asyncio.create_task(self._run(state, worklist), name="audio-audit")
        return state

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def aclose(self) -> None:
        await self.stop()

    # ── internals ─────────────────────────────────────────────────────────
    def _track_langs(self, canonical_path: str) -> list:
        """Ordered, de-duplicated audio-track languages from probe_store ffprobe
        streams (e.g. ['de', 'ru']). Drives both the multi-track verdict (≥2
        distinct) AND the per-track display, so the UI shows the actual track
        languages instead of what was heard in the one track we listened to."""
        if self._probe_store is None:
            return []
        try:
            pr = self._probe_store.get(canonical_path)
        except Exception:
            return []
        out = []
        for a in getattr(pr, "audio", None) or []:
            code = (getattr(a, "language", None) or "").strip().lower()
            if code and code != "und" and code not in out:
                out.append(code)
        return out

    async def _run(self, state: AuditState, worklist: list) -> None:
        try:
            for entry in worklist:
                canonical_path, tag_lang, mtime = entry

                # GPU-polite: while live sweeps run, back off and fire NO detect.
                # Re-check after sleeping so we resume promptly when they finish.
                while self._busy_check is not None and self._safe_busy():
                    await asyncio.sleep(_BUSY_SLEEP_S)

                # Resumable: skip a file already audited at the same mtime.
                existing = None
                try:
                    existing = self._store.get(canonical_path)
                except Exception:
                    existing = None
                if (
                    existing is not None
                    and mtime is not None
                    and existing.mtime is not None
                    and abs(existing.mtime - mtime) <= 1
                ):
                    state.processed += 1
                    continue

                try:
                    await self._audit_one(state, canonical_path, tag_lang, mtime)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    state.errors.append({"path": canonical_path, "error": repr(e)})
                finally:
                    state.processed += 1

                # Throttle: trickle, never burst.
                await asyncio.sleep(_PER_FILE_SLEEP_S)

            state.status = "done"
            state.finished_at = time.time()
            log.info(
                "audio-audit done: %d files, %d findings, %d errors",
                state.processed,
                state.found,
                len(state.errors),
            )
            # #157 gap-fill: RUN-level supervision. A clean full run records a
            # success on the unified Health roster. Best-effort (getattr guard),
            # mirroring the 7 supervised loops — a missing store never fails a run.
            _h = getattr(self, "_health", None)
            if _h:
                _h.record_success("audio-audit")
        except asyncio.CancelledError:
            state.status = "cancelled"
            state.finished_at = time.time()
            raise
        except Exception as e:
            log.exception("audio-audit failed: %s", e)
            state.status = "error"
            state.error = repr(e)
            state.finished_at = time.time()
            # #157 gap-fill: a RUN-level crash (the outer drive, not a per-file
            # error) surfaces on the Health roster. Best-effort.
            _h = getattr(self, "_health", None)
            if _h:
                _h.record_failure("audio-audit", e)

    def _safe_busy(self) -> bool:
        try:
            return bool(self._busy_check())
        except Exception:
            return False

    async def _audit_one(
        self, state: AuditState, canonical_path: str, tag_lang: str | None, mtime: float | None
    ) -> None:
        resp = await self._subgen.detect_language_robust(self._to_subgen(canonical_path))
        detect = parse_robust_detect(resp)
        track_langs = self._track_langs(canonical_path)
        multitrack = len(track_langs) >= 2
        lang, _src, mixed, mislabel = resolve_source_language(detect, tag_lang, None, multitrack=multitrack)
        # #357: recognise a confident-multilingual file (>=2 high-conf distinct
        # chunk languages) from the per-chunk probabilities, so The Beasts is
        # classified 'multilingual' rather than the false 'confused'/suspect.
        from .config import settings
        from .multilang import classify_high_conf_langs

        multilingual_langs = classify_high_conf_langs(
            (detect or {}).get("chunks_conf") or [], settings.multilang_chunk_min_prob
        )
        status = _derive_status(detect, mixed, mislabel, multitrack, multilingual_langs=multilingual_langs)
        detected_lang = (detect or {}).get("language")
        det = detect or {}
        self._store.upsert(
            canonical_path=canonical_path,
            tag_lang=tag_lang,
            detected_lang=detected_lang,
            status=status,
            languages_heard=list(det.get("languages_heard") or []),
            n_agreeing=det.get("n_agreeing"),
            n_total=det.get("n_total"),
            mtime=mtime,
            track_languages=track_langs,
            chunks_conf=(detect or {}).get("chunks_conf"),
        )
        if status in ("mislabel", "bilingual", "multitrack", "multilingual"):
            state.found += 1
        # Tier 2 feedback: a unanimous mislabel is high-confidence ground that
        # subarr HEARD a different language than the tag. Write a `whisper-robust`
        # verification so coverage shows it and the override gate can act —
        # NEVER as `user` truth, so it stays visible for the user to confirm and
        # never silently parrots a wrong auto-guess (risky scripts go sub-gate).
        if status == "mislabel" and detected_lang and self._audio_lang is not None:
            conf = _TIER2_CONF_RISKY if detected_lang in _RISKY_LANGS else _TIER2_CONF
            try:
                self._audio_lang.upsert(
                    canonical_path=(canonical_path or "").lstrip("/"),
                    lang_code=detected_lang,
                    source="whisper-robust",
                    confidence=conf,
                    evidence={
                        "via": "library-audit",
                        "heard": list(det.get("languages_heard") or []),
                        "n_agreeing": det.get("n_agreeing"),
                        "n_total": det.get("n_total"),
                        "tag_was": tag_lang,
                    },
                )
            except Exception:  # best-effort — the audit row is the required part
                pass
        # #357: a confident-multilingual file is the ANSWER, not a mislabel —
        # auto-record the ordered high-conf set so coverage surfaces it (and the
        # override gate skips it). source stays auto (never `user`) so the user
        # can still correct it. Mirrors the whisper-robust Tier-2 write above.
        if status == "multilingual" and multilingual_langs and self._audio_lang is not None:
            key = (canonical_path or "").lstrip("/")
            try:
                # #357: NEVER clobber a human verdict. A user correction lives in
                # the verification store, but the walker's skip-guard keys on the
                # audit-store mtime — so a re-audit after a mtime bump would re-run
                # here. Without this guard it would silently overwrite the user's
                # single-language (or zxx) correction back to auto-multilingual.
                existing = self._audio_lang.get(key)
                if existing is not None and "user" in (getattr(existing, "source", "") or "").lower():
                    pass  # respect the user's (or series-intent) verdict
                else:
                    self._audio_lang.upsert(
                        canonical_path=key,
                        lang_code=multilingual_langs[0],  # first-of-set; singular consumers keep working
                        source="auto-high-conf-multi",
                        confidence=0.9,  # confident by construction (>=1 chunk >= T per lang)
                        evidence={
                            "via": "library-audit",
                            "heard": list(det.get("languages_heard") or []),
                            "multilingual": multilingual_langs,
                            "tag_was": tag_lang,
                        },
                        lang_class="multi",
                        lang_codes=multilingual_langs,
                    )
            except Exception:  # best-effort — the audit row is the required part
                log.warning("multilingual auto-record failed for %s", scrub(key), exc_info=True)
