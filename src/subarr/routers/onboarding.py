"""Onboarding wizard API.

GET    /api/onboarding/state              — current step + progress
PUT    /api/onboarding/state              — merge progress, optionally advance step
POST   /api/onboarding/complete           — finalize wizard
POST   /api/onboarding/reset              — start over (Settings → Re-run setup)
POST   /api/onboarding/test/{service}     — verify URL + API key reach service
POST   /api/onboarding/auto-detect        — run discovery + config-extract
POST   /api/onboarding/probe-paths        — check media root reachable + populated
POST   /api/onboarding/first-walk         — kick off the foreground probe walk
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ─── Models ─────────────────────────────────────────────────────────


class StateUpdate(BaseModel):
    step: int | None = None
    progress: dict[str, Any] | None = None
    unset: list[str] | None = None


class TestRequest(BaseModel):
    url: str
    api_key: str | None = None


class ProbePathsRequest(BaseModel):
    media_root: str


# ─── State endpoints ────────────────────────────────────────────────


@router.get("/onboarding/state")
def get_state(request: Request) -> dict[str, Any]:
    state = request.app.state.onboarding.get()
    return state.to_dict()


@router.put("/onboarding/state")
def put_state(body: StateUpdate, request: Request) -> dict[str, Any]:
    state = request.app.state.onboarding.update(
        step=body.step,
        progress_patch=body.progress,
        unset_keys=body.unset,
    )
    return state.to_dict()


@router.post("/onboarding/complete")
def complete(request: Request) -> dict[str, Any]:
    # Flush progress into running settings so the rest of the app sees
    # the wizard's values without a restart.
    state = request.app.state.onboarding.complete()
    _apply_progress_to_settings(state.progress)
    log.info("onboarding completed — applied progress to settings")
    return state.to_dict()


@router.post("/onboarding/reset")
def reset(request: Request) -> dict[str, Any]:
    state = request.app.state.onboarding.reset()
    return state.to_dict()


# ─── Test-connection (per service) ──────────────────────────────────


@router.post("/onboarding/test/{service}")
async def test_connection(service: str, body: TestRequest,
                          request: Request) -> dict[str, Any]:
    """Verify a (url, api_key) reaches the named service.

    Returns a structured result with:
      ok: bool
      version: str | None
      detail: str  (e.g. '658 wanted episodes' for Bazarr)
      error: str | None
    so the wizard can show the Overseerr-style "Bazarr 1.5.6 · 658
    wanted" confirmation chip when green.
    """
    svc = service.lower()
    handlers = {
        "bazarr": _test_bazarr,
        "sonarr": _test_sonarr,
        "radarr": _test_radarr,
        "tautulli": _test_tautulli,
        "subgen": _test_subgen,
        "ollama": _test_ollama,
    }
    handler = handlers.get(svc)
    if handler is None:
        raise HTTPException(404, detail=f"unknown service: {svc}")
    try:
        return await handler(body)
    except Exception as e:
        log.warning("test_connection %s failed: %s", svc, e)
        return {"ok": False, "error": str(e), "version": None, "detail": None}


async def _test_bazarr(body: TestRequest) -> dict[str, Any]:
    """Direct httpx probe — the existing BazarrClient binds to global
    settings, but the wizard needs to test arbitrary URLs before
    committing them to settings. Same /api endpoints + headers."""
    import httpx
    base = body.url.rstrip("/")
    headers = {"X-API-KEY": body.api_key or ""}
    async with httpx.AsyncClient(base_url=base, headers=headers, timeout=15.0) as c:
        r = await c.get("/api/episodes/wanted")
        r.raise_for_status()
        eps = (r.json() or {}).get("data") or []
        r = await c.get("/api/movies/wanted")
        r.raise_for_status()
        movs = (r.json() or {}).get("data") or []
        version = None
        try:
            s = await c.get("/api/system/status")
            if s.status_code == 200:
                version = (s.json() or {}).get("data", {}).get("bazarr_version")
        except Exception:
            pass
    return {
        "ok": True, "version": version,
        "detail": f"{len(eps)} wanted episodes · {len(movs)} wanted movies",
        "error": None,
    }


async def _test_sonarr(body: TestRequest) -> dict[str, Any]:
    # #138: dropped the GET /api/v3/series count from the connection test.
    # On a 1700-show library that endpoint returns multiple MB of JSON and
    # can exceed the 15s timeout, making the onboarding step fail for
    # *exactly* the users who need it most. system/status alone proves
    # reachability + API-key validity (it's gated by auth), which is the
    # only thing the wizard actually needs to gate "Continue" on.
    # The series count surfaces later via the Coverage page anyway.
    import httpx
    headers = {"X-Api-Key": body.api_key or ""}
    async with httpx.AsyncClient(base_url=body.url.rstrip("/"),
                                   headers=headers, timeout=15.0) as c:
        r = await c.get("/api/v3/system/status")
        r.raise_for_status()
        status = r.json()
    version = status.get("version")
    return {
        "ok": True, "version": version,
        "detail": f"Sonarr {version or 'connected'}",
        "error": None,
    }


async def _test_radarr(body: TestRequest) -> dict[str, Any]:
    # #138: same as _test_sonarr — dropped GET /api/v3/movie. Equivalent
    # large-library timeout for Radarr installs with 10k+ movies.
    import httpx
    headers = {"X-Api-Key": body.api_key or ""}
    async with httpx.AsyncClient(base_url=body.url.rstrip("/"),
                                   headers=headers, timeout=15.0) as c:
        r = await c.get("/api/v3/system/status")
        r.raise_for_status()
        status = r.json()
    version = status.get("version")
    return {
        "ok": True, "version": version,
        "detail": f"Radarr {version or 'connected'}",
        "error": None,
    }


async def _test_tautulli(body: TestRequest) -> dict[str, Any]:
    import httpx
    async with httpx.AsyncClient(base_url=body.url.rstrip("/"), timeout=15.0) as c:
        r = await c.get("/api/v2", params={
            "apikey": body.api_key or "", "cmd": "get_history", "length": "1",
        })
        r.raise_for_status()
        body_json = r.json()
        rows = (body_json.get("response", {}).get("data", {}).get("data") or [])
    return {
        "ok": True, "version": None,
        "detail": f"{len(rows)} recent play row(s) reachable",
        "error": None,
    }


async def _test_subgen(body: TestRequest) -> dict[str, Any]:
    from ..subgen_client import SubgenClient
    c = SubgenClient(base_url=body.url)
    try:
        caps = await c.probe_capabilities()
        if not caps.reachable:
            return {"ok": False, "version": None,
                    "detail": None, "error": "subgen not reachable at this URL"}
        kind = "subarr-subgen" if caps.is_subarr_subgen else "vanilla (compat mode)"
        return {
            "ok": True, "version": caps.version,
            "detail": f"{kind} — has_queue={caps.has_queue} has_batch={caps.has_batch}",
            "error": None,
        }
    finally:
        await c.aclose()


async def _test_ollama(body: TestRequest) -> dict[str, Any]:
    from ..integrations.ollama import OllamaClient
    c = OllamaClient(base_url=body.url, model="any")  # model arg is irrelevant for /api/tags
    try:
        tags = await c.tags()
        models = tags.get("models") or []
        return {
            "ok": True, "version": None,
            "detail": f"{len(models)} models installed",
            "error": None,
        }
    finally:
        await c.aclose()


# ─── Auto-detect (discovery + config-extract) ──────────────────────


@router.post("/onboarding/auto-detect")
async def auto_detect(request: Request) -> dict[str, Any]:
    """Run docker discovery + Tier-3 API-key extract in one shot.

    Returns a dict the wizard can use to pre-fill every integration's
    URL + API key in a single network round-trip.
    """
    disc = getattr(request.app.state, "docker_discovery", None)
    if disc is None:
        return {"available": False, "reason": "discovery not configured", "services": {}}
    if not await disc.reachable():
        return {"available": False, "reason": "docker not reachable", "services": {}}

    candidates = await disc.discover()
    # Group by service — wizard picks one candidate per service (or
    # surfaces a chooser when multiple).
    by_service: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        by_service.setdefault(c.service, []).append(c.to_dict())

    # For each detected service, try to extract its API key from the
    # mounted config dir (Tier-3 opt-in path). Returns masked, never
    # raw.
    from ..config_extractor import extract_for_service

    enriched: dict[str, dict[str, Any]] = {}
    for service, cs in by_service.items():
        primary = cs[0]
        # Look for the corresponding env-var that points at the config
        # dir for this service (SUBARR_BAZARR_CONFIG_DIR, etc.)
        env_var = f"SUBARR_{service.upper()}_CONFIG_DIR"
        config_dir = os.environ.get(env_var)
        config_path = Path(config_dir) if config_dir else None
        key_result = extract_for_service(service, config_dir=config_path)
        enriched[service] = {
            "candidate": primary,
            "alternates": cs[1:],
            "api_key_extract": key_result.to_dict(),
        }

    return {"available": True, "services": enriched, "count": len(enriched)}


# ─── Probe paths ────────────────────────────────────────────────────


@router.post("/onboarding/probe-paths")
def probe_paths(body: ProbePathsRequest, request: Request) -> dict[str, Any]:
    """Validate the user's media root: exists, is a directory,
    contains video files. Returns a sample listing the wizard can
    show to the user as confirmation ('3 of 1,247 items shown')."""
    root = Path(body.media_root)
    if not root.exists():
        return {"ok": False, "error": f"path does not exist: {root}"}
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {root}"}

    from ..paths import VIDEO_EXTS
    samples: list[str] = []
    total = 0
    try:
        for p in root.iterdir():
            total += 1
            if p.is_dir():
                samples.append(f"📁 {p.name}/")
            elif p.suffix.lower() in VIDEO_EXTS:
                samples.append(f"🎬 {p.name}")
            else:
                samples.append(f"📄 {p.name}")
            if len(samples) >= 5:
                break
    except PermissionError as e:
        return {"ok": False, "error": f"permission denied reading {root}: {e}"}

    return {
        "ok": True,
        "sample_count": len(samples),
        "total_top_level": total,
        "samples": samples,
        "media_root": str(root),
    }


# ─── First walk ─────────────────────────────────────────────────────


@router.post("/onboarding/first-walk")
async def first_walk(request: Request) -> dict[str, Any]:
    """Kick off the post-wizard probe walk against the configured media
    roots. Foreground (Overseerr pattern) so the dashboard shows real
    data on first paint instead of an empty state.

    Also PERSISTS the chosen probe_roots onto the coverage_walk schedule
    so that every future scheduled walk includes the ffprobe step. Without
    this hand-off the cache built during onboarding would slowly go stale
    as new episodes arrive — and the "skip embedded EN" filter would have
    no fresh data to consult. (See: this was the original-subarr behavior
    that got lost in translation in the v1.0 rebuild.)
    """
    walker = request.app.state.probe_walker
    state = request.app.state.onboarding.get()
    roots_raw = state.progress.get("probe_roots") or ["TV", "Movies"]
    roots = [r.strip().strip("/") for r in roots_raw if r and r.strip()]

    # 1. Persist roots onto the schedule so ongoing walks ffprobe too.
    persist_error: str | None = None
    try:
        store = request.app.state.schedule
        # Store as comma-separated string per schedule_store schema.
        store.update_schedule("coverage_walk", probe_roots=",".join(roots))
        log.info("first-walk: persisted probe_roots=%s onto coverage_walk schedule", roots)
    except Exception as e:
        # Don't fail the walk if persistence fails — log it, surface it
        # in the response so the wizard can flag.
        persist_error = str(e)
        log.warning("first-walk: schedule probe_roots persist failed: %s", e)

    # 2. Fire the foreground walk for first-paint coverage data.
    walks: list[dict[str, Any]] = []
    for root in roots:
        try:
            w = await walker.start_walk(root)
            walks.append({"walk_id": w.id, "root": w.root})
        except Exception as e:
            log.warning("first-walk start failed for %s: %s", root, e)
            walks.append({"root": root, "error": str(e)})
    return {
        "walks": walks,
        "schedule_probe_roots": roots,
        "schedule_persisted": persist_error is None,
        **({"schedule_persist_error": persist_error} if persist_error else {}),
    }


# ─── Internal: flush wizard progress to settings ────────────────────


def _apply_progress_to_settings(progress: dict[str, Any]) -> None:
    """Write the wizard's collected values into the running Settings
    instance so the rest of the app picks them up without a restart.

    NOTE: this is a runtime patch — restarting the container reloads
    from env vars. v1.x adds a config-file persistence layer; for v1.0
    we rely on the user pinning their env vars in compose after the
    wizard finishes (the wizard's final step shows a copy-paste
    snippet of the values they entered)."""
    from ..config import settings
    mapping = {
        "media_root":       ("media_root", Path),
        "arr_path_prefix":  ("arr_path_prefix", str),
        "bazarr_url":       ("bazarr_url", str),
        "bazarr_api_key":   ("bazarr_api_key", str),
        "sonarr_url":       ("sonarr_url", str),
        "sonarr_api_key":   ("sonarr_api_key", str),
        "radarr_url":       ("radarr_url", str),
        "radarr_api_key":   ("radarr_api_key", str),
        "tautulli_url":     ("tautulli_url", str),
        "tautulli_api_key": ("tautulli_api_key", str),
        "subgen_url":       ("subgen_url", str),
        "ollama_url":       ("ollama_url", str),
        "ollama_model":     ("ollama_model", str),
    }
    for src_key, (settings_attr, coerce) in mapping.items():
        if src_key in progress and progress[src_key]:
            try:
                setattr(settings, settings_attr, coerce(progress[src_key]))
            except Exception as e:
                log.warning("settings flush: %s=%r failed: %s",
                            settings_attr, progress[src_key], e)
