"""GET /api/coverage — prioritised gap list.

Heavy endpoint (touches 4 upstream services + filesystem). Process-local
TTL cache keyed by (tautulli, probe) since each combo yields different
output. Cache is bypassable via ?fresh=true.

?probe=true folds the ProbeStore cache into each row (embedded_en,
audio_langs, suggest_bazarr_rescan). Default is True now — the cache
lookup is cheap and the embedded-EN reconciliation is the v1.1 hotfix's
whole point.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Query, Request

from ..coverage_engine import CoverageReport, build_coverage

router = APIRouter(prefix="/api", tags=["coverage"])
log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60   # legacy, used by ?fresh handling fallback
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


@router.post("/coverage/refresh")
async def refresh_coverage(request: Request) -> dict[str, Any]:
    """v1.1 ARCH: trigger a background rebuild of the coverage cache.
    Returns immediately with the previous snapshot's stats; the next
    GET /api/coverage will read the new snapshot. Fire-and-forget pattern.

    Frontend calls this after user actions that invalidate the cache
    (e.g. audio-lang verification, manual Bazarr sync). UI polls until
    `refreshing` flips back to False."""
    import asyncio as _asyncio
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is None:
        return {"refreshed": False, "reason": "cache not configured"}
    if cov_cache.is_refreshing():
        return {"refreshed": False, "reason": "refresh already in flight"}
    bundle = request.app.state.integrations
    probe_store = request.app.state.probe_store
    audio_lang_store = getattr(request.app.state, "audio_lang", None)

    probe_walker = getattr(request.app.state, "probe_walker", None)

    async def _do():
        try:
            await cov_cache.refresh(bundle, probe_store, audio_lang_store,
                                    probe_walker=probe_walker)
        except Exception as e:
            log.warning("manual coverage refresh failed: %s", e)

    _asyncio.create_task(_do())
    snap = cov_cache.get_cached()
    return {
        "refreshed": True,
        "previous_generated_at": snap.generated_at if snap else None,
        "previous_item_count": snap.item_count if snap else 0,
    }


@router.get("/coverage/status")
async def coverage_status(request: Request) -> dict[str, Any]:
    """Cheap polling endpoint — returns generated_at + refreshing flag
    only. UI polls during a manual refresh to know when to refetch."""
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    if cov_cache is None:
        return {"available": False}
    snap = cov_cache.get_cached()
    return {
        "available": True,
        "generated_at": snap.generated_at if snap else None,
        "item_count": snap.item_count if snap else 0,
        "build_duration_s": snap.build_duration_s if snap else None,
        "refreshing": cov_cache.is_refreshing(),
    }


# Score values that suppress a row from "actually needs queuing" view.
# Treated as "subarr/probe confirmed English present" rows.
_SUPPRESSED_EMBEDDED = {"EN", "EN(SDH)"}


@router.get("/coverage")
async def get_coverage(
    request: Request,
    fresh: bool = Query(False, description="Bypass the 60s cache"),
    tautulli: bool = Query(True, description="Include Tautulli scoring"),
    probe: bool = Query(True, description="Fold ProbeStore embedded-sub data into rows"),
    hide_embedded_en: bool = Query(
        True,
        description=(
            "Hide rows where our probe confirmed an embedded English track "
            "(full or SDH). Default True — these aren't real gaps. Set False "
            "to see them, e.g. when diagnosing Bazarr-probe disagreements."
        ),
    ),
    hide_stale_disk: bool = Query(
        True,
        description=(
            "Hide rows where subarr's recursive scan found a sibling .srt on "
            "disk that Bazarr doesn't know about yet (common when subgen "
            "wrote it out-of-band). Default True — same logic as "
            "hide_embedded_en. Set False to see them + use the →Bazarr "
            "button to trigger Bazarr's scan-disk task."
        ),
    ),
    hide_english_audio: bool = Query(
        True,
        description=(
            "Hide rows where the file's audio is English (per ffprobe). "
            "Bazarr asking for English subs on English-audio content is "
            "redundant for most users (transcribing for hearing-impaired "
            "is a different workflow). Default True — set False to see "
            "them. Mirrors subgen's SKIP_IF_AUDIO_LANGUAGES=eng default."
        ),
    ),
    hide_pending_download: bool = Query(
        True,
        description=(
            "v1.1-B: Hide rows Sonarr/Radarr report as 'no file imported "
            "yet' — Bazarr's wanted list includes these but they're not "
            "actionable (we can't transcribe a file that doesn't exist). "
            "Default True. Set False to see the awaiting-download backlog. "
            "Counts show up in `totals.pending_download` either way."
        ),
    ),
    only_wanted_langs: str = Query(
        "",
        description=(
            "User-preference filter. Comma-separated ISO 639-1 codes "
            "(e.g. 'en' or 'en,es'). When set, only rows where Bazarr is "
            "asking for subs in at least one of these languages are kept. "
            "Useful when Bazarr's language profile requests langs you "
            "don't actually want. Empty = no filter (show all wanted)."
        ),
    ),
) -> dict[str, Any]:
    # v1.1 ARCH: read from the background-refreshed snapshot cache. Returns
    # in <50ms instead of 60-90s. ?fresh=true forces a synchronous rebuild
    # for callers that genuinely need the latest data (rare — banner +
    # row chips update via 'audio-lang-verified' event triggering a refresh
    # call). When the cache hasn't warmed yet (first boot before background
    # task fires), we fall through to a synchronous build.
    cov_cache = getattr(request.app.state, "coverage_cache", None)
    bundle = request.app.state.integrations
    probe_store = request.app.state.probe_store if probe else None
    audio_lang_store = getattr(request.app.state, "audio_lang", None)

    if cov_cache is not None and not fresh:
        snap = cov_cache.get_cached()
        if snap is not None:
            body = {
                "generated_at": snap.generated_at,
                "items": list(snap.items),
                "totals": dict(snap.totals),
                "sources": dict(snap.sources),
                "build_duration_s": snap.build_duration_s,
                "refreshing": cov_cache.is_refreshing(),
            }
            # Apply the filter chain over the cached items so the user-
            # selected hide_* flags still take effect without rebuilding.
            return _apply_filters_and_pack(
                body, now=time.time(),
                hide_embedded_en=hide_embedded_en,
                hide_stale_disk=hide_stale_disk,
                hide_english_audio=hide_english_audio,
                hide_pending_download=hide_pending_download,
                only_wanted_langs=only_wanted_langs,
            )

    # ?fresh=true OR no cache available → synchronous rebuild and store.
    if cov_cache is not None and fresh:
        snap = await cov_cache.refresh(
            bundle, probe_store, audio_lang_store, use_tautulli=tautulli,
            probe_walker=getattr(request.app.state, "probe_walker", None),
        )
        body = {
            "generated_at": snap.generated_at,
            "items": list(snap.items),
            "totals": dict(snap.totals),
            "sources": dict(snap.sources),
            "build_duration_s": snap.build_duration_s,
            "refreshing": False,
        }
        return _apply_filters_and_pack(
            body, now=time.time(),
            hide_embedded_en=hide_embedded_en,
            hide_stale_disk=hide_stale_disk,
            hide_english_audio=hide_english_audio,
            hide_pending_download=hide_pending_download,
        )

    # Fallback (e.g. boot before warm) — synchronous build, no cache write.
    report: CoverageReport = await build_coverage(
        bundle, use_tautulli=tautulli, probe_store=probe_store,
        audio_lang_store=audio_lang_store,
    )
    body = report.to_dict()
    return _apply_filters_and_pack(
        body, now=time.time(),
        hide_embedded_en=hide_embedded_en,
        hide_stale_disk=hide_stale_disk,
        hide_english_audio=hide_english_audio,
        hide_pending_download=hide_pending_download,
    )


def _apply_filters_and_pack(
    body: dict[str, Any], *, now: float,
    hide_embedded_en: bool, hide_stale_disk: bool,
    hide_english_audio: bool, hide_pending_download: bool,
    only_wanted_langs: str = "",
) -> dict[str, Any]:
    """Shared post-processing: applies the hide_* filters over the items
    list and returns the response body. Mutates `body` (caller passes a
    fresh dict copy)."""
    wanted_lang_set: set[str] = set()
    if only_wanted_langs:
        wanted_lang_set = {
            t.strip().lower() for t in only_wanted_langs.split(",") if t.strip()
        }
    if hide_embedded_en or hide_stale_disk or hide_english_audio or hide_pending_download or wanted_lang_set:
        items = body["items"]
        kept = []
        emb_dropped = stale_dropped = eng_audio_dropped = pending_dropped = wanted_lang_dropped = 0
        for it in items:
            # Probe-gate: unverified rows are STICKY — no hide/lang filter
            # may drop them. They stay visible (bucketed by the frontend)
            # until the probe runs, so nothing the user hasn't seen can
            # silently disappear. Once verified, the normal filters apply.
            if it.get("verification_state", "verified") != "verified":
                kept.append(it)
                continue
            if hide_pending_download and it.get("pending_download"):
                pending_dropped += 1
                continue
            # only_wanted_langs filter — keep row only if any of its
            # missing-subs codes intersect the user's preference set.
            if wanted_lang_set:
                missing = it.get("bazarr", {}).get("missing_subtitles") or []
                missing_set = {(c or "").lower() for c in missing}
                if not (missing_set & wanted_lang_set):
                    wanted_lang_dropped += 1
                    continue
            if hide_embedded_en and it.get("embedded_en") in _SUPPRESSED_EMBEDDED:
                emb_dropped += 1
                continue
            if hide_stale_disk and it.get("has_sub_on_disk"):
                stale_dropped += 1
                continue
            if hide_english_audio:
                if it.get("audio_label_suspect"):
                    pass  # v1.1-O — don't auto-suppress suspect rows
                else:
                    audio_langs = [(l or "").lower() for l in (it.get("audio_langs") or [])]
                    if "eng" in audio_langs:
                        eng_audio_dropped += 1
                        continue
            if hide_english_audio and (it.get("original_language") or "").lower() == "english":
                if not it.get("audio_langs"):
                    eng_audio_dropped += 1
                    continue
            kept.append(it)
        body["items"] = kept
        body["totals"] = dict(body["totals"])
        body["totals"]["items"] = len(kept)
        body["totals"]["suppressed_by_embedded_en"] = emb_dropped
        body["totals"]["suppressed_by_stale_disk"] = stale_dropped
        body["totals"]["suppressed_by_english_audio"] = eng_audio_dropped
        body["totals"]["suppressed_by_pending_download"] = pending_dropped
        body["totals"]["suppressed_by_wanted_langs"] = wanted_lang_dropped
        body["totals"]["episodes"] = sum(1 for i in kept if i["media_type"] == "episode")
        body["totals"]["movies"] = sum(1 for i in kept if i["media_type"] == "movie")
    # Probe-gate bucket counts (always, over the items actually returned).
    # The frontend renders verified rows as the gap list and the rest in
    # sticky "Analyzing" / "Couldn't analyze" sections.
    vstates = [it.get("verification_state", "verified") for it in body.get("items", [])]
    body.setdefault("totals", {})
    body["totals"]["verification"] = {
        "verified": sum(1 for v in vstates if v == "verified"),
        "unprobed": sum(1 for v in vstates if v == "unprobed"),
        "probe_failed": sum(1 for v in vstates if v == "probe_failed"),
        "unsupported": sum(1 for v in vstates if v == "unsupported"),
    }
    body.setdefault("cached", True)
    if body.get("cached") and "generated_at" in body:
        body["cache_age_s"] = round(now - body["generated_at"], 1)
    return body
