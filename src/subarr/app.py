"""FastAPI app entry point."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class RevalidatingStaticFiles(StaticFiles):
    """Static files that must be revalidated before reuse. The v1 HTML
    references bundles by a fixed name with no cache-bust, so without this
    browsers heuristic-cache the bundles and serve STALE UI after every
    update. `no-cache` forces an ETag revalidation each load — cheap 304s
    when unchanged, fresh bundle the instant it changes. ETag/Last-Modified
    are still sent by the base class, so this stays bandwidth-efficient."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

from . import __version__
from .completion_watcher import CompletionWatcher
from .config import settings
from .coverage_engine import IntegrationBundle
from .docker_client import DockerOps
from .enrichment import EnrichmentStore
from .integrations.ollama import OllamaClient
from .pending_store import PendingStore
from .probe_store import ProbeStore
from .probe_walker import ProbeWalker
from .provenance import ProvenanceStore
from .onboarding import OnboardingStore
from .routers import (
    admin, arbiter as r_arbiter, arena as r_arena, arr_mediainfo as r_arr_mediainfo,
    audio_lang as r_audio_lang,
    bazarr_sync, blacklist as r_blacklist, browse, coverage, coverage_actions,
    discovery as r_discovery, household as r_household,
    providers as r_providers, vision as r_vision,
    enrichment as r_enrichment, gpu, home as r_home, integrations, logs, mode,
    onboarding as r_onboarding, probe as r_probe, provenance as r_provenance,
    queue, scan, schedule as r_schedule, sidecar as r_sidecar,
    telemetry as r_telemetry, updates as r_updates, vad as r_vad,
)
from . import arena_explain as _arena_explain
from .arena import AsrRunner
from .arena_service import ArenaService
from .arena_store import ArenaStore
from .scan_runner import ScanRunner
from .scan_store import ScanStore
from .error_store import ErrorStore
from .schedule_store import ScheduleStore
from .scheduler import Scheduler
from .subgen_client import SubgenClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


def _arena_fallback_lang(app_, media_path):
    """Arena fallback source language when Whisper robust detection is
    inconclusive: the file's KNOWN spoken/audio language.

    Tier 1 — the ffprobe audio-stream tag (probe_store).
    Tier 2 — coverage's `audio_langs`, which folds in arr mediaInfo. This
    catches files whose ffprobe audio track is UNTAGGED (language=null) but
    Sonarr/Radarr knows the spoken language (e.g. Black Wedding: ffprobe tag
    null, Sonarr original_language 'Serbian', but the audio is Norwegian — and
    coverage's AUDIO column correctly shows `nor`). We use audio_langs (the
    spoken track), NOT original_language (the show's production-language
    metadata, which can differ from what's actually spoken).

    Returns an ISO-639-1 code, or None (→ the herd's 'undetermined' bucket)."""
    from .langs import normalize_lang
    canon = (media_path or "").strip().lstrip("/")
    # Tier 0: a user's manual audio-lang verification (incl. one set via the
    # tuning-lab "set language" action) — ground truth, so a corrected file's
    # future sweeps auto-resolve.
    try:
        als = getattr(app_.state, "audio_lang", None)
        v = als.get(canon) if als is not None else None
        code = normalize_lang(getattr(v, "lang_code", None) or "")
        if code and code != "und":
            return code
    except Exception:
        pass
    # Tier 1: ffprobe audio-stream language tag.
    try:
        store = getattr(app_.state, "probe_store", None)
        pr = store.get(canon) if store is not None else None
        for a in (getattr(pr, "audio", None) or []):
            code = normalize_lang(getattr(a, "language", None) or "")
            if code and code != "und":
                return code
    except Exception:
        pass
    # Tier 2: coverage's audio_langs (ffprobe + arr mediaInfo merged).
    try:
        cc = getattr(app_.state, "coverage_cache", None)
        snap = cc.get_cached() if cc is not None else None
        for it in (getattr(snap, "items", None) or []):
            if it.get("file_canonical_path") == canon or it.get("canonical_path") == canon:
                for al in (it.get("audio_langs") or []):
                    code = normalize_lang(al)
                    if code and code != "und":
                        return code
    except Exception:
        pass
    return None


