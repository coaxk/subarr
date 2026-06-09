"""v1.1-O Layer 4: Manual audio-language verification endpoints.

GET  /api/audio-lang/verifications        — list all stored verifications
GET  /api/audio-lang/verifications/{path} — get one
POST /api/audio-lang/verifications        — upsert (user confirms language)
DELETE /api/audio-lang/verifications/{path} — remove a verification
POST /api/audio-lang/verifications/bulk-for-series — apply to multiple files
GET  /api/audio-lang/pending-review       — coverage rows needing user input
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..audio_sampler import (
    extract_sample,
    find_dialog_positions,
)
from ..config import settings
from ..paths import PathOutsideRootError, canonical_to_fs
from ..subgen_client import SubgenUnavailable

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audio-lang", tags=["audio-lang"])


class VerifyRequest(BaseModel):
    canonical_path: str
    lang_code: str
    source: str = "user"
    confidence: float = 1.0
    evidence: dict | None = None


@router.get("/verifications")
async def list_verifications(request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    rows = [v.to_dict() for v in store.list_all()]
    return {"count": len(rows), "verifications": rows}


@router.post("/verifications")
async def upsert_verification(req: VerifyRequest, request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    store.upsert(
        canonical_path=req.canonical_path,
        lang_code=req.lang_code,
        source=req.source,
        confidence=req.confidence,
        evidence=req.evidence,
    )

    # v1.1.1 #219 closer: propagate the user's audio-language verification
    # to Sonarr's per-file record so Bazarr's next sync sees the correct
    # foreign language. Bazarr re-evaluates "audio matches subs" against
    # the now-correct audio code and the episode appears in /wanted —
    # subarr's bazarr_blind flag clears naturally on the next coverage walk.
    # Opt-in via SONARR_PROPAGATE_AUDIO_LANG=1. Failures are non-fatal:
    # the local verification still persists, the propagation outcome is
    # surfaced in the response so the UI can show what happened.
    propagation: dict[str, Any] = {"attempted": False}
    if settings.sonarr_propagate_audio_lang and req.source == "user":
        propagation = await _propagate_to_sonarr(
            request,
            canonical_path=req.canonical_path,
            lang_code=req.lang_code,
        )

    # v1.1 ARCH fix #197: kick a background coverage refresh so the
    # corresponding row's chip turns green within a few seconds, not 30.
    # #104: route through the coalescing entry point so a burst of
    # verifications collapses into a single debounced rebuild.
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is not None:
        bundle = request.app.state.integrations
        probe_store = request.app.state.probe_store
        cov_cache.request_refresh(bundle, probe_store, store)
    return {
        "verified": True,
        "canonical_path": req.canonical_path,
        "lang_code": req.lang_code.lower(),
        "sonarr_propagation": propagation,
    }


async def _propagate_to_sonarr(
    request: Request,
    *,
    canonical_path: str,
    lang_code: str,
) -> dict[str, Any]:
    """Push the user's audio-language verification back to Sonarr's
    per-episodeFile language record. Closes the bazarr-blind loop.

    Steps:
      1. Resolve canonical_path → series via the (already-fetched)
         Sonarr series list; find episodeFile matching the path.
      2. Resolve lang_code (en, fre, ger…) → Sonarr language ID via
         /api/v3/language reference table (cached per process).
      3. PUT the episodeFile with updated `languages` field.
    Returns {attempted: True, ok: bool, detail: ...}."""
    from ..integrations import IntegrationError

    bundle = request.app.state.integrations
    sonarr = bundle.sonarr
    if not sonarr.is_configured():
        return {"attempted": True, "ok": False, "detail": "Sonarr not configured"}

    # Find the episodeFile id for this canonical_path by walking the
    # in-process episode-file path map the coverage cache populates.
    ep_file_id: int | None = None
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is not None:
        snap = cov_cache.get_cached()
        if snap is not None:
            for item in snap.items:
                if item.get("file_canonical_path") == canonical_path and (item.get("bazarr") or {}).get(
                    "episode_id"
                ):
                    # Sonarr episode id → episodeFile id requires a lookup.
                    try:
                        ep = await sonarr.episode(item["bazarr"]["episode_id"])
                        ep_file_id = ep.get("episodeFileId")
                        break
                    except IntegrationError as e:
                        log.debug("propagation episode lookup failed: %s", e)
    if not ep_file_id:
        return {
            "attempted": True,
            "ok": False,
            "detail": f"couldn't resolve episodeFile id for path {canonical_path!r}",
        }

    # Resolve the language name. Sonarr's /language returns
    # [{id: 1, name: "English"}, {id: 2, name: "French"}, ...].
    try:
        langs = await sonarr.languages()
    except IntegrationError as e:
        return {"attempted": True, "ok": False, "detail": f"sonarr /language fetch failed: {e}"}
    name = _iso_to_sonarr_name(lang_code)
    target = next(
        (l for l in langs if (l.get("name") or "").lower() == name.lower()),
        None,
    )
    if target is None:
        return {"attempted": True, "ok": False, "detail": f"Sonarr has no language named {name!r}"}

    try:
        await sonarr.update_episode_file_languages(
            episode_file_id=ep_file_id,
            languages=[{"id": target["id"], "name": target["name"]}],
        )
    except IntegrationError as e:
        return {"attempted": True, "ok": False, "detail": f"sonarr PUT failed: {e}"}
    log.info(
        "sonarr propagation OK: episodeFile=%s language=%s (id=%s) via user verification on %s",
        ep_file_id,
        target["name"],
        target["id"],
        canonical_path,
    )

    # Kick Bazarr's "Sync with Sonarr" task so it picks up the new audio
    # language NOW rather than waiting for its scheduled sync (often hours).
    # Bazarr re-evaluates "audio matches subs" on next sync against the now-
    # correct foreign-language audio → the episode appears in /wanted.
    # Best-effort: failure is logged but doesn't roll back the Sonarr write,
    # because Sonarr is the source of truth — Bazarr will eventually sync
    # on its own schedule regardless.
    bazarr_sync = await _trigger_bazarr_sync(bundle)
    return {
        "attempted": True,
        "ok": True,
        "episode_file_id": ep_file_id,
        "language": target["name"],
        "bazarr_sync": bazarr_sync,
    }


# Cache the discovered Bazarr task id so we don't list/match every verify.
_bazarr_sync_task_id: str | None = None


async def _trigger_bazarr_sync(bundle) -> dict[str, Any]:
    """Trigger Bazarr's update_series task — the metadata sync that pulls
    fresh series + episode info from Sonarr. Bounded (~few seconds even
    on large libraries) and idempotent. Returns {ok, detail}."""
    from ..integrations import IntegrationError

    global _bazarr_sync_task_id
    bazarr = bundle.bazarr
    if not bazarr.is_configured():
        return {"attempted": False, "detail": "Bazarr not configured"}

    # Discover the sync task id once per process. Bazarr exposes a stable
    # `update_series` task; we match by job_id substring for forward-compat.
    if _bazarr_sync_task_id is None:
        try:
            tasks = await bazarr.list_tasks()
        except IntegrationError as e:
            return {"attempted": False, "detail": f"bazarr task list failed: {e}"}
        for t in tasks:
            jid = (t.get("job_id") or "").lower()
            name = (t.get("name") or "").lower()
            if "update_series" in jid or "sync with sonarr" in name:
                _bazarr_sync_task_id = t.get("job_id") or t.get("id") or t.get("name")
                break
        if _bazarr_sync_task_id is None:
            return {"attempted": False, "detail": "no update_series task discovered in Bazarr"}

    try:
        await bazarr.trigger_task(_bazarr_sync_task_id)
    except IntegrationError as e:
        return {"attempted": True, "ok": False, "detail": f"bazarr trigger failed: {e}"}
    log.info("bazarr sync triggered: task=%s", _bazarr_sync_task_id)
    return {"attempted": True, "ok": True, "task": _bazarr_sync_task_id}


def _iso_to_sonarr_name(code: str) -> str:
    """Map ISO 639-1/2 codes the UI uses to Sonarr's language names.
    Sonarr stores names like "English", "French", "German"."""
    m = {
        "en": "English",
        "eng": "English",
        "fr": "French",
        "fre": "French",
        "fra": "French",
        "de": "German",
        "ger": "German",
        "deu": "German",
        "es": "Spanish",
        "spa": "Spanish",
        "it": "Italian",
        "ita": "Italian",
        "pt": "Portuguese",
        "por": "Portuguese",
        "ru": "Russian",
        "rus": "Russian",
        "ja": "Japanese",
        "jpn": "Japanese",
        "ko": "Korean",
        "kor": "Korean",
        "zh": "Chinese",
        "chi": "Chinese",
        "zho": "Chinese",
        "nl": "Dutch",
        "dut": "Dutch",
        "nld": "Dutch",
        "pl": "Polish",
        "pol": "Polish",
        "sv": "Swedish",
        "swe": "Swedish",
        "no": "Norwegian",
        "nor": "Norwegian",
        "da": "Danish",
        "dan": "Danish",
        "fi": "Finnish",
        "fin": "Finnish",
        "tr": "Turkish",
        "tur": "Turkish",
        "ar": "Arabic",
        "ara": "Arabic",
        "he": "Hebrew",
        "heb": "Hebrew",
        "hi": "Hindi",
        "hin": "Hindi",
        # 2026-05-31 — Balkan + Baltic + remaining EU slavic langs.
        "bg": "Bulgarian",
        "bul": "Bulgarian",
        "ca": "Catalan",
        "cat": "Catalan",
        "hr": "Croatian",
        "hrv": "Croatian",
        "scr": "Croatian",
        "cs": "Czech",
        "cze": "Czech",
        "ces": "Czech",
        "et": "Estonian",
        "est": "Estonian",
        "el": "Greek",
        "gre": "Greek",
        "ell": "Greek",
        "hu": "Hungarian",
        "hun": "Hungarian",
        "id": "Indonesian",
        "ind": "Indonesian",
        "lv": "Latvian",
        "lav": "Latvian",
        "lt": "Lithuanian",
        "lit": "Lithuanian",
        "ms": "Malay",
        "may": "Malay",
        "msa": "Malay",
        "ro": "Romanian",
        "rum": "Romanian",
        "ron": "Romanian",
        "sr": "Serbian",
        "srp": "Serbian",
        "scc": "Serbian",
        "sk": "Slovak",
        "slo": "Slovak",
        "slk": "Slovak",
        "sl": "Slovenian",
        "slv": "Slovenian",
        "th": "Thai",
        "tha": "Thai",
        "uk": "Ukrainian",
        "ukr": "Ukrainian",
        "vi": "Vietnamese",
        "vie": "Vietnamese",
    }
    return m.get(code.strip().lower(), code)


@router.delete("/verifications/{canonical_path:path}")
async def delete_verification(canonical_path: str, request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    removed = store.delete(canonical_path)
    if not removed:
        raise HTTPException(404, detail="not verified")
    return {"deleted": True, "canonical_path": canonical_path}


class BulkSeriesRequest(BaseModel):
    series_canonical_prefix: str  # e.g. "TV/Flics"
    lang_code: str
    file_paths: list[str]


@router.post("/verifications/bulk-for-series")
async def bulk_for_series(req: BulkSeriesRequest, request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    n = store.bulk_for_series(
        series_canonical_prefix=req.series_canonical_prefix,
        lang_code=req.lang_code,
        file_paths=req.file_paths,
    )
    return {"upserted": n, "lang_code": req.lang_code.lower()}


# ─── #226: series-level intent CRUD ─────────────────────────────────


class SeriesIntentRequest(BaseModel):
    series_prefix: str  # e.g. "TV/Cheers/" — trailing slash auto-added
    lang_code: str
    note: str | None = None


@router.put("/series-intent")
async def upsert_series_intent(req: SeriesIntentRequest, request: Request) -> dict[str, Any]:
    """Declare 'every file under this series prefix is language X'.
    Every subsequent get() for a path under the prefix returns the
    declared lang as a virtual verification — including future episodes
    that don't exist on disk yet. Per-file verifications override this
    so the user can still correct individual mixed-track episodes."""
    store = request.app.state.audio_lang
    store.set_series_intent(
        series_prefix=req.series_prefix,
        lang_code=req.lang_code,
        note=req.note,
    )
    # Kick a coalesced coverage refresh so episodes under the prefix flip
    # green within seconds (mirrors upsert_verification). Best-effort.
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is not None:
        bundle = request.app.state.integrations
        probe_store = request.app.state.probe_store
        cov_cache.request_refresh(bundle, probe_store, store)
    return {"ok": True, "series_prefix": req.series_prefix, "lang_code": req.lang_code.lower()}


@router.get("/series-intent")
async def list_series_intents(request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    rows = store.list_series_intents()
    # Best-effort enrichment from the coverage snapshot: how many files each
    # rule currently covers, and whether it's a show or a movie. No snapshot
    # (fresh boot) → count 0, default "show". title is derived client-side.
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    snap = cov_cache.get_cached() if cov_cache is not None else None
    snap_items = snap.items if snap is not None else []
    enriched = []
    for row in rows:
        prefix = row["series_prefix"]
        count = 0
        media_type = "show"
        for it in snap_items:
            p = it.get("file_canonical_path") or it.get("canonical_path") or ""
            if p.startswith(prefix):
                count += 1
                if it.get("media_type") == "movie":
                    media_type = "movie"
        enriched.append({**row, "covered_count": count, "media_type": media_type})
    return {"items": enriched}


@router.delete("/series-intent")
async def delete_series_intent(series_prefix: str, request: Request) -> dict[str, Any]:
    store = request.app.state.audio_lang
    removed = store.delete_series_intent(series_prefix)
    if not removed:
        raise HTTPException(404, detail=f"no intent declared for {series_prefix}")
    # Revoking a rule must re-evaluate coverage so inherited episodes revert.
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is not None:
        bundle = request.app.state.integrations
        probe_store = request.app.state.probe_store
        cov_cache.request_refresh(bundle, probe_store, store)
    return {"deleted": True, "series_prefix": series_prefix}


# ─── #140: mis-grouped-series dismiss ───────────────────────────────


class MixedDismissRequest(BaseModel):
    series_path: str  # the series directory, e.g. "TV/Trigger"
    note: str | None = None


@router.post("/mixed-dismiss")
async def dismiss_mixed_series(req: MixedDismissRequest, request: Request) -> dict[str, Any]:
    """Dismiss a #140 mixed-language flag for a series the user knows is a
    genuinely multilingual show. Suppressed on all future coverage walks until
    re-enabled."""
    store = request.app.state.audio_lang
    store.dismiss_mixed(req.series_path, note=req.note)
    return {"ok": True, "series_path": req.series_path, "dismissed": True}


@router.delete("/mixed-dismiss")
async def undismiss_mixed_series(series_path: str, request: Request) -> dict[str, Any]:
    """Re-enable the mixed-language flag for a previously-dismissed series."""
    store = request.app.state.audio_lang
    removed = store.undismiss_mixed(series_path)
    if not removed:
        raise HTTPException(404, detail=f"no dismissal for {series_path}")
    return {"deleted": True, "series_path": series_path}


# ─── #159: default-audio-track mismatch — dismiss + in-place swap ────


class TrackMismatchDismissRequest(BaseModel):
    file_canonical_path: str
    note: str | None = None


@router.post("/track-mismatch-dismiss")
async def dismiss_track_mismatch(req: TrackMismatchDismissRequest, request: Request) -> dict[str, Any]:
    """Dismiss the #159 default-track prompt for a file whose default audio the
    user wants to keep as-is. Suppressed on future walks until re-enabled."""
    store = request.app.state.audio_lang
    store.dismiss_track_mismatch(req.file_canonical_path, note=req.note)
    return {"ok": True, "file_canonical_path": req.file_canonical_path, "dismissed": True}


class TrackMismatchDismissBulkRequest(BaseModel):
    file_canonical_paths: list[str]
    note: str | None = None


@router.post("/track-mismatch-dismiss-bulk")
async def dismiss_track_mismatch_bulk(
    req: TrackMismatchDismissBulkRequest, request: Request
) -> dict[str, Any]:
    """#170: dismiss the #159 prompt for many files at once — e.g. "dismiss all
    track-mismatches in this show". The bulk language-assign path deliberately
    excludes track-mismatch rows (they need swap/dismiss, not a language), so
    this is how a user clears a backlog of them without per-row clicks."""
    store = request.app.state.audio_lang
    paths = [p for p in (req.file_canonical_paths or []) if p]
    for p in paths:
        store.dismiss_track_mismatch(p, note=req.note)
    return {"ok": True, "dismissed": len(paths)}


@router.delete("/track-mismatch-dismiss")
async def undismiss_track_mismatch(file_canonical_path: str, request: Request) -> dict[str, Any]:
    """Re-enable the #159 prompt for a previously-dismissed file."""
    store = request.app.state.audio_lang
    removed = store.undismiss_track_mismatch(file_canonical_path)
    if not removed:
        raise HTTPException(404, detail=f"no dismissal for {file_canonical_path}")
    return {"deleted": True, "file_canonical_path": file_canonical_path}


