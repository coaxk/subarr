"""POST /api/coverage/queue — resolve a Bazarr-wanted row to a single
file path and enqueue it via the existing scan runner.

Bazarr's wanted payload gives us `sonarrEpisodeId` but no file path. We
resolve Sonarr → episode → episode_file → path, then strip the
ARR_PATH_PREFIX to canonical form. That way we queue ONE .mkv file
rather than the whole series directory.

Movies fall back to the series/movie directory because Radarr's
identifiers in Bazarr's wanted payload don't expose a per-file id
the same way Sonarr's do (the wanted row has the movie itself, which
IS the single video file). For movies the canonical path is already
file-level, so no resolution needed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..integrations import IntegrationError
from ..paths import PathOutsideRootError, canonical_to_fs
from ..provenance import SOURCE_SUBGENSCAN
from ..scan_store import PATH_STATUS_PENDING

router = APIRouter(prefix="/api", tags=["coverage"])
log = logging.getLogger(__name__)


class CoverageQueueRequest(BaseModel):
    sonarr_episode_id: int | None = None
    # Fallback for movies / rows missing sonarr id.
    canonical_path: str | None = None
    reverse: bool = False


def _strip_arr_prefix(arr_path: str) -> str:
    """Mirror of coverage_engine._strip_arr_prefix — kept inline here to
    avoid a circular import; the prefix is one env var."""
    prefix = settings.arr_path_prefix
    s = arr_path or ""
    if prefix and s.startswith(prefix):
        s = s[len(prefix):]
    return s.strip("/")


@router.post("/coverage/queue", status_code=202)
async def coverage_queue(req: CoverageQueueRequest, request: Request) -> dict:
    bundle = request.app.state.integrations

    canonical: str | None = None
    resolved_via: str = ""
    series_id: int | None = None

    if req.sonarr_episode_id is not None:
        if not bundle.sonarr.is_configured():
            raise HTTPException(503, detail="sonarr not configured; cannot resolve episode id")
        try:
            ep = await bundle.sonarr.episode(req.sonarr_episode_id)
        except IntegrationError as e:
            raise HTTPException(502, detail=f"sonarr episode lookup failed: {e}")

        series_id = ep.get("seriesId")
        ep_file_id = ep.get("episodeFileId")
        if not ep_file_id:
            raise HTTPException(
                404,
                detail=(
                    f"sonarr episode {req.sonarr_episode_id} has no episodeFileId "
                    "(file not present on disk according to Sonarr)"
                ),
            )
        try:
            ep_file = await bundle.sonarr.episode_file(ep_file_id)
        except IntegrationError as e:
            raise HTTPException(502, detail=f"sonarr episode_file lookup failed: {e}")

        arr_path = ep_file.get("path")
        if not arr_path:
            raise HTTPException(500, detail=f"sonarr episode_file {ep_file_id} has no path field")
        canonical = _strip_arr_prefix(arr_path)
        resolved_via = f"sonarr_episode_id={req.sonarr_episode_id}"

    elif req.canonical_path:
        canonical = req.canonical_path.strip().strip("/")
        resolved_via = "canonical_path"
    else:
        raise HTTPException(400, detail="must provide sonarr_episode_id or canonical_path")

    # Validate the resolved path exists on disk before enqueueing.
    try:
        target = canonical_to_fs(canonical)
    except PathOutsideRootError:
        raise HTTPException(400, detail=f"resolved path escapes media root: {canonical!r}")
    if not target.exists():
        raise HTTPException(
            404,
            detail=(
                f"resolved path not present on subarr's media mount: {canonical!r}. "
                "Sonarr's filesystem view and subarr's may have diverged."
            ),
        )
    if not (target.is_file() or target.is_dir()):
        raise HTTPException(400, detail=f"resolved path is neither file nor dir: {canonical!r}")

    # v1.1.1 #224: pre-flight audio_language_override lookup.
    # If the user has verified this file's audio language (review queue) AND
    # the verified language is non-English (would otherwise trip subgen's
    # SKIP_IF_AUDIO_LANGUAGES=eng default), forward the override. The
    # capability advertisement on /queue tells us whether subgen will honour
    # it; on vanilla / v4.2 we still send the param (it's silently ignored)
    # so behaviour stays consistent if subgen upgrades mid-flight.
    #
    # v1.2 #105: evidence check. An override that sends the wrong language
    # to Whisper is worse than no override — it forces Whisper to decode
    # English audio as if it were Japanese (anime libraries with EN dub
    # tracks are the canonical failure case). Refuse to forward when the
    # evidence is weak:
    #   - confidence below 0.5 → likely Tautulli-signal-only guess
    #   - source unknown / empty → corrupt store entry
    # Log the full evidence chain at INFO so post-hoc audits can answer
    # "why did subgen transcribe E07 as JA?".
    _RISKY_LANGS = {"ja", "ko", "zh"}  # non-Latin scripts where a wrong
    # override is most expensive — Whisper outputs unusable nonsense
    # rather than degraded English, which masks the bug.
    _MIN_CONFIDENCE = 0.5

    audio_language_override: str | None = None
    audio_lang_store = getattr(request.app.state, "audio_lang", None)
    if audio_lang_store is not None:
        verification = audio_lang_store.get(canonical)
        if verification is not None:
            lang = (verification.lang_code or "").strip().lower()
            src = (verification.source or "").strip().lower()
            conf = float(getattr(verification, "confidence", 0.0) or 0.0)
            # Only override when the verification disagrees with the default
            # English skip-list — for English-verified files there's nothing
            # to bypass.
            if lang and lang not in ("en", "eng"):
                # Evidence gate
                if not src:
                    log.warning(
                        "coverage_queue: REFUSING override=%s for %s — "
                        "verification has no source field (corrupt store entry?)",
                        lang, canonical,
                    )
                elif conf < _MIN_CONFIDENCE:
                    log.warning(
                        "coverage_queue: REFUSING override=%s for %s — "
                        "confidence %.2f < %.2f (source=%s). "
                        "Let subgen detect from audio instead.",
                        lang, canonical, conf, _MIN_CONFIDENCE, src,
                    )
                else:
                    audio_language_override = lang
                    # Extra-loud log for non-Latin scripts where a wrong
                    # override produces unusable output (vs degraded text
                    # for Latin-script languages — easier to spot the bug).
                    evidence_keys = list((verification.evidence or {}).keys())
                    if lang in _RISKY_LANGS:
                        log.info(
                            "coverage_queue: forwarding RISKY override=%s for %s "
                            "(source=%s, conf=%.2f, evidence=%s)",
                            lang, canonical, src, conf, evidence_keys,
                        )
                    else:
                        log.info(
                            "coverage_queue: forwarding audio_language_override=%s "
                            "for %s (source=%s, conf=%.2f, evidence=%s)",
                            lang, canonical, src, conf, evidence_keys,
                        )

    # Enqueue via the existing scan store + runner.
    store = request.app.state.scans
    runner = request.app.state.runner
    scan = store.create([canonical], reverse=req.reverse)
    runner.start(scan, audio_language_override=audio_language_override)

    # Provenance: record the submission so the completion watcher can
    # detect when subgen finishes + trigger Bazarr's scan-disk task.
    provenance = request.app.state.provenance
    ledger_id = provenance.record(
        canonical_path=canonical,
        scan_id=scan.id,
        source=SOURCE_SUBGENSCAN,
        series_id=series_id,
        sonarr_episode_id=req.sonarr_episode_id,
    )

    return {
        "id": scan.id,
        "canonical_path": canonical,
        "resolved_via": resolved_via,
        "status": scan.status,
        "is_file": target.is_file(),
        "series_id": series_id,
        "ledger_id": ledger_id,
    }