def _arena_audio_tracks(app_, media_path):
    """The file's audio-TRACK languages (normalized ISO-639-1) from the ffprobe
    streams in probe_store. ≥2 distinct = a multi-track file (e.g. an original +
    a dub), which the Tuning Lab can only sweep ONE track of — surfaced as a
    'multitrack' advisory distinct from single-track bilingual content."""
    from .langs import normalize_lang
    out = []
    try:
        store = getattr(app_.state, "probe_store", None)
        pr = store.get((media_path or "").strip().lstrip("/")) if store is not None else None
        for a in (getattr(pr, "audio", None) or []):
            out.append(normalize_lang(getattr(a, "language", None) or "") or None)
    except Exception:
        return []
    return out


@asynccontextmanager
async def lifespan(app_: FastAPI):
    # Schema migrations run BEFORE any store touches the DB. After they
    # complete the DB has the v1.0 baseline (+ any newer migrations). The
    # per-store init_schema() calls below become no-ops on a migrated DB
    # (CREATE TABLE IF NOT EXISTS); kept during the v0.x→v1.0 transition
    # so a half-deployed wheel can still self-heal. Remove the
    # init_schema() calls after v1.0 ships.
    from .migrate import run_migrations
    applied = run_migrations(settings.db_path)
    if applied:
        log.info("schema migrations applied this boot: %s",
                 [m.name for m in applied])
    app_.state.subgen = SubgenClient()
    # Probe subgen capabilities once at boot. The result drives:
    #   - whether the header counter shows queue depth
    #   - whether completion_watcher polls /queue vs the provenance table
    #   - whether the scan-submit UI is enabled or shows "needs subarr-subgen"
    # Stored on app.state for the health endpoint + downstream gating.
    # #229: re-probed periodically by SubgenWatchdog (started further down)
    # so subgen restarts get detected; on restart, in-flight scan_store
    # entries can be reconciled as orphaned instead of pretending they
    # are still queued.
    app_.state.subgen_caps = await app_.state.subgen.probe_capabilities()
    app_.state.subgen_restart_detected_at = None
    app_.state.subgen_restart_from = None
    app_.state.subgen_restart_to = None
    # All schema is owned by migrations (run_migrations above, before any
    # store is constructed). Stores no longer self-create tables.
    app_.state.scans = ScanStore(settings.db_path)
    # Anonymous error-class log for telemetry (schema via migration 006).
    app_.state.errors = ErrorStore(settings.db_path)
    app_.state.runner = ScanRunner(
        store=app_.state.scans,
        caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
        # Resolve subgen live so onboarding can swap the client without
        # restarting the runner.
        subgen_provider=lambda: app_.state.subgen,
        # Best-effort anonymous error-class recording for telemetry.
        error_recorder=lambda cls: app_.state.errors.record(cls),
    )
    # #131 tuning-lab arena. Sweeps persist (SQLite) so history survives a
    # restart and feeds the federated tournament (#124). Reconcile any run that
    # was mid-flight when the process last died → error, so the UI never shows a
    # forever-spinning sweep. build_runner resolves subgen + caps LIVE (closure
    # over app_.state) so an onboarding client-swap or a subgen upgrade picked
    # up by the watchdog is reflected on the next run without a restart.
    app_.state.arena_store = ArenaStore(settings.db_path)
    _orphaned = app_.state.arena_store.reconcile_interrupted()
    if _orphaned:
        log.info("arena: marked %d interrupted sweep(s) as errored on boot", _orphaned)
    app_.state.arena = ArenaService(
        app_.state.arena_store,
        build_runner=lambda run: AsrRunner(
            app_.state.subgen,
            capabilities=getattr(app_.state, "subgen_caps", None),
            source_language=run.source_language,
        ),
        # ollama EXPLAINS the result in plain language (not scoring). Resolved
        # live so an onboarding ollama-config swap is picked up without restart.
        explainer=lambda result, media_path: _arena_explain.explain(
            result, media_path, getattr(app_.state, "ollama", None)),
        # Fallback source language when Whisper robust detection is inconclusive:
        # the file's KNOWN audio language from the ffprobe tag (probe_store).
        # Resolved live (probe_store is created later in lifespan; this closure
        # runs only at sweep time, by which point it exists).
        lang_fallback=lambda media_path: _arena_fallback_lang(app_, media_path),
        # Audio-track languages → 'multitrack' advisory (original + dub etc.).
        track_info=lambda media_path: _arena_audio_tracks(app_, media_path),
    )
    app_.state.docker = DockerOps()
    app_.state.integrations = IntegrationBundle()
    app_.state.provenance = ProvenanceStore(settings.db_path)
    app_.state.watcher = CompletionWatcher(
        provenance=app_.state.provenance,
        caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
        # Resolve clients live so onboarding can swap them without
        # restarting the watcher. bundle_provider supplies bazarr + the
        # Plex client (Plex fires partial-scan when the sidecar lands,
        # closing the Apple TV loop without waiting for Plex's periodic
        # full library scan).
        bundle_provider=lambda: app_.state.integrations,
        subgen_provider=lambda: app_.state.subgen,
    )
    app_.state.watcher.start()
    app_.state.schedule = ScheduleStore(settings.db_path)
    app_.state.ollama = OllamaClient()
    # #119: last-known ollama reachability, populated by the integrations-
    # health probe (GET /api/integrations/health). None until first probe;
    # telemetry gates "ollama configured" on this real signal instead of
    # the defaulted OLLAMA_URL (non-empty on every install).
    app_.state.ollama_probe_result = None
    # v1.1-O Layer 4: user audio-language verifications (manual review queue).
    from .audio_lang_store import AudioLangStore
    app_.state.audio_lang = AudioLangStore(settings.db_path)
    # v1.1 ARCH: coverage cache + background refresh (kills 60-90s page loads).
    from .coverage_cache import CoverageCache, background_refresh_loop
    app_.state.coverage_cache = CoverageCache(settings.db_path)
    app_.state.coverage_cache.load()  # warm in-memory mirror; table via migrations
    # v1.1 ARCH: dashboard cache (30s refresh, in-memory only).
    from .dashboard_cache import DashboardCache, background_refresh_loop as dash_refresh_loop
    app_.state.dashboard_cache = DashboardCache()
    app_.state.enrichment = EnrichmentStore(settings.db_path)
    app_.state.probe_store = ProbeStore(settings.db_path)
    app_.state.probe_walker = ProbeWalker(app_.state.probe_store)
    app_.state.pending = PendingStore(settings.db_path)
    app_.state.onboarding = OnboardingStore(settings.db_path)
    app_.state.scheduler = Scheduler(
        schedule_store=app_.state.schedule,
        # Resolve the bundle live so onboarding can swap clients without
        # restarting the scheduler.
        bundle_provider=lambda: app_.state.integrations,
        scan_store=app_.state.scans,
        runner=app_.state.runner,
        provenance=app_.state.provenance,
        probe_walker=app_.state.probe_walker,
        pending_store=app_.state.pending,
        # #79: live caps so the coverage_walk forced-only-EN gate tracks the
        # runtime IGNORE_FORCED_SUBTITLES value.
        caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
    )
    app_.state.scheduler.start()

    # v1.1 ARCH: start the coverage-cache background refresh loop.
    # Independent of the coverage_walk schedule (much heavier); this one
    # is purely "keep the page snappy". Default 5-min tick.
    app_.state.coverage_cache_task = asyncio.create_task(
        background_refresh_loop(
            cache=app_.state.coverage_cache,
            # Resolve the bundle live so onboarding can swap clients
            # without restarting this refresh loop.
            bundle_provider=lambda: app_.state.integrations,
            probe_store=app_.state.probe_store,
            audio_lang_store=app_.state.audio_lang,
            # PR-C: eager-probe unprobed wanted files each refresh so the
            # probe-gate's gap list populates regardless of probe_roots.
            probe_walker=app_.state.probe_walker,
            # #79: resolve subgen caps live so the forced-only-EN gate tracks
            # the watchdog-detected IGNORE_FORCED_SUBTITLES runtime value.
            caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
        )
    )
    # Dashboard cache background refresh — passes a build closure that
    # reuses the existing /api/home/dashboard internals.
    from .routers.home import _build_dashboard as _dash_build
    app_.state.dashboard_cache_task = asyncio.create_task(
        dash_refresh_loop(
            cache=app_.state.dashboard_cache,
            build_fn=lambda: _dash_build(app_.state),
        )
    )

    # Update notification poller — once-per-24h GitHub release check
    # cached to update_checks table. Backs /api/updates which the UI
    # consumes for the header pill + Home tile + Settings panel.
    #
    # #108: the patch-stack revision (subarr_subgen_patch_rev = 'v4.7')
    # is the version we compare against coaxk/subarr-subgen release
    # tags. Previously we sent subgen_caps.version (the UPSTREAM subgen
    # version, e.g. '2026.05.3') which is meaningful for vanilla subgen
    # but never matches the patch-stack tags — so users running an old
    # patch level never saw an update notification. Fall back to the
    # upstream version when running vanilla subgen (no patch_rev), and
    # to None when subgen is unreachable.
    _subgen_caps = getattr(app_.state, "subgen_caps", None)
    if _subgen_caps and getattr(_subgen_caps, "subarr_subgen_patch_rev", None):
        _subgen_current = _subgen_caps.subarr_subgen_patch_rev
    elif _subgen_caps and _subgen_caps.version:
        # Vanilla subgen — keep the legacy behaviour (compare upstream
        # version against the McCloudS/subgen tag, though release_notes
        # link still points to our repo by DEFAULT_PRODUCTS).
        _subgen_current = _subgen_caps.version
    else:
        _subgen_current = None
    from .update_checker import UpdateChecker
    current_versions = {
        "subarr": __version__,
        "subarr-subgen": _subgen_current,
    }
    app_.state.update_checker = UpdateChecker(
        db_path=settings.db_path,
        current_version_resolver=current_versions,
    )
    app_.state.update_checker.start()

    # #229: subgen identity watchdog. Periodically re-probes subgen's
    # /queue + /status to detect restarts (patch_rev or version change).
    # On restart, stamps app.state.subgen_restart_detected_at so the
    # queue UI can surface the event + reconcile orphaned scan_store
    # entries. Reconciliation hook lives on the watchdog so this module
    # stays decoupled from scan_store + watcher details.
    from .subgen_watchdog import SubgenWatchdog

    async def _on_subgen_restart(old_caps, new_caps, detected_at):
        app_.state.subgen_restart_detected_at = detected_at
        app_.state.subgen_restart_from = {
            "patch_rev": getattr(old_caps, "subarr_subgen_patch_rev", None),
            "version": getattr(old_caps, "version", None),
        }
        app_.state.subgen_restart_to = {
            "patch_rev": getattr(new_caps, "subarr_subgen_patch_rev", None),
            "version": getattr(new_caps, "version", None),
        }
        # #229 phase 2: reconcile orphaned scan_store entries. Walks
        # scan_store for status=ok entries created before the restart
        # detection, excludes those whose canonical_path appears in
        # provenance.completed_paths_since (transcription confirmed),
        # re-tags the rest as PATH_STATUS_ORPHANED with a clear reason.
        # The Queue UI surfaces them in their own "Lost on restart"
        # bucket (see queue.py history endpoint).
        try:
            scans = app_.state.scans
            provenance = app_.state.provenance
            # Look back 24h — anything older than that is from a previous
            # session anyway. Anything completed_at >= cutoff is "real done".
            cutoff = detected_at
            completed = provenance.completed_paths_since(detected_at - 86400)
            # Evidence: if subgen is reachable and still holds these items in
            # its queue, it was a blip, not a real restart — don't orphan
            # them. A genuine restart leaves subgen's queue empty.
            import os as _os
            live_basenames: set[str] = set()
            try:
                q = await app_.state.subgen.queue()
                for t in (q.get("processing") or []) + (q.get("queued") or []):
                    if isinstance(t, dict) and t.get("path"):
                        live_basenames.add(_os.path.basename(t["path"]))
            except Exception as e:
                log.debug("orphan reconcile: subgen queue fetch failed: %s", e)
            n = scans.mark_orphaned_before(
                cutoff, completed_paths=completed, live_basenames=live_basenames,
            )
            log.warning(
                "subgen restart reconciliation: %d in-flight scan_store "
                "entries marked ORPHANED (provenance confirms %d completed "
                "in last 24h, preserved as ok)",
                n, len(completed),
            )
        except Exception as e:
            log.error("subgen restart reconciliation failed: %s", e,
                      exc_info=True)

    def _get_caps():
        return getattr(app_.state, "subgen_caps", None)

    def _set_caps(c):
        app_.state.subgen_caps = c

    app_.state.subgen_watchdog = SubgenWatchdog(
        # Resolve subgen live so onboarding reload doesn't leave the
        # watchdog probing the closed boot client.
        subgen_provider=lambda: app_.state.subgen,
        get_caps=_get_caps,
        set_caps=_set_caps,
        on_restart=_on_subgen_restart,
    )
    app_.state.subgen_watchdog.start()

    # Anonymous telemetry — ON by default per v1.0 product decision.
    # Opt-out one-click in Settings. Stats published publicly at
    # subarr.com/stats so users see what we see.
    from .telemetry import TelemetryCollector, make_default_stats_provider
    app_.state.telemetry = TelemetryCollector(
        db_path=settings.db_path,
        endpoint=settings.telemetry_endpoint,
        subarr_version=__version__,
        stats_provider=make_default_stats_provider(app_.state),
        subgen_caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
    )
    app_.state.telemetry.start()

    # Tier-2 docker discovery for the onboarding wizard. Optional —
    # disabled if neither SUBARR_DOCKER_PROXY_URL nor a socket path
    # is configured. The wizard probes via GET /api/discovery and
    # gracefully falls back to manual entry when unavailable.
    if settings.docker_proxy_url or settings.docker_socket_path:
        from .docker_discovery import DockerDiscovery
        app_.state.docker_discovery = DockerDiscovery(
            base_url=settings.docker_proxy_url or None,
            unix_socket=settings.docker_socket_path or None,
        )
        log.info(
            "docker discovery enabled (transport=%s)",
            "proxy" if settings.docker_proxy_url else "socket",
        )
    else:
        app_.state.docker_discovery = None

    try:
        yield
    finally:
        if app_.state.docker_discovery is not None:
            await app_.state.docker_discovery.aclose()
        await app_.state.telemetry.stop()
        await app_.state.update_checker.stop()
        try:
            await app_.state.subgen_watchdog.stop()
        except (AttributeError, Exception):
            pass
        await app_.state.probe_walker.aclose()
        await app_.state.scheduler.stop()
        try:
            app_.state.coverage_cache_task.cancel()
            await app_.state.coverage_cache_task
        except (asyncio.CancelledError, AttributeError):
            pass
        try:
            app_.state.dashboard_cache_task.cancel()
            await app_.state.dashboard_cache_task
        except (asyncio.CancelledError, AttributeError):
            pass
        await app_.state.watcher.stop()
        await app_.state.runner.aclose()
        await app_.state.subgen.aclose()
        await app_.state.integrations.aclose()
        await app_.state.ollama.aclose()
        app_.state.scans.close()
        app_.state.errors.close()
        app_.state.provenance.close()
        app_.state.schedule.close()
        app_.state.enrichment.close()
        app_.state.probe_store.close()
        app_.state.pending.close()
        app_.state.onboarding.close()
        app_.state.docker.close()