class TrackSwapRequest(BaseModel):
    file_canonical_path: str


@router.post("/track-mismatch-swap")
async def track_mismatch_swap(req: TrackSwapRequest, request: Request) -> dict[str, Any]:
    """#159: make the original-language audio track the sole default, in place,
    via mkvpropedit (instant, lossless, reversible). The server RE-VALIDATES the
    mismatch against a live probe + the coverage row's originalLanguage — the
    client only names the file, never the track index."""
    from ..media_probe import detect_default_track_mismatch, probe
    from ..track_swap import TrackSwapError, swap_default_audio_track

    file_path = req.file_canonical_path
    if not file_path.lower().endswith(".mkv"):
        raise HTTPException(400, detail="track swap is only supported for .mkv files")

    # 1. Confirm subarr actually flagged this file + get the authoritative
    #    originalLanguage from the coverage snapshot (don't act on hearsay).
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    row = None
    if cov_cache is not None:
        snap = cov_cache.get_cached()
        if snap is not None:
            for it in snap.items:
                if it.get("file_canonical_path") == file_path and it.get("default_track_mismatch"):
                    row = it
                    break
    if row is None:
        raise HTTPException(404, detail="no flagged track mismatch for this file")
    original_language = row.get("original_language")

    # 2. Resolve fs path (traversal guard) + re-probe live to get current tracks.
    fs = _resolve_canonical_to_fs(file_path)
    try:
        result = await probe(Path(fs))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, detail=f"probe failed: {e}")
    m = detect_default_track_mismatch(result.audio, original_language)
    if m is None:
        return {"ok": True, "swapped": False, "detail": "already correct"}

    # 3. Swap: make the native-language track the sole default audio track.
    audio_ordinals = list(range(1, len(result.audio) + 1))
    try:
        await swap_default_audio_track(fs, m.native_audio_ordinal, audio_ordinals)
    except TrackSwapError as e:
        raise HTTPException(500, detail=str(e))

    # 4. Re-probe + refresh the cache so the row clears immediately.
    try:
        st = Path(fs).stat()
        fresh = await probe(Path(fs))
        fresh.canonical_path = file_path
        request.app.state.probe_store.upsert(
            canonical_path=file_path,
            mtime=st.st_mtime,
            size=st.st_size,
            result=fresh,
        )
    except Exception:  # noqa: BLE001 — best-effort cache freshness
        pass
    if cov_cache is not None:
        try:
            cov_cache.request_refresh(
                request.app.state.integrations,
                request.app.state.probe_store,
                request.app.state.audio_lang,
            )
        except Exception:  # noqa: BLE001
            pass
    return {
        "ok": True,
        "swapped": True,
        "file_canonical_path": file_path,
        "made_default_track": f"a{m.native_audio_ordinal}",
        "native_lang": m.native_lang,
        "previous_default_lang": m.default_lang,
    }


