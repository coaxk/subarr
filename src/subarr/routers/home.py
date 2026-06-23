"""GET /api/home/dashboard — single aggregator endpoint for the v1.0
Home page.

Bundles every section's data into one response so the React frontend
only makes one round-trip per poll. Five sections:

  - stages       : counts + top file per pipeline stage (discovered,
                   probing, bazarr-wanted, scanning, written-back)
  - integrations : per-upstream health + version (Bazarr/Sonarr/Radarr/
                   Tautulli/subgen)
  - gpu          : best-effort GPU stats (nvidia-smi); null when no GPU
  - next_run     : scheduler config + countdown to next walk
  - activity     : last N provenance entries with kind classification

Cost note: most sections are single store queries, but the stages block
DOES fan out to Bazarr (episodes/movies wanted) + subgen /queue. To keep
that off the request path, a 30s DashboardCache (see app.py) fronts this
builder — the frontend's 5s poll mostly hits the cached snapshot, and the
upstream fan-out runs on the cache's own 30s tick.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/home/dashboard")
async def home_dashboard(request: Request, fresh: bool = False) -> dict[str, Any]:
    """v1.1 ARCH: serves the cached dashboard snapshot instantly.
    Background task rebuilds every 30s. ?fresh=true forces a synchronous
    rebuild for callers that need it now."""
    state = request.app.state
    cache = getattr(state, "dashboard_cache", None)
    if cache is not None and not fresh:
        snap = cache.get_cached()
        if snap is not None:
            out = snap.to_response()
            out["refreshing"] = cache.is_refreshing()
            return out
    if cache is not None and fresh:
        snap = await cache.refresh(lambda: _build_dashboard(state))
        return snap.to_response()
    # Cold-path fallback (no cache yet) — synchronous build.
    return await _build_dashboard(state)


async def _build_dashboard(state) -> dict[str, Any]:
    """The slow path — kept as a separate function so the cache can call
    it on its own schedule."""
    integrations_task = asyncio.create_task(_integrations_block(state))
    stages = await _stages_block(state)
    gpu = await _gpu_block(state)
    next_run = _next_run_block(state)
    activity = _activity_block(state)
    integrations = await integrations_task
    return {
        "generated_at": time.time(),
        "stages": stages,
        "integrations": integrations,
        "gpu": gpu,
        "next_run": next_run,
        "activity": activity,
    }


# ─── Stages ─────────────────────────────────────────────────────────


async def _stages_block(state) -> list[dict[str, Any]]:
    """Five stage tiles. Each: id, label, count, top file, topMeta,
    live flag, optional tail (e.g. "4 failed").

    Sparkline data deferred to v1.1 (needs time-bucketed history we
    don't currently store). spark=[] for now — UI tile renders without
    a line, no breakage."""
    stages: list[dict[str, Any]] = []

    # 1. discovered: total media files known to subarr.
    # Use probe_store as the proxy for "files we've seen" — probed and/or
    # unprobed both end up there once a probe walk has touched them.
    try:

        def _discovered_sync() -> tuple[int, str, str]:
            # probe_store.all_entries() loads the FULL probe table (4700+ rows,
            # ~1.2s) — far too heavy to run on the event loop, where it froze
            # every concurrent request during the 30s dashboard refresh. Keep
            # the existing data shape but run it off the loop via to_thread.
            entries = state.probe_store.all_entries()
            if not entries:
                return 0, "", ""
            newest = max(entries, key=lambda e: getattr(e, "probed_at", 0) or 0)
            return (
                len(entries),
                "/" + newest.canonical_path,
                _format_size_meta(getattr(newest, "size", 0), getattr(newest, "duration_s", None)),
            )

        discovered_count, top, top_meta = await asyncio.to_thread(_discovered_sync)
    except Exception as e:
        log.debug("stages: discovered probe error: %s", e)
        discovered_count = 0
        top, top_meta = "", ""
    stages.append(
        {
            "id": "discovered",
            "label": "discovered",
            "count": discovered_count,
            "delta": 0,
            "spark": [],
            "top": top,
            "topMeta": top_meta,
            "live": False,
        }
    )

    # 2. probing: active probe walks.
    try:
        walks = state.probe_walker.list_all()
        active_walks = [w for w in walks if w.status == "running"]
        probing_count = sum(max(0, (w.total_files or 0) - (w.processed or 0)) for w in active_walks)
        live = bool(active_walks)
        current = active_walks[0] if active_walks else None
        top = "/" + current.root if current else ""
        top_meta = f"walk {current.id[:8]}… · {current.processed}/{current.total_files}" if current else ""
    except Exception as e:
        log.debug("stages: probing error: %s", e)
        probing_count, live, top, top_meta = 0, False, "", ""
    stages.append(
        {
            "id": "probing",
            "label": "probing",
            "count": probing_count,
            "delta": 0,
            "spark": [],
            "top": top,
            "topMeta": top_meta,
            "live": live,
        }
    )

    # 3. bazarr-wanted: count from the latest integrations-health cache.
    # Cheaper than re-hitting Bazarr — we just use what the integrations
    # probe block computes alongside.
    wanted_count = 0
    try:
        bazarr = state.integrations.bazarr
        if bazarr.is_configured():
            eps = await bazarr.episodes_wanted()
            movs = await bazarr.movies_wanted()
            wanted_count = len(eps) + len(movs)
    except Exception as e:
        log.debug("stages: bazarr-wanted error: %s", e)
    stages.append(
        {
            "id": "wanted",
            "label": "bazarr-wanted",
            "count": wanted_count,
            "delta": 0,
            "spark": [],
            "top": "",
            "topMeta": "",
            "live": False,
        }
    )

    # 4. scanning: subgen processing count (from /queue if available).
    scanning_count, active_count, queued_count, live, top, top_meta, progress_pct = (
        0,
        0,
        0,
        False,
        "",
        "",
        None,
    )
    try:
        caps = getattr(state, "subgen_caps", None)
        if caps and caps.has_queue:
            q = await state.subgen.queue()
            processing = q.get("processing") or []
            active_count = len(processing)  # #97: in-flight (may be >1)
            queued_count = len(q.get("queued") or [])  # #97: remaining in queue
            scanning_count = active_count + queued_count
            live = bool(processing)
            if processing:
                top = processing[0].get("path", "")
                top_meta = "subgen · in flight"
                # #97: live progress of the CURRENT job. Reuse the queue
                # endpoint's matcher (don't reinvent the log parsing) and
                # only read subgen's logs when something is actually
                # transcribing — idle dashboards cost nothing extra.
                docker_ops = getattr(state, "docker", None)
                if docker_ops is not None:
                    try:
                        from .queue import match_progress

                        pm = await docker_ops.recent_progress(tail=80)
                        prog = match_progress(top, pm)
                        if prog and prog.get("pct") is not None:
                            progress_pct = int(prog["pct"])
                            eta = prog.get("eta")
                            top_meta = f"subgen · {progress_pct}%" + (
                                f" · ETA {eta}" if eta and eta != "?" else ""
                            )
                    except Exception as e:
                        log.debug("stages: scanning progress error: %s", e)
    except Exception as e:
        log.debug("stages: scanning error: %s", e)
    # #336: include subarr's pending feeder backlog so the tile reflects work
    # WAITING to transcribe, not just what's already been handed to subgen.
    # Without this the tile read "0 queued" while jobs sat throttled in the
    # pending queue (the same Queued-vs-Pending gap the Queue page had).
    try:
        pq = getattr(state, "pending_queue", None)
        if pq is not None:
            pending_waiting = pq.count_by_status().get("pending", 0)
            queued_count += pending_waiting
            scanning_count = active_count + queued_count
    except Exception as e:
        log.debug("stages: pending-count error: %s", e)
    stages.append(
        {
            "id": "scanning",
            "label": "transcribing",
            "count": scanning_count,  # total — kept for the chrome-counts poller
            "active": active_count,  # #97: currently transcribing/translating
            "queued": queued_count,  # #97: remaining in queue
            "progress": progress_pct,  # #97: live % of the current job (None when idle)
            "delta": 0,
            "spark": [],
            "top": top,
            "topMeta": top_meta,
            "live": live,
        }
    )

    # 5. written-back: provenance entries that completed + got Bazarr
    # scan-disk fired. Show the most recent one as "top".
    written_count, top, top_meta, tail, tail_kind = 0, "", "", None, None
    try:
        recent_done = state.provenance.recent(limit=200)
        completed = [e for e in recent_done if e.completed_at is not None]
        written_count = len(completed)
        if completed:
            top = "/" + completed[0].canonical_path
            top_meta = "eng"  # provenance doesn't carry the lang yet; refine later
        # Tail: count of pending-not-yet-completed entries within 24h
        pending = state.provenance.pending()
        if pending:
            tail = f"{len(pending)} in flight"
            tail_kind = None
    except Exception as e:
        log.debug("stages: written-back error: %s", e)
    stages.append(
        {
            "id": "written",
            "label": "written-back",
            "count": written_count,
            "delta": 0,
            "spark": [],
            "top": top,
            "topMeta": top_meta,
            "live": False,
            "tail": tail,
            "tailKind": tail_kind,
        }
    )

    return stages


def _format_size_meta(size_bytes: int | None, duration_s: float | None) -> str:
    parts: list[str] = []
    if size_bytes:
        if size_bytes >= 1_000_000_000:
            parts.append(f"{size_bytes / 1_000_000_000:.1f} GB")
        elif size_bytes >= 1_000_000:
            parts.append(f"{size_bytes / 1_000_000:.0f} MB")
        else:
            parts.append(f"{size_bytes // 1000} KB")
    if duration_s:
        mins = int(duration_s // 60)
        parts.append(f"{mins // 60}h{mins % 60:02d}" if mins >= 60 else f"{mins}m")
    return " · ".join(parts)


# ─── Integrations ──────────────────────────────────────────────────


async def _integrations_block(state) -> list[dict[str, Any]]:
    """Same probes as /api/integrations/health, in the shape the
    React Integration tiles want: {name, status, version, ping, extra}.
    """
    from .integrations import _probe

    bundle = state.integrations
    probes = await asyncio.gather(
        _probe("bazarr", bundle.bazarr, "bazarr_badges"),
        _probe("sonarr", bundle.sonarr),
        _probe("radarr", bundle.radarr),
        _probe("plex", bundle.plex),
        _probe("tautulli", bundle.tautulli),
    )
    out = [_to_tile(p) for p in probes]

    # Add subgen tile from cached caps.
    caps = getattr(state, "subgen_caps", None)
    if caps is not None:
        out.append(
            {
                "name": "subgen",
                "status": "ok" if caps.reachable else "error",
                "version": caps.version or "?",
                "ping": 0,
                "extra": (
                    "subarr-subgen"
                    if caps.is_subarr_subgen
                    else ("vanilla — compat mode" if caps.reachable else "unreachable")
                ),
                "configured": True,
            }
        )

    # Add Ollama tile — it's a standalone client on app.state, not part
    # of the integrations bundle. Probe via /api/tags (cheap — lists
    # installed models) so we can show "ok · <model-count> models".
    ollama = getattr(state, "ollama", None)
    if ollama is not None:
        if ollama.is_configured():
            try:
                tags = await ollama.tags()
                model_count = len(tags.get("models") or [])
                out.append(
                    {
                        "name": "ollama",
                        "status": "ok",
                        "version": "",  # ollama /api/tags doesn't surface server version
                        "ping": 0,
                        "extra": f"{model_count} models · {ollama.model}",
                        "configured": True,
                    }
                )
            except Exception as e:
                log.debug("ollama probe error: %s", e)
                out.append(
                    {
                        "name": "ollama",
                        "status": "error",
                        "version": "",
                        "ping": 0,
                        "extra": "unreachable",
                        "configured": True,
                    }
                )
        else:
            out.append(
                {
                    "name": "ollama",
                    "status": "muted",
                    "version": "",
                    "ping": 0,
                    "extra": "not configured",
                    "configured": False,
                }
            )
    return out


def _to_tile(probe: dict[str, Any]) -> dict[str, Any]:
    """Map /api/integrations/health row shape → Home tile shape."""
    online = probe.get("online", False)
    extra_count = probe.get("episodes_wanted") if probe.get("name") == "bazarr" else None
    return {
        "name": probe.get("name"),
        "status": "ok" if online else ("error" if probe.get("configured") else "muted"),
        "version": probe.get("version") or "?",
        "ping": 0,  # we don't currently time per-probe RTT; cosmetic for now
        "extra": (
            f"{extra_count} wanted"
            if extra_count is not None
            else probe.get("error") or ("configured" if probe.get("configured") else "not configured")
        ),
        "configured": probe.get("configured", False),
    }


# ─── GPU ────────────────────────────────────────────────────────────


async def _gpu_block(state) -> dict[str, Any] | None:
    """Best-effort GPU snapshot. None when no GPU / nvidia-smi missing.
    Calls the existing GET /api/gpu handler directly — same data,
    skips the HTTP round-trip."""
    try:
        from .gpu import gpu_status

        snap = await gpu_status()
        if not snap.get("online"):
            return None
        mem = snap.get("memory") or {}
        util = snap.get("utilization") or {}
        pwr = snap.get("power") or {}
        return {
            "name": snap.get("name") or "GPU",
            "util_pct": int(util.get("gpu_pct") or 0),
            "vram_used_mb": int(mem.get("used_mib") or 0),
            "vram_total_mb": int(mem.get("total_mib") or 0),
            "temp_c": int(snap.get("temperature_c") or 0),
            "power_w": int(pwr.get("draw_w") or 0),
            "power_cap_w": int(pwr.get("limit_w") or 0),
            "processes": snap.get("processes") or [],
        }
    except Exception as e:
        log.debug("gpu block error: %s", e)
        return None


# ─── Next run ──────────────────────────────────────────────────────


def _next_run_block(state) -> dict[str, Any]:
    """Scheduler config + countdown to next walk."""
    try:
        schedules = state.schedule.list_schedules()
        # Pick the most recently-configured schedule. v1.0 expects one
        # named 'default'; pre-existing user configs use whatever name
        # they set during onboarding.
        cfg = next((s for s in schedules if s.enabled), None) or (schedules[0] if schedules else None)
    except Exception as e:
        log.debug("next-run block error: %s", e)
        cfg = None
    if cfg is None:
        return {
            "enabled": False,
            "rule": None,
            "mode": None,
            "targets": [],
            "next_run_at": None,
            "countdown_s": None,
            "last_run_at": None,
            "last_result": None,
        }
    try:
        rules = state.schedule.get_rules() or None
        mode = rules.mode if rules else None
    except Exception:
        mode = None
    next_at = cfg.next_run_at
    countdown = max(0, int(next_at - time.time())) if next_at else None
    targets = [t.strip() for t in cfg.probe_roots.split(",") if t.strip()] if cfg.probe_roots else []
    return {
        "enabled": cfg.enabled,
        "rule": cfg.name,
        "mode": mode,
        "targets": targets,
        "next_run_at": next_at,
        "countdown_s": countdown,
        "last_run_at": cfg.last_run_at,
        "last_result": cfg.last_result,
    }


# ─── Activity ──────────────────────────────────────────────────────


def _activity_block(state, limit: int = 10) -> list[dict[str, Any]]:
    """Last N provenance entries, classified into a 'kind' the UI
    color-codes (written-back / scanned / queued / failed)."""
    try:
        rows = state.provenance.recent(limit=limit)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for e in rows:
        if e.completed_at and e.bazarr_scan_triggered_at:
            kind = "written-back"
        elif e.completed_at:
            kind = "scanned"
        else:
            kind = "queued"
        # We don't track 'failed' in provenance currently — that'd need
        # a status column on subs_generated. v1.1 task.
        out.append(
            {
                "t": _fmt_clock(e.queued_at),
                "rel": _fmt_rel(e.queued_at),
                "kind": kind,
                "path": "/" + e.canonical_path,
                "meta": e.source or "",
                "ledger_id": e.id,
            }
        )
    return out


def _fmt_clock(ts: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _fmt_rel(ts: float) -> str:
    delta = max(0, int(time.time() - ts))
    if delta < 60:
        return f"{delta}s"
    if delta < 3600:
        return f"{delta // 60}m"
    if delta < 86400:
        return f"{delta // 3600}h"
    return f"{delta // 86400}d"