app = FastAPI(title="subarr", version=__version__, lifespan=lifespan)

# Basic auth — no-op when SUBARR_USER/SUBARR_PASS unset. Added first
# so the middleware wraps everything below (including static asset
# serving and the legacy / route).
from .auth import BasicAuthMiddleware  # noqa: E402
app.add_middleware(BasicAuthMiddleware, user=settings.auth_user, password=settings.auth_pass)

app.include_router(browse.router)
app.include_router(mode.router)
app.include_router(queue.router)
app.include_router(scan.router)
app.include_router(gpu.router)
app.include_router(logs.router)
app.include_router(admin.router)
app.include_router(integrations.router)
app.include_router(coverage.router)
app.include_router(coverage_actions.router)
app.include_router(r_provenance.router)
app.include_router(r_schedule.router)
app.include_router(r_enrichment.router)
app.include_router(r_probe.router)
app.include_router(r_arr_mediainfo.router)
app.include_router(r_arbiter.router)
app.include_router(r_audio_lang.router)
app.include_router(r_providers.router)
app.include_router(r_blacklist.router)
app.include_router(r_household.router)
app.include_router(r_vision.router)
app.include_router(bazarr_sync.router)
app.include_router(r_updates.router)
app.include_router(r_discovery.router)
app.include_router(r_telemetry.router)
app.include_router(r_home.router)
app.include_router(r_onboarding.router)
app.include_router(r_sidecar.router)
app.include_router(r_vad.router)
app.include_router(r_arena.router)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "media_root": str(settings.media_root),
        "subgen_url": settings.subgen_url,
    }