def _resolve_canonical_to_fs(canonical: str) -> str:
    """Translate a canonical path (TV/Show/...) into an absolute fs path
    on subarr's container view, with traversal guard."""
    try:
        target = canonical_to_fs(canonical)
    except (PathOutsideRootError, ValueError) as e:
        raise HTTPException(400, detail=f"invalid path: {e}")
    if not target.is_file():
        raise HTTPException(404, detail=f"not found: {canonical}")
    return str(target)


@router.get("/sample-positions")
async def sample_positions(
    canonical_path: str = Query(..., description="canonical path under media_root"),
    track: int = Query(0, ge=0, description="audio stream index"),
    n: int = Query(3, ge=1, le=6),
) -> dict[str, Any]:
    """v1.1-O Layer 4++: scan the file for non-silent regions and return
    N suggested sample start positions. UI uses these to populate the
    audio-review player's 'Sample 1 / 2 / 3' buttons — guarantees the user
    isn't hearing dead air on first play."""
    fs = _resolve_canonical_to_fs(canonical_path)
    result = await find_dialog_positions(fs, track=track, n=n)
    return {
        "canonical_path": canonical_path,
        "track": track,
        "duration_s": result.duration_s,
        "audio_tracks": result.audio_tracks,
        "positions": result.positions,
        "silence_count": len(result.silence_ranges),
        # #111: how positions were found — "vad" (silero speech detection) or
        # "silencedetect" (fallback). UI shows provenance so the user knows
        # the clips were picked by detecting actual dialogue.
        "method": result.method,
    }


