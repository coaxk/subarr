"""#131 — tuning-lab arena API.

POST /api/arena/run         start a config sweep (background; returns the run)
GET  /api/arena/runs        list runs
GET  /api/arena/{id}        run state (poll while running, or just read once)
GET  /api/arena/{id}/events SSE stream of live progress

The sweep runs against the live subgen model over the v4.10 /asr path-input
channel (no upload, no shared scratch). Gated on capabilities.asr_arena so
older/vanilla subgen gets a clear "needs subarr-subgen >=v4.10" instead of a
confusing failure.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..arena import ConfigVariant
from ..paths import PathOutsideRootError, canonical_to_fs

router = APIRouter(prefix="/api/arena", tags=["arena"])


def _audio_track_langs(app, canonical_path: str) -> list:
    """Ordered audio-track languages (ISO-639-1) from probe_store ffprobe
    streams. The list index is the audio-stream ordinal used for -map 0:a:N."""
    from ..langs import normalize_lang

    out = []
    store = getattr(app.state, "probe_store", None)
    pr = store.get(canonical_path) if store is not None else None
    for a in getattr(pr, "audio", None) or []:
        out.append(normalize_lang(getattr(a, "language", None) or "") or None)
    return out


class VariantSpec(BaseModel):
    label: str = Field(..., min_length=1)
    kwargs: dict = Field(default_factory=dict)


class ArenaRunRequest(BaseModel):
    media_path: str = Field(..., min_length=1)
    variants: list[VariantSpec] = Field(..., min_length=1)
    source_language: str | None = None


@router.post("/run", status_code=202)
async def create_arena_run(req: ArenaRunRequest, request: Request) -> dict:
    p = req.media_path.strip().strip("/")
    if not p:
        raise HTTPException(400, detail="empty media_path")
    try:
        target = canonical_to_fs(p)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {req.media_path!r}")
    if not target.is_file():
        raise HTTPException(404, detail=f"not a file: {req.media_path!r}")

    labels = [v.label for v in req.variants]
    if len(set(labels)) != len(labels):
        raise HTTPException(400, detail="variant labels must be unique")

    # Capability gate upfront: a clear 503 beats kicking off a run that will
    # fail at preflight. asr_arena = subarr-subgen >=v4.10 (/asr path+kwargs).
    caps = getattr(request.app.state, "subgen_caps", None)
    if not getattr(caps, "asr_arena", False):
        raise HTTPException(
            503,
            detail={
                "error": "unsupported",
                "reason": "the tuning lab needs subarr-subgen >=v4.10 — its /asr "
                "endpoint must advertise the arena channel (path-input + "
                "per-request kwargs).",
                "remedy": "Upgrade your subgen image to ghcr.io/coaxk/subarr-subgen:latest (>=2026.05.3-r4).",
            },
        )

    svc = request.app.state.arena
    variants = [ConfigVariant(v.label, v.kwargs) for v in req.variants]
    # Multi-track fan-out: a file with ≥2 distinct audio-track languages (an
    # original + a dub) sweeps EACH track — one run per track, labeled by that
    # track's language, extracting from that audio stream — so the herd gets
    # per-track recipe data. Skipped when the user pinned a language explicitly.
    track_langs = _audio_track_langs(request.app, p)
    if not req.source_language and len({t for t in track_langs if t}) >= 2:
        runs = []
        for idx, lang in enumerate(track_langs):
            r = svc.create(p, variants, source_language=lang, track_index=idx, is_track_fanout=True)
            svc.start(r)
            runs.append(r)
        return {**runs[0].to_dict(), "fanned_out": len(runs), "fanned_tracks": [t for t in track_langs]}
    run = svc.create(p, variants, source_language=req.source_language)
    svc.start(run)
    return run.to_dict()


@router.get("/runs")
async def list_arena_runs(request: Request) -> dict:
    # Lightweight summaries (no scorecards), newest-first — the sweeps list.
    # Full detail (ranked table) is fetched per-run via GET /api/arena/{id}.
    return {"runs": [r.summary() for r in request.app.state.arena.list()]}


@router.get("/by-language")
async def arena_by_language(request: Request) -> dict:
    # [#26] herd view: per-language recipe stats aggregated across completed
    # sweeps. Defined BEFORE /{run_id} so it isn't captured as a run id.
    return {"languages": request.app.state.arena.aggregate_by_language()}


@router.get("/leaderboard")
async def arena_leaderboard(request: Request, min_languages: int = 3) -> dict:
    # [#146] Global recipe leaderboard: rolls the per-language herd up into one
    # overall ranking by the MEAN OF PER-LANGUAGE MEANS (each language weighted
    # equally). Defined BEFORE /{run_id} so it isn't captured as a run id.
    board = request.app.state.arena.aggregate_global_leaderboard(min_languages=min_languages)
    return {"leaderboard": board, "min_languages": min_languages}


@router.get("/audio-issues")
async def arena_audio_issues(request: Request) -> dict:
    """[#155 phase 1] Library audio-language issues, aggregated from sweeps that
    have ALREADY run — zero extra GPU. Collects done runs the language resolver
    flagged as a mislabel (Whisper unanimously disagrees with the tag) or
    bilingual (multiple languages heard), newest run per file, so a user can
    review/correct them in one place. (Phase 2 = a throttled walker for
    not-yet-swept files.) Defined before /{run_id} so it isn't captured as one."""
    runs = sorted(
        request.app.state.arena.list(), key=lambda r: getattr(r, "created_at", 0) or 0, reverse=True
    )
    # User adjudication is ground truth: once a file has an audio-lang
    # verification, drop it from the issues list (it's been resolved).
    verified: set[str] = set()
    als = getattr(request.app.state, "audio_lang", None)
    if als is not None:
        try:
            verified = {(p or "").lstrip("/") for p in als.get_all_as_lookup().keys()}
        except Exception:
            verified = set()
    by_path: dict[str, dict] = {}
    for r in runs:
        if r.status != "done":
            continue
        res = r.result or {}
        mislabel = bool(res.get("audio_lang_mislabel"))
        mixed = bool(res.get("audio_lang_mixed"))
        if not (mislabel or mixed):
            continue
        if (r.media_path or "").lstrip("/") in verified:  # already resolved
            continue
        if r.media_path in by_path:  # keep the newest run per file
            continue
        by_path[r.media_path] = {
            "run_id": r.id,
            "media_path": r.media_path,
            "status": "mislabel" if mislabel else "bilingual",
            "detected": r.source_language,
            "languages_heard": res.get("audio_languages_heard") or [],
        }
    issues = list(by_path.values())
    return {"issues": issues, "count": len(issues)}


@router.get("/{run_id}")
async def get_arena_run(run_id: str, request: Request) -> dict:
    run = request.app.state.arena.get(run_id)
    if run is None:
        raise HTTPException(404, detail="arena run not found")
    return run.to_dict()


@router.delete("/{run_id}", status_code=204)
async def delete_arena_run(run_id: str, request: Request):
    if not request.app.state.arena.delete(run_id):
        raise HTTPException(404, detail="arena run not found")
    return None


class SetLanguageRequest(BaseModel):
    lang: str = Field(..., min_length=1)


@router.post("/{run_id}/language")
async def set_arena_run_language(run_id: str, req: SetLanguageRequest, request: Request) -> dict:
    """Manually set a sweep's source language — the escape hatch for sweeps that
    landed in 'undetermined' (Whisper inconclusive + no tagged fallback). Sets
    the run's language (user-sourced → re-buckets it in the herd) AND records a
    global audio-lang verification for the file, so coverage and FUTURE sweeps
    of the same file inherit it (user ground truth)."""
    from ..langs import normalize_lang

    run = request.app.state.arena.get(run_id)
    if run is None:
        raise HTTPException(404, detail="arena run not found")
    code = normalize_lang(req.lang)
    if not code or code == "und":
        raise HTTPException(400, detail=f"unrecognized language: {req.lang!r}")
    run.source_language = code
    if isinstance(run.result, dict):
        run.result["source_language"] = code
        run.result["source_language_source"] = "user"
    request.app.state.arena_store.save(run)
    # Persist as global ground truth (best-effort — the herd update above is the
    # required part; the verification is the bonus that benefits coverage + the
    # next sweep of this file).
    store = getattr(request.app.state, "audio_lang", None)
    if store is not None:
        try:
            store.upsert(
                canonical_path=(run.media_path or "").strip().lstrip("/"),
                lang_code=code,
                source="user",
                confidence=1.0,
                evidence={"via": "tuning-lab set-language", "run_id": run_id},
            )
        except Exception:
            pass
    return run.to_dict()


@router.get("/{run_id}/events")
async def arena_events(run_id: str, request: Request) -> StreamingResponse:
    svc = request.app.state.arena
    if svc.get(run_id) is None:
        raise HTTPException(404, detail="arena run not found")

    async def gen():
        try:
            async for evt in svc.subscribe(run_id):
                payload = json.dumps(evt.get("data"))
                yield f"event: {evt['event']}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(gen(), media_type="text/event-stream")
