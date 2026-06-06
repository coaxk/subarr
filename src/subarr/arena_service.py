"""#131 — ArenaService: background lifecycle + SSE event stream for a sweep.

Mirrors ScanRunner: `create()` makes a pending run, `start()` schedules an
asyncio task that drives `run_arena`, and `subscribe()` is an async generator
the SSE endpoint streams.

Sweeps PERSIST (ArenaStore / SQLite): history survives a restart, and the
results are the substrate the federated tournament (#124) is built on. The
service write-throughs to the store on every state transition, so `get()` /
`list()` always read persisted truth (and survive navigation + restart). The
SSE subscribers + asyncio tasks stay in-memory (transient).

The run state stays light on purpose: live `outcomes` carry label/ok/error
(not the full SRT), and `result` is the serialized `TournamentResult`.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict
from typing import AsyncIterator, Callable

from .arena import CandidateRunner, ConfigVariant, run_arena
from .arena_store import ArenaRun, ArenaStore  # re-exported for stable imports

__all__ = ["ArenaRun", "ArenaStore", "ArenaService", "resolve_source_language"]


def resolve_source_language(detect, tag, submit, multitrack=False):
    """Decide a sweep's source language from the Whisper per-chunk distribution,
    the file's known audio tag, and any language the user set at submit.

    The chunk-agreement SHAPE is the discriminator (not a single ratio):
      - user submit         → authoritative.
      - Whisper UNANIMOUS   → trust it (overrides a wrong tag); mislabel=True if
        it disagrees with the tag (The Ring: tagged Danish, heard Dutch 3/3).
      - SPLIT, real 2nd lang (majority ≥2 AND ≥2 distinct, not unanimous) →
        BILINGUAL: keep the tag as the nominal primary, mixed=True (Besa: tagged
        Serbian, heard en/en/sr → stays Serbian, flagged en+sr).
      - else tag fallback; else a bare plurality; else undetermined.

    `multitrack` (the file has ≥2 distinct audio-TRACK languages, e.g. an
    original + a dub) suppresses the mislabel flag: a Whisper-vs-tag mismatch
    there just means the sweep transcribed a different track (Trigger: ger+rus
    tracks, swept the default German), not a mislabeled tag.

    Returns (language|None, source|None, mixed: bool, mislabel: bool) where
    source ∈ {user, whisper, tagged, whisper-weak, None}."""
    det = detect or {}
    heard = list(det.get("languages_heard") or [])
    whisper_lang = det.get("language")
    unanimous = bool(det.get("unanimous"))
    n_ag = int(det.get("n_agreeing") or 0)
    # a real secondary language (true majority + ≥2 distinct) = bilingual;
    # 1/1/1 (no majority) is just Whisper confused, NOT mixed.
    mixed = (not unanimous) and n_ag >= 2 and len(set(heard)) >= 2
    if submit:
        return submit, "user", False, False
    if unanimous and whisper_lang:
        mislabel = bool(tag and tag != whisper_lang and not multitrack)
        return whisper_lang, "whisper", False, mislabel
    if tag:
        return tag, "tagged", mixed, False
    if whisper_lang and n_ag >= 2:
        return whisper_lang, "whisper-weak", mixed, False
    return None, None, mixed, False


class ArenaService:
    def __init__(self, store: ArenaStore, build_runner: Callable[[ArenaRun], CandidateRunner],
                 explainer=None, max_concurrent: int = 1, lang_fallback=None,
                 track_info=None):
        self._store = store
        self._build_runner = build_runner
        self._explainer = explainer  # async (result_dict, media_path) -> str|None
        # (media_path) -> iso code | None. Resolves the file's KNOWN audio
        # language (ffprobe tag / arr) so a sweep whose Whisper detection was
        # inconclusive still gets a (weaker, "tagged") label instead of None.
        self._lang_fallback = lang_fallback
        # (media_path) -> [track-lang...]. ≥2 distinct = multitrack advisory.
        self._track_info = track_info
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # Single-flight by default: each sweep runs CPU-heavy QE/torch, so N
        # concurrent sweeps saturate the box (observed: 6 stacked sweeps pinned
        # subarr-next + cascade-failed). Submits queue; only `max_concurrent`
        # process at once.
        self._sema = asyncio.Semaphore(max_concurrent)

    # ── store (persisted) ────────────────────────────────────────────────────
    def create(self, media_path: str, variants: list[ConfigVariant],
               source_language: str | None = None, track_index: int = 0) -> ArenaRun:
        run = ArenaRun(
            id=uuid.uuid4().hex[:12],
            media_path=media_path,
            variants=[{"label": v.label, "kwargs": v.kwargs} for v in variants],
            source_language=source_language,
            created_at=time.time(),
            track_index=track_index,
        )
        self._store.save(run)
        return run

    def get(self, run_id: str) -> ArenaRun | None:
        return self._store.get(run_id)

    def list(self) -> list[ArenaRun]:
        return self._store.list()

    def aggregate_by_language(self) -> list[dict]:
        return self._store.aggregate_by_language()

    def aggregate_global_leaderboard(self, *, min_languages: int = 3) -> list[dict]:
        return self._store.aggregate_global_leaderboard(min_languages=min_languages)

    def delete(self, run_id: str) -> bool:
        return self._store.delete(run_id)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, run: ArenaRun) -> None:
        self._tasks[run.id] = asyncio.create_task(self._run(run), name=f"arena-{run.id}")

    async def aclose(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()

    # ── SSE ────────────────────────────────────────────────────────────────
    def _emit(self, run_id: str, evt: dict) -> None:
        for q in list(self._subscribers.get(run_id, ())):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # a slow consumer drops events; it can re-GET the state

    async def subscribe(self, run_id: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(run_id, set()).add(q)
        try:
            while True:
                evt = await q.get()
                yield evt
                if evt.get("event") in ("done", "error"):
                    return
        finally:
            subs = self._subscribers.get(run_id)
            if subs:
                subs.discard(q)
                if not subs:
                    self._subscribers.pop(run_id, None)

    async def _run(self, run: ArenaRun) -> None:
        run_id = run.id
        # Queued until a slot frees — keeps concurrent submits from running
        # their heavy QE work in parallel and saturating the box.
        run.status = "queued"
        run.outcomes = {"clips": [], "done": 0, "total": 0}  # progress scaffold
        self._store.save(run)
        self._emit(run_id, {"event": "queued", "data": {"id": run.id}})
        try:
            async with self._sema:
                run.status = "running"
                self._store.save(run)
                self._emit(run_id, {"event": "start",
                                    "data": {"id": run.id, "variants": [v["label"] for v in run.variants]}})
                await self._execute(run)
        except asyncio.CancelledError:
            run.status = "error"
            run.error = "cancelled"
            self._store.save(run)
            raise
        except Exception as e:
            run.status = "error"
            run.error = str(e)
            self._store.save(run)
            self._emit(run_id, {"event": "error", "data": {"error": str(e)}})

    async def _execute(self, run: ArenaRun) -> None:
        run_id = run.id
        runner = self._build_runner(run)
        variants = [ConfigVariant(v["label"], v["kwargs"]) for v in run.variants]
        per_clip = run.outcomes  # alias the progress dict

        def on_clip(idx: int, kind: str, total: int) -> None:
            if not per_clip["clips"]:
                per_clip["clips"] = [{"kind": None, "status": "pending"} for _ in range(total)]
                per_clip["total"] = total * (len(variants) + 1)  # +1 source per clip
            for j in range(idx):
                per_clip["clips"][j]["status"] = "done"
            per_clip["clips"][idx] = {"kind": kind, "status": "running"}
            self._store.save(run)
            self._emit(run_id, {"event": "clip", "data": {"idx": idx, "kind": kind, "total": total}})

        def on_step() -> None:
            per_clip["done"] += 1
            self._store.save(run)
            self._emit(run_id, {"event": "step", "data": {"done": per_clip["done"]}})

        result = await run_arena(run.media_path, variants, runner=runner,
                                 on_clip=on_clip, on_step=on_step)
        for c in per_clip["clips"]:
            c["status"] = "done"
        serialized = asdict(result)
        if self._explainer is not None:
            try:
                serialized["explanation"] = await self._explainer(serialized, run.media_path)
            except Exception:  # explanation is a nicety — never fail the sweep
                serialized["explanation"] = None
        # Resolve the source language from the Whisper per-chunk distribution +
        # the file's known audio tag. The chunk-agreement SHAPE is the key:
        #   * user set it at submit            → authoritative.
        #   * Whisper UNANIMOUS                 → trust it (can override a wrong
        #       tag); flag a mislabel if it disagrees with the tag (The Ring:
        #       tagged Danish, heard Dutch 3/3 → Dutch + mislabel).
        #   * Whisper SPLIT (real 2nd language) → BILINGUAL content; keep the
        #       tag as the nominal primary + flag the languages heard (Besa:
        #       tagged Serbian, heard en/en/sr → stays Serbian + "mixed en,sr").
        #   * else → tag fallback; else weak plurality; else undetermined.
        det = serialized.get("audio_detect") or {}
        tag = None
        if self._lang_fallback is not None:
            try:
                tag = self._lang_fallback(run.media_path)
            except Exception:
                tag = None
        tracks = []
        if self._track_info is not None:
            try:
                tracks = self._track_info(run.media_path) or []
            except Exception:
                tracks = []
        track_langs = sorted({t for t in tracks if t})
        # ≥2 distinct audio-track LANGUAGES = a multi-track file (original + dub)
        # — the sweep only transcribed ONE track. Distinct from single-track
        # bilingual content; also suppresses a false "mislabel" (the Whisper-vs-
        # tag mismatch is just a different track).
        multitrack = len(track_langs) >= 2
        final, src, mixed, mislabel = resolve_source_language(
            det, tag, run.source_language, multitrack=multitrack)
        run.source_language = final
        serialized["source_language"] = final
        serialized["source_language_source"] = src
        serialized["audio_languages_heard"] = list(det.get("languages_heard") or [])
        serialized["audio_lang_mixed"] = mixed
        serialized["audio_lang_mislabel"] = mislabel
        serialized["audio_multitrack"] = multitrack
        serialized["audio_track_languages"] = track_langs
        run.result = serialized
        run.status = "done"
        self._store.save(run)
        self._emit(run_id, {"event": "done", "data": run.to_dict()})