@router.get("/sample")
async def sample(
    canonical_path: str = Query(...),
    start: float = Query(0.0, ge=0.0),
    duration: float = Query(5.0, gt=0.0, le=30.0),
    track: int = Query(0, ge=0),
):
    """v1.1-O Layer 4++: stream a short MP3 of the requested audio
    window. Browser plays inline via <audio> element."""
    fs = _resolve_canonical_to_fs(canonical_path)

    async def _gen():
        async for chunk in extract_sample(fs, start_s=start, duration_s=duration, track=track):
            yield chunk

    return StreamingResponse(
        _gen(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
        },
    )


@router.get("/pending-review")
async def pending_review(request: Request) -> dict[str, Any]:
    """v1.1-O Layer 4: surface coverage rows that need user audio-lang
    verification — suspect or unknown flags set, no existing verification.

    v1.1 ARCH: reads from the coverage_cache snapshot instead of rebuilding
    coverage end-to-end. Returns instantly (was 60-90s)."""
    audio_lang_store = request.app.state.audio_lang
    verifications = audio_lang_store.get_all_as_lookup()
    # #170: apply the track-mismatch dismiss at READ time too. build_coverage
    # bakes it out only when it regenerates the snapshot; this endpoint reads the
    # cached snapshot, so without this a dismissed row reappears until the next
    # full walk. Mirrors how `verifications` is already applied live below.
    tm_dismissed = audio_lang_store.get_track_mismatch_dismissed_set()
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    items_source: list[dict[str, Any]] = []
    if cov_cache is not None:
        snap = cov_cache.get_cached()
        if snap is not None:
            items_source = snap.items
    if not items_source:
        # No cache yet (very fresh boot) — fall back to a quick build.
        from ..coverage_engine import build_coverage

        bundle = request.app.state.integrations
        probe_store = request.app.state.probe_store
        report = await build_coverage(
            bundle,
            use_tautulli=True,
            probe_store=probe_store,
            audio_lang_store=audio_lang_store,
            subgen_caps=getattr(request.app.state, "subgen_caps", None),
        )
        items_source = report.to_dict()["items"]
    pending = []
    for it in items_source:
        file_path = it.get("file_canonical_path")
        canonical = it.get("canonical_path")
        flag = None
        extra: dict[str, Any] = {}
        # #159: track mismatch is checked FIRST and is exempt from the
        # already-verified skip — it's about which audio track is *default*
        # (double-translation), orthogonal to verifying the audio language.
        if it.get("default_track_mismatch"):
            if file_path and file_path in tm_dismissed:
                continue  # user dismissed this track-mismatch — honor it live
            flag = "track_mismatch"
            extra = {
                "mismatch_default_track_lang": it.get("mismatch_default_track_lang"),
                "mismatch_native_track_lang": it.get("mismatch_native_track_lang"),
                "mismatch_native_audio_ordinal": it.get("mismatch_native_audio_ordinal"),
            }
        # Skip if already verified — check BOTH keys (bulk-verify stores under
        # file_canonical_path || canonical_path; Bazarr-synthetic / series-level
        # rows have a None file_canonical_path and are verified under canonical).
        elif (file_path and file_path in verifications) or (canonical and canonical in verifications):
            continue  # already verified, and not a track mismatch
        elif it.get("audio_label_suspect"):
            flag = "suspect"
        elif it.get("audio_label_unknown"):
            flag = "unknown"
        if not flag:
            continue
        pending.append(
            {
                "canonical_path": it.get("canonical_path"),
                "file_canonical_path": file_path,
                "title": it.get("title"),
                "episode_number": it.get("episode_number"),
                "original_language": it.get("original_language"),
                "audio_langs": it.get("audio_langs"),
                "flag": flag,
                "notes": it.get("audio_label_notes"),
                **extra,
            }
        )
    return {"count": len(pending), "items": pending[:200]}