_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", RevalidatingStaticFiles(directory=_STATIC_DIR), name="static")

    from fastapi.responses import RedirectResponse
    from fastapi import Request

    # v1.0 screens — high-fidelity React mockups from Claude Design.
    # All live under /static/v1/ so embedded relative paths
    # (home-hifi/tokens.css, home-hifi/atoms.jsx, etc.) resolve via the
    # existing StaticFiles mount. Each route below is a tidy URL that
    # redirects to the underlying .html so cross-screen <a href="/coverage">
    # navigation in the React chrome resolves naturally.
    _V1_DIR = _STATIC_DIR / "v1"
    if _V1_DIR.is_dir():
        # Screen → static-file map. Add a route per screen as design ships.
        # The pretty URL is the source of truth for cross-screen links in
        # chrome.jsx; the underlying .html file is an implementation detail.
        _V1_SCREENS = {
            "/home":       "home.html",
            "/coverage":   "coverage.html",
            "/onboarding": "onboarding.html",
            "/rules":      "rules.html",
            "/settings":   "settings.html",
            "/file-modal": "file-modal.html",
            "/queue":      "queue.html",
            "/library":    "library.html",
            "/logs":       "logs.html",
            "/review":     "review.html",  # v1.1.1: dedicated audio-lang review queue
            "/arena":      "arena.html",   # #131: tuning-lab config sweep
        }

        def _make_v1_route(html_file: str):
            def _v1_screen():
                return RedirectResponse(url=f"/static/v1/{html_file}", status_code=302)
            return _v1_screen

        for _path, _html in _V1_SCREENS.items():
            if (_V1_DIR / _html).is_file():
                app.add_api_route(_path, _make_v1_route(_html), methods=["GET"])

    # Root route — smart redirect based on onboarding state. First-time users
    # hit the wizard, configured installs land on Home. Legacy vanilla-JS UI
    # was retired in v1.0 (#118).
    @app.get("/")
    def index(request: Request):
        store = getattr(request.app.state, "onboarding", None)
        if store is not None:
            try:
                state = store.get()
                if not state.is_complete:
                    return RedirectResponse(url="/onboarding", status_code=302)
            except Exception as e:
                log.warning("onboarding state lookup failed at /: %r", e)
        return RedirectResponse(url="/home", status_code=302)
else:
    # Packaging regression — static assets weren't installed alongside the
    # package. Log loudly + return a useful 503 at / so the failure mode is
    # visible. See pyproject.toml [tool.setuptools.package-data] for the
    # canonical fix.
    log.error(
        "subarr static directory not found at %s — frontend will 404. "
        "Check pyproject.toml [tool.setuptools.package-data] subarr = ['static/**/*'].",
        _STATIC_DIR,
    )

    @app.get("/")
    def index_missing() -> dict:
        from fastapi import HTTPException
        raise HTTPException(503, detail=f"frontend not packaged — expected static dir at {_STATIC_DIR}")


def main() -> None:
    import uvicorn

    log.info("subarr %s on port %d", __version__, settings.port)
    uvicorn.run("subarr.app:app", host="0.0.0.0", port=settings.port, reload=False)  # nosec B104 — container deployment must bind 0.0.0.0 to be reachable from sibling containers


if __name__ == "__main__":
    main()