# ─── v1.2 Layer 3: robust Whisper detection (subarr-subgen v4.5+) ───


class WhisperDetectRequest(BaseModel):
    canonical_path: str
    chunks: int = 3
    chunk_length_s: int = 30


@router.post("/whisper-detect")
async def whisper_detect(req: WhisperDetectRequest, request: Request) -> dict[str, Any]:
    """v1.2-A Layer 3: ask subgen to run robust multi-chunk language
    detection on a file. Synchronous on the subgen side (~6-12s) — the
    caller should not chain this in tight loops.

    Returns the structured per-chunk breakdown + aggregate vote from
    subgen's POST /detect_language_robust. The review UI surfaces this
    as evidence next to the suspect/unknown flag so the user can confirm
    the audio-lang with calibrated confidence instead of guessing.

    Gates on subgen capabilities.robust_language_detection (subarr-subgen
    v4.5+). Older subgen returns 503 with an upgrade hint.
    """
    canonical = (req.canonical_path or "").strip().strip("/")
    if not canonical:
        raise HTTPException(400, detail="canonical_path required")
    caps = getattr(request.app.state, "subgen_caps", None)
    if caps is None or not getattr(caps, "robust_language_detection", False):
        raise HTTPException(
            503,
            detail=(
                "robust_language_detection capability missing — upgrade "
                "subarr-subgen to v4.5+. Current subgen rev: "
                + (getattr(caps, "subarr_subgen_patch_rev", None) or "vanilla")
            ),
        )
    try:
        fs_path = canonical_to_fs(canonical)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"path escapes media root: {canonical!r}")
    if not fs_path.exists():
        raise HTTPException(404, detail=f"not found on disk: {canonical!r}")
    subgen = request.app.state.subgen
    # subgen wants the path as IT sees it (same media_root mount). The
    # canonical form is mount-relative; both sides share the same root
    # by design (see compose.yaml + onboarding wizard mounting docs).
    subgen_path = str(fs_path)
    try:
        result = await subgen.detect_language_robust(
            subgen_path,
            chunks=max(1, min(10, int(req.chunks))),
            chunk_length_s=max(5, min(120, int(req.chunk_length_s))),
        )
    except SubgenUnavailable as e:
        raise HTTPException(502, detail=f"subgen unavailable: {e}")
    return result
