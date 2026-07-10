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
    """Static files with path-aware Cache-Control (issue #138).

    Default = `no-cache`: the v1 HTML references its JS bundles by a fixed
    name with NO content hash, so any heuristic caching serves STALE UI
    after an update. `no-cache` forces an ETag revalidation each load —
    cheap 304s when unchanged, fresh bundle the instant it changes.
    Bundles (v1/home-hifi/*.bundle.js) and HTML therefore stay no-cache.

    Exception = a 1-week revalidated cache for assets that are either
    content-stable or change only with a version bump: pinned vendor JS,
    favicons, flag SVGs, and the OG card. NOT `immutable` (filenames have
    no hash, so we still want a conditional revalidation, just not on
    every navigation). `path` here is RELATIVE to the mount root (e.g.
    "v1/vendor/react.production.min.js") — no "/static" prefix, no leading
    slash — so the prefixes below are matched in that space."""

    # Long-cache, version-stable assets, matched against the mount-relative
    # path (starlette passes get_response a prefix-less path).
    _LONG_CACHE_PREFIXES = (
        "v1/vendor/",
        "v1/flags/",
        "v1/favicon-",
    )
    _LONG_CACHE_EXACT = (
        "v1/favicon.svg",
        "v1/og-card.png",
    )

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        norm = path.replace("\\", "/").lstrip("/")
        if norm in self._LONG_CACHE_EXACT or norm.startswith(self._LONG_CACHE_PREFIXES):
            resp.headers["Cache-Control"] = "max-age=604800, must-revalidate"
        else:
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
from .pending_feeder import PendingQueueFeeder
from .pending_queue import PendingQueueStore
from .provenance import ProvenanceStore, SOURCE_SUBGENSCAN
from .onboarding import OnboardingStore, install_is_configured
from .routers import (
    admin,
    aftercare as r_aftercare,
    api_keys as r_api_keys,
    auth as r_auth,
    arbiter as r_arbiter,
    arena as r_arena,
    arr_mediainfo as r_arr_mediainfo,
    audio_audit as r_audio_audit,
    audio_lang as r_audio_lang,
    bazarr_sync,
    blacklist as r_blacklist,
    browse,
    coverage,
    coverage_actions,
    discovery as r_discovery,
    household as r_household,
    languages as r_languages,
    providers as r_providers,
    vision as r_vision,
    enrichment as r_enrichment,
    forced_segment as r_forced_segment,
    gpu,
    home as r_home,
    instances as r_instances,
    integrations,
    libraries as r_libraries,
    logs,
    mode,
    onboarding as r_onboarding,
    probe as r_probe,
    provenance as r_provenance,
    queue,
    scan,
    schedule as r_schedule,
    sidecar as r_sidecar,
    subgen_setup as r_subgen_setup,
    telemetry as r_telemetry,
    updates as r_updates,
    vad as r_vad,
)
from . import arena_explain as _arena_explain
from .arena import AsrRunner
from .arena_service import ArenaService
from .arena_store import ArenaStore
from .aftercare_store import AfterCareStore
from .existing_audit_service import ExistingAuditService
from .scan_runner import ScanRunner
from .scan_store import ScanStore
from .crash_store import CrashStore
from .data_persistence import check_data_persistence
from .db_integrity import check_db_integrity
from .error_store import ErrorStore
from .log_ring import LogRing
from .task_health import TaskHealthStore
from .schedule_store import ScheduleStore
from .scheduler import Scheduler
from .single_process import check_single_process
from .subgen_client import SubgenClient


# #157 gap-fill: the process-wide in-process log ring. Installed on the root
# logger by _apply_logging; the logs router reads it. None until installed.
LOG_RING: LogRing | None = None


def _apply_logging(debug: bool) -> None:
    """#157 gap-fill: configure logging levels from the SUBARR_DEBUG knob AND
    install the in-process LogRing on the root logger (idempotent).

    Off (default): today's behaviour byte-for-byte — root INFO, and the
    httpx/httpcore request loggers pinned to WARNING so the health/queue polls
    to subgen/sonarr/radarr/bazarr/tautulli/plex don't flood the log with
    "200 OK" lines and bury real signal.

    On: root -> DEBUG and httpx/httpcore UN-pinned (left at INFO) so request
    detail shows for "go nuts locally" debugging.
    """
    global LOG_RING
    root_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=root_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # basicConfig is a no-op once a handler exists (e.g. after importlib.reload),
    # so set the root level explicitly too.
    logging.getLogger().setLevel(root_level)
    http_level = logging.INFO if debug else logging.WARNING
    logging.getLogger("httpx").setLevel(http_level)
    logging.getLogger("httpcore").setLevel(http_level)

    # Install the LogRing exactly once (importlib.reload re-runs this module).
    root = logging.getLogger()
    if not any(isinstance(h, LogRing) for h in root.handlers):
        LOG_RING = LogRing(level=logging.INFO)  # capture INFO+ so the live tail is useful
        root.addHandler(LOG_RING)
    else:
        LOG_RING = next(h for h in root.handlers if isinstance(h, LogRing))


_apply_logging(settings.debug)
log = logging.getLogger(__name__)


def _subgen_update_version(release_tag: str | None) -> str | None:
    """Current subarr-subgen version for the update check (#224 + dev-build
    guard). Only a real release tag (e.g. 'v2026.05.3-r9') is comparable to
    the repo's release tags. A `dev-<sha>` local/soak build (scripts/build.sh)
    or a missing tag is unreleased and uncomparable, so it resolves to None —
    the checker then shows the latest release but never claims an update,
    rather than falsely nagging an upgrade that points BACKWARD to the last
    release."""
    if not release_tag or release_tag.startswith("dev"):
        return None
    return release_tag


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
        for a in getattr(pr, "audio", None) or []:
            code = normalize_lang(getattr(a, "language", None) or "")
            if code and code != "und":
                return code
    except Exception:
        pass
    # Tier 2: coverage's audio_langs (ffprobe + arr mediaInfo merged).
    try:
        cc = getattr(app_.state, "coverage_cache", None)
        snap = cc.get_cached() if cc is not None else None
        for it in getattr(snap, "items", None) or []:
            if it.get("file_canonical_path") == canon or it.get("canonical_path") == canon:
                for al in it.get("audio_langs") or []:
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
        for a in getattr(pr, "audio", None) or []:
            out.append(normalize_lang(getattr(a, "language", None) or "") or None)
    except Exception:
        return []
    return out


def _walk_all_library_files():
    """#134: enumerate every video file across ALL library roots for the
    audio-audit deep scan. Yields (canonical, mtime|None); canonicals carry
    @slug/ heads for non-default libraries (fs_to_canonical). Unreachable
    roots are skipped, never abort the audit."""
    import os
    from pathlib import Path as _P

    from .paths import VIDEO_EXTS, fs_to_canonical

    seen: set[str] = set()
    for lib in settings.libraries:
        try:
            if not lib.fs_root.is_dir():
                continue
            walk = os.walk(lib.fs_root)
        except Exception:
            continue
        for dirpath, _dirs, files in walk:
            for fn in files:
                dot = fn.rfind(".")
                if dot < 0 or fn[dot:].lower() not in VIDEO_EXTS:
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    c = fs_to_canonical(_P(fp))
                except Exception:
                    continue
                if c in seen:
                    continue
                seen.add(c)
                try:
                    mt = os.stat(fp).st_mtime
                except OSError:
                    mt = None
                yield (c, mt)


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
        log.info("schema migrations applied this boot: %s", [m.name for m in applied])

    # #238: auth. Migrations (above) created the auth tables before we open the
    # store. settings + store live on app.state for the auth router + gate.
    from .auth_store import AuthStore

    app_.state.settings = settings
    app_.state.auth_store = AuthStore(settings.db_path)
    if settings.auth_reset:
        app_.state.auth_store.clear_credential()
        log.warning("SUBARR_AUTH_RESET set — cleared the stored credential; first-run setup will show.")

    # #260 login brute-force throttle + the trusted-proxy set used to resolve the
    # real client IP behind a reverse proxy. Parsed once here from env config.
    from .login_throttle import LoginThrottle, parse_cidrs

    app_.state.trusted_proxies = parse_cidrs(settings.trusted_proxies)
    app_.state.login_throttle = LoginThrottle(
        max_attempts=settings.login_max_attempts,
        window_s=settings.login_window_s,
        allowlist=parse_cidrs(settings.login_allowlist),
    )

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
    app_.state.scans.prune()  # #197: scan history, 180d
    # Anonymous error-class log for telemetry (schema via migration 006).
    app_.state.errors = ErrorStore(settings.db_path)
    app_.state.errors.prune()  # #197: telemetry reads 30d; keep 60d
    # #157 P2: sanitized fleet crash aggregates (type + module:line only).
    # Fed by TaskHealthStore.record_failure below; aggregated into the daily
    # ping as crash_counts_24h. Pruned on boot (error_events never was — don't
    # repeat that gap).
    app_.state.crashes = CrashStore(settings.db_path)
    app_.state.crashes.prune(days=30)
    # #157: per-loop health so a silently-crashing background task (the #79
    # class) surfaces instead of freezing quietly. Each supervised loop records
    # success/failure per cycle; /api/health/tasks + the header pill read it.
    app_.state.task_health = TaskHealthStore(settings.db_path, crash_recorder=app_.state.crashes.record)
    # #196: quick_check on boot — irreplaceable user data (verifications,
    # intents, provenance) lives in this one file, and corruption must be
    # loud (Health page + red pill), not silently half-working. Never blocks
    # boot on failure; milliseconds on healthy files.
    check_db_integrity(settings.db_path, app_.state.task_health)
    # #196/#202: warn loudly if /data isn't a persistent volume (recreate =
    # all verifications/intents lost + a fresh telemetry id). Verdict cached
    # for the telemetry data_persistent signal. None = unknown (not a container).
    app_.state.data_persistent = check_data_persistence(settings.db_path, app_.state.task_health)
    # #291: advisory warning — WAL over NFS/SMB risks corruption; log only,
    # not a hard failure (the setup may be fine, e.g. NFS with local locking).
    from .data_persistence import data_dir_network_fs

    _net_fs = data_dir_network_fs(settings.db_path)
    if _net_fs is not None:
        log.warning(
            "NFS/network-FS WARNING: /data is on a %s filesystem. "
            "SQLite WAL mode over network filesystems risks corruption. "
            "Recommend mounting a local-disk volume at /data.",
            _net_fs,
        )
    # #204: exactly one subarr process per database. The handle must live on
    # app.state — the flock dies with it. Second worker / second container on
    # the same /data → red Health task + loud log (we warn, never brick boot).
    app_.state.process_lock = check_single_process(settings.db_path, app_.state.task_health)
    # Seed the roster so all supervised loops show on the Health page from boot
    # (as "never run yet"), not only after their first cycle. Each loop's first
    # record_success/failure carries its real cadence and corrects these.
    for _tname, _tiv in (
        (
            "coverage-cache",
            600,
        ),  # 2x refresh interval — covers sleep + 60-90s build (see background_refresh_loop)
        ("dashboard-cache", 30),
        ("scheduler", 60),
        ("completion-watcher", 30),
        ("update-checker", 86400),
        ("subgen-watchdog", 30),
        ("queue-feeder", 5),  # #66/#116
        # #157 gap-fill: opt-in foreground walker. expected_interval_s=None so
        # the staleness branch never fires — an idle walker shows failures/streak
        # WITHOUT a false "stale" alarm, and sits on the unified Health roster.
        ("audio-audit", None),
        # #364: opt-in deep-scan walker. expected_interval_s=None (event-driven,
        # never "stale") — sits on the unified Health roster like audio-audit.
        ("forced-segment", None),
    ):
        app_.state.task_health.register(_tname, expected_interval_s=_tiv)
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
    # #136: age-based retention — arena_runs is append-only and grows unbounded.
    # Prune sweeps older than the retention window on boot (0/negative disables).
    if settings.arena_retention_days > 0:
        import time as _time

        _cutoff = _time.time() - settings.arena_retention_days * 86400
        _pruned = app_.state.arena_store.prune_older_than(_cutoff)
        if _pruned:
            log.info(
                "arena: pruned %d sweep(s) older than %d days on boot", _pruned, settings.arena_retention_days
            )
    app_.state.arena = ArenaService(
        app_.state.arena_store,
        build_runner=lambda run: AsrRunner(
            app_.state.subgen,
            capabilities=getattr(app_.state, "subgen_caps", None),
            source_language=run.source_language,
            track=getattr(run, "track_index", 0),
        ),
        # ollama EXPLAINS the result in plain language (not scoring). Resolved
        # live so an onboarding ollama-config swap is picked up without restart.
        explainer=lambda result, media_path: _arena_explain.explain(
            result, media_path, getattr(app_.state, "ollama", None)
        ),
        # Fallback source language when Whisper robust detection is inconclusive:
        # the file's KNOWN audio language from the ffprobe tag (probe_store).
        # Resolved live (probe_store is created later in lifespan; this closure
        # runs only at sweep time, by which point it exists).
        lang_fallback=lambda media_path: _arena_fallback_lang(app_, media_path),
        # Audio-track languages → 'multitrack' advisory (original + dub etc.).
        track_info=lambda media_path: _arena_audio_tracks(app_, media_path),
        subgen_provider=lambda: app_.state.subgen,
        caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
    )
    app_.state.docker = DockerOps()
    app_.state.integrations = IntegrationBundle()
    app_.state.provenance = ProvenanceStore(settings.db_path)
    # #156: construct aftercare store before CompletionWatcher so it can be
    # passed in directly (watcher judges each completed job's .srt on the fly).
    app_.state.aftercare = AfterCareStore(settings.db_path)
    app_.state.aftercare.prune()  # #197: 365d, never touches unreviewed flags
    # #216: library-wide audit of EXISTING external subs, single-flight bg task.
    app_.state.existing_audit = ExistingAuditService(
        aftercare_store=app_.state.aftercare,
        provenance=app_.state.provenance,
        libraries=settings.libraries,
    )

    def _probe_duration_lookup(canonical: str) -> float | None:
        """#216: the file's ffprobe duration for aftercare's sync-overrun
        signal. Resolved live — probe_store is constructed later in lifespan
        (same pattern as the arena fallback-lang closure above)."""
        try:
            store = getattr(app_.state, "probe_store", None)
            pr = store.get((canonical or "").strip().lstrip("/")) if store is not None else None
            return getattr(pr, "duration_s", None)
        except Exception:
            return None

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
        aftercare_store=app_.state.aftercare,
        duration_lookup=_probe_duration_lookup,
    )
    app_.state.watcher._health = app_.state.task_health  # #157 supervision
    app_.state.watcher.start()
    app_.state.schedule = ScheduleStore(settings.db_path)

    # #66/#116: queue authority. subarr's pending queue + a depth-aware feeder
    # that drains it into subgen one job at a time (single-file /batch, reusing
    # the scan_store/runner/provenance plumbing so completion tracking is
    # unchanged). Idle until producers route through it (later slice) — starting
    # it now just activates the supervised loop. target_depth + pause read live
    # from the auto-queue rules so the API can toggle them without a restart.
    app_.state.pending_queue = PendingQueueStore(settings.db_path)

    async def _feeder_submit(job) -> None:
        scan = app_.state.scans.create([job.canonical_path], reverse=False)
        app_.state.runner.start(
            scan,
            audio_language_override=job.audio_language_override,
            ignore_forced=job.ignore_forced,
        )
        # Full provenance (series_id carried on the job) so completion_watcher
        # fires Bazarr's scan-disk task the moment subgen finishes (#66/#116 s6).
        app_.state.provenance.record(
            canonical_path=job.canonical_path,
            scan_id=scan.id,
            source=SOURCE_SUBGENSCAN,
            series_id=job.series_id,
            sonarr_episode_id=job.sonarr_episode_id,
            radarr_movie_id=job.radarr_movie_id,  # #368: movie upload provenance
        )

    app_.state.queue_feeder = PendingQueueFeeder(
        store=app_.state.pending_queue,
        subgen_provider=lambda: app_.state.subgen,
        submit_job=_feeder_submit,
        target_depth_provider=lambda: app_.state.schedule.get_rules().queue_target_depth,
        paused_provider=lambda: app_.state.schedule.get_rules().queue_paused,
        caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
        arena_inflight_provider=lambda: app_.state.arena.inflight_count(),
    )
    app_.state.queue_feeder._health = app_.state.task_health  # #157 supervision
    app_.state.queue_feeder.start()

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
    # #155 phase 2: library-wide audio-language audit walker. OPT-IN (started
    # only via POST /api/audio-audit/start) + throttled + GPU-polite (yields to
    # live tuning-lab sweeps). The worklist is the coverage snapshot's files
    # that have a known audio tag; busy_check reports active arena sweeps.
    from .audio_audit import AudioAuditWalker
    from .audio_audit_store import AudioAuditStore
    from .langs import normalize_lang as _normalize_lang

    def _safe_mtime(canonical: str):
        # File mtime makes the walk resumable (a re-scan skips unchanged files).
        # Best-effort: a missing/unreadable file just gets None (always re-checked).
        try:
            from .paths import canonical_to_fs as _c2fs

            return _c2fs(canonical).stat().st_mtime
        except Exception:
            return None

    def _coverage_tag_map():
        # {canonical_path: tag_lang} for files the coverage snapshot knows — used
        # to give library-scope files a tag (so mislabel can fire) when one exists.
        cc = getattr(app_.state, "coverage_cache", None)
        snap = cc.get_cached() if cc is not None else None
        out = {}
        for it in (snap.items if snap is not None else None) or []:
            c = it.get("file_canonical_path") or it.get("canonical_path")
            audio = it.get("audio_langs") or []
            if c and audio:
                tag = _normalize_lang(audio[0])
                if tag:
                    out[c] = tag
        return out

    def _coverage_worklist():
        # Default scope: the tracked coverage set, files with a known audio tag.
        cc = getattr(app_.state, "coverage_cache", None)
        snap = cc.get_cached() if cc is not None else None
        if snap is None:
            return []
        out = []
        seen = set()
        for it in snap.items or []:
            c = it.get("file_canonical_path") or it.get("canonical_path")
            audio = it.get("audio_langs") or []
            if not c or not audio or c in seen:
                continue
            tag = _normalize_lang(audio[0])
            if not tag:
                continue
            seen.add(c)
            out.append((c, tag, _safe_mtime(c)))
        return out

    def _library_worklist():
        # Opt-in deep scan: every video file across ALL library roots (#134),
        # not just the tracked set. Tags come from coverage where known (else
        # None → bilingual/multitrack still detected, mislabel needs a tag to
        # disagree with).
        tag_map = _coverage_tag_map()
        return [(c, tag_map.get(c), mt) for c, mt in _walk_all_library_files()]

    def _audit_worklist(scope: str = "coverage"):
        return _library_worklist() if scope == "library" else _coverage_worklist()

    def _audit_busy():
        arena = getattr(app_.state, "arena", None)
        if arena is None:
            return False
        try:
            return any(r.status in ("running", "queued", "pending") for r in arena.list())
        except Exception:
            return False

    app_.state.audio_audit_store = AudioAuditStore(settings.db_path)
    app_.state.audio_audit = AudioAuditWalker(
        app_.state.subgen,
        app_.state.audio_audit_store,
        worklist=_audit_worklist,
        probe_store=app_.state.probe_store,
        busy_check=_audit_busy,
        audio_lang=app_.state.audio_lang,
    )
    app_.state.audio_audit._health = app_.state.task_health  # #157 supervision

    # #364: forced-segment deep-scan pipeline (opt-in; OFF by default). Store +
    # generator + walker + at-import hook. LID + translate are bound to the subgen
    # client. Slice 1 ships Branch B ONLY (clip uploaded to subgen /asr for LID):
    # it works for every deployment (co-located OR remote subgen, no shared fs).
    # The cheap path-based Branch A needs subgen-visible-scratch plumbing to
    # resolve clips, and slice 2's LOCAL LID model supersedes the subgen-LID
    # mechanism entirely — so we pass subgen_scratch_prefix=None (→ Branch B) and
    # don't expose a Branch-A selector that would only half-work.
    from . import lid as _lid
    from .forced_segment import ForcedSegmentParams, qualifies_for_forced_segment
    from .forced_segment_lid import LocalLidBackend
    from .forced_segment_service import (
        ForcedSegmentGenerator,
        ForcedSegmentWalker,
        subgen_lid,
        subgen_translate,
    )
    from .forced_segment_store import ForcedSegmentScanStore
    from .media_probe import audio_lang_summary, english_track_summary

    # #364 slice 2: prefer the local silero-lang95 backend over the per-utterance
    # subgen LID path (Branch B) when the model is available. Guarded on
    # forced_segment_enabled so a disabled install never triggers a model pull.
    _local_lid = None
    if settings.forced_segment_enabled and _lid.ensure_available():
        _local_lid = LocalLidBackend(params=ForcedSegmentParams())
        log.info("forced-segment: local silero-lang95 LID active")
    elif settings.forced_segment_enabled:
        log.info("forced-segment: silero-lang95 unavailable — using subgen LID fallback")

    app_.state.forced_segment_store = ForcedSegmentScanStore(settings.db_path)

    async def _fs_lid(clip_path, subgen_clip_path, _span):
        # subgen_clip_path is always None in slice 1 (scratch prefix disabled) ->
        # subgen_lid takes the Branch B upload path. Kept in the signature so
        # slice 2 can re-enable the cheap path without a wiring change.
        return await subgen_lid(app_.state.subgen, clip_path, subgen_clip_path=subgen_clip_path)

    async def _fs_translate(clip_path, _span):
        return await subgen_translate(app_.state.subgen, clip_path)

    def _fs_gate(canonical: str):
        # Resolve gate inputs from stores already built above. Cheap: probe cache
        # (audio + duration + embedded-forced), audio-lang store (#357 lang_class),
        # and a disk check for an existing .forced.en.srt sidecar.
        from .paths import PathOutsideRootError as _PORE
        from .paths import canonical_to_fs as _c2fs

        pr = None
        try:
            pr = app_.state.probe_store.get(canonical)
        except Exception:
            pr = None
        audio_langs = audio_lang_summary(pr) if pr is not None else []
        embedded_en = english_track_summary(pr) if pr is not None else None
        duration_s = getattr(pr, "duration_s", None) if pr is not None else None
        size = None
        has_sidecar = False
        try:
            fs = _c2fs(canonical)
            if fs.exists():
                size = fs.stat().st_size
                has_sidecar = fs.with_name(fs.stem + ".forced.en.srt").exists()
        except (_PORE, OSError):
            pass
        lang_class = "single"
        try:
            v = app_.state.audio_lang.get(canonical.lstrip("/"))
            if v is not None:
                lang_class = getattr(v, "lang_class", "single")
        except Exception:
            lang_class = "single"
        ok, reason = qualifies_for_forced_segment(
            audio_langs=audio_langs,
            embedded_en=embedded_en,
            lang_class=lang_class,
            has_forced_sidecar=has_sidecar,
            duration_s=duration_s,
            params=ForcedSegmentParams(),
        )
        return ok, reason, duration_s, size

    app_.state.forced_segment_gen = ForcedSegmentGenerator(
        subgen=app_.state.subgen,
        scan_store=app_.state.forced_segment_store,
        params=ForcedSegmentParams(),
        lid_fn=_fs_lid,
        translate_fn=_fs_translate,
        gate_fn=_fs_gate,
        aftercare_store=getattr(app_.state, "aftercare", None),
        subgen_scratch_prefix=None,  # #364 slice 1: Branch B only (see above)
        local_lid=_local_lid,  # #364 slice 2: local silero-lang95 backend, or None
    )
    app_.state.forced_segment = ForcedSegmentWalker(
        generator=app_.state.forced_segment_gen,
        worklist=lambda scope="library": [c for c, _mt in _walk_all_library_files()],
        busy_check=_audit_busy,  # reuse the arena-busy check (GPU-polite)
    )
    app_.state.forced_segment._health = app_.state.task_health  # #157 supervision
    app_.state.watcher._forced_segment = app_.state.forced_segment_gen  # at-import hook

    app_.state.pending = PendingStore(settings.db_path)
    app_.state.pending.prune()  # #197: resolved walks 30d; pending never pruned
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
        pending_queue=app_.state.pending_queue,  # #66/#116 slice 6
        # #79: live caps so the coverage_walk forced-only-EN gate tracks the
        # runtime IGNORE_FORCED_SUBTITLES value.
        caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
    )
    app_.state.scheduler._health = app_.state.task_health  # #157 supervision
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
            health=app_.state.task_health,  # #157
        )
    )
    # Dashboard cache background refresh — passes a build closure that
    # reuses the existing /api/home/dashboard internals.
    from .routers.home import _build_dashboard as _dash_build

    app_.state.dashboard_cache_task = asyncio.create_task(
        dash_refresh_loop(
            cache=app_.state.dashboard_cache,
            build_fn=lambda: _dash_build(app_.state),
            health=app_.state.task_health,  # #157
        )
    )

    # Event-loop stall detector — logs a WARNING when synchronous work blocks
    # the loop (the class of bug that freezes every concurrent request at once).
    from .loop_lag import monitor_event_loop_lag

    app_.state.loop_lag_task = asyncio.create_task(monitor_event_loop_lag())

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
    # #224: the #108 comparison broke when subarr-subgen's tagging moved
    # from patch-rev style (v4.x) to date style (v2026.05.3-rN) — the
    # patch_rev can never string-match a tag, so every install (including
    # a fully-current one) showed a permanent fake "update available".
    # Trust rule: only claim a current version when subgen reports its
    # actual RELEASE TAG (`release_tag` in caps, shipped by subarr-subgen
    # r9+). Unknown current → the checker shows the latest release but
    # never claims an update (missed_releases/update_available both treat
    # None as "don't guess"). Vanilla subgen comparison is equally broken
    # against our repo's tags — #223 redoes that flow properly.
    _subgen_current = _subgen_update_version(
        getattr(_subgen_caps, "release_tag", None) if _subgen_caps else None
    )
    from .update_checker import (
        DEFAULT_PRODUCTS,
        VANILLA_SUBGEN_PRODUCT,
        VANILLA_SUBGEN_RAW_URL,
        VANILLA_SUBGEN_REPO,
        UpdateChecker,
    )

    # #223: show ONE subgen-family row matched to the CONNECTED subgen. Vanilla
    # McCloudS/subgen has no releases, so it can't use the Atom feed — route it
    # through the version-constant path against its installed version. When the
    # build is our subarr-subgen (or subgen is unreachable, where the default
    # image is the safe assumption), keep the Atom-feed product.
    _is_vanilla = _subgen_caps is not None and getattr(_subgen_caps, "is_subarr_subgen", True) is False
    if _is_vanilla:
        products = {
            "subarr": DEFAULT_PRODUCTS["subarr"],
            VANILLA_SUBGEN_PRODUCT: VANILLA_SUBGEN_REPO,
        }
        vanilla_products = {VANILLA_SUBGEN_PRODUCT: VANILLA_SUBGEN_RAW_URL}
        current_versions = {
            "subarr": __version__,
            # the upstream version subgen reports via its API (e.g. '2026.06.4')
            VANILLA_SUBGEN_PRODUCT: getattr(_subgen_caps, "version", None),
        }
    else:
        products = DEFAULT_PRODUCTS
        vanilla_products = None
        current_versions = {
            "subarr": __version__,
            "subarr-subgen": _subgen_current,
        }
    app_.state.update_checker = UpdateChecker(
        db_path=settings.db_path,
        products=products,
        current_version_resolver=current_versions,
        vanilla_products=vanilla_products,
    )
    app_.state.update_checker._health = app_.state.task_health  # #157 supervision
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
                cutoff,
                completed_paths=completed,
                live_basenames=live_basenames,
            )
            log.warning(
                "subgen restart reconciliation: %d in-flight scan_store "
                "entries marked ORPHANED (provenance confirms %d completed "
                "in last 24h, preserved as ok)",
                n,
                len(completed),
            )
        except Exception as e:
            log.error("subgen restart reconciliation failed: %s", e, exc_info=True)

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
    app_.state.subgen_watchdog._health = app_.state.task_health  # #157 supervision
    app_.state.subgen_watchdog.start()

    # Anonymous telemetry — ON by default per v1.0 product decision.
    # Opt-out one-click in Settings. Stats published publicly at
    # subarr.com/stats so users see what we see.
    from .telemetry import TelemetryCollector, make_default_stats_provider

    async def _refresh_subgen_caps() -> None:
        # #202: re-probe subgen right before a telemetry send so subgen_kind is
        # current, not the boot/default-URL snapshot. Best-effort (the collector
        # already wraps this in try/except).
        subgen = getattr(app_.state, "subgen", None)
        if subgen is not None:
            app_.state.subgen_caps = await subgen.probe_capabilities()

    app_.state.telemetry = TelemetryCollector(
        db_path=settings.db_path,
        endpoint=settings.telemetry_endpoint,
        subarr_version=__version__,
        stats_provider=make_default_stats_provider(app_.state),
        subgen_caps_provider=lambda: getattr(app_.state, "subgen_caps", None),
        subgen_caps_refresher=_refresh_subgen_caps,
    )
    app_.state.telemetry._health = app_.state.task_health  # #289 supervision
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
        try:
            await app_.state.audio_audit.aclose()
        except (AttributeError, Exception):
            pass
        try:
            await app_.state.forced_segment.aclose()
        except (AttributeError, Exception):
            pass
        try:
            await app_.state.arena.aclose()  # cancel + await in-flight sweeps before store close
        except (AttributeError, Exception):
            pass
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
        try:
            app_.state.loop_lag_task.cancel()
            await app_.state.loop_lag_task
        except (asyncio.CancelledError, AttributeError):
            pass
        await app_.state.watcher.stop()
        try:
            await app_.state.queue_feeder.stop()  # #66/#116
        except (AttributeError, Exception):
            pass
        await app_.state.runner.aclose()
        await app_.state.subgen.aclose()
        await app_.state.integrations.aclose()
        await app_.state.ollama.aclose()
        app_.state.scans.close()
        app_.state.errors.close()
        app_.state.task_health.close()  # #157
        app_.state.provenance.close()
        app_.state.schedule.close()
        try:
            app_.state.pending_queue.close()  # #66/#116
        except (AttributeError, Exception):
            pass
        app_.state.enrichment.close()
        app_.state.probe_store.close()
        try:
            app_.state.audio_audit_store.close()
        except (AttributeError, Exception):
            pass
        try:
            app_.state.forced_segment_store.close()
        except (AttributeError, Exception):
            pass
        app_.state.pending.close()
        app_.state.onboarding.close()
        try:
            app_.state.arena_store.close()
        except (AttributeError, Exception):
            pass
        try:
            app_.state.aftercare.close()  # #156
        except (AttributeError, Exception):
            pass
        app_.state.docker.close()


app = FastAPI(title="subarr", version=__version__, lifespan=lifespan)

# Middleware stack (#138). Starlette wraps the LAST-added middleware
# OUTERMOST, so registration order below is the REVERSE of execution
# order. Target, from the wire inward:
#   BasicAuth -> ApiKey -> Csrf -> SecurityHeaders -> GZip -> routes
# so register inward-first: GZip, SecurityHeaders, Csrf, ApiKey, BasicAuth.
#   - GZip compresses route/static bodies >=1KB (text/JSON/JS/CSS).
#   - SecurityHeaders stamps CSP + hardening headers on every response.
#   - Csrf (#198) rejects cross-origin browser writes to /api/* (default on).
#   - ApiKey (#198) gates /api/* when SUBARR_API_KEY is set (no-op otherwise).
#   - BasicAuth is outermost so a 401 challenge returns before any body
#     is built or compressed. No-op when SUBARR_USER/SUBARR_PASS unset.
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402
import secrets as _secrets  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from .security_headers import SecurityHeadersMiddleware  # noqa: E402
from .auth import AuthGateMiddleware, session_cookie_name as _session_cookie_name  # noqa: E402
from .api_security import CsrfOriginMiddleware  # noqa: E402
from .request_crash_capture import RequestCrashCaptureMiddleware  # noqa: E402

# #238: signed session-cookie secret. Priority: SUBARR_SESSION_SECRET env →
# the secret PERSISTED in the DB (auth_secret table) → an ephemeral per-boot
# value. The DB-persisted secret means logins survive restarts AND uvicorn
# --reload with no env required (the ephemeral fallback was silently logging
# everyone out on every restart/reload). The DB read is best-effort + guarded;
# if the data dir isn't reachable at import we fall back to ephemeral.
from .auth_store import load_or_create_session_secret as _load_secret  # noqa: E402

_session_secret = settings.session_secret or _load_secret(settings.db_path)
if not _session_secret:
    _session_secret = _secrets.token_urlsafe(48)
    log.warning(
        "session secret is EPHEMERAL (no SUBARR_SESSION_SECRET and the DB-persisted "
        "secret was unavailable) — logins reset on restart. This should be rare; set "
        "SUBARR_SESSION_SECRET to a long random string if it persists."
    )

# #199: innermost — sees only exceptions that escaped route handlers (500s),
# records the sanitized (type, module:line) into CrashStore, re-raises. The
# recorder resolves app.state lazily because the store is built in lifespan,
# after this module-level registration.
app.add_middleware(
    RequestCrashCaptureMiddleware,
    crash_recorder=lambda exc: getattr(app.state, "crashes", None) and app.state.crashes.record(exc),
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CsrfOriginMiddleware, enabled=settings.csrf_protection)
# #238: forced-auth gate. Replaces the optional BasicAuth + ApiKey middlewares —
# the gate accepts session OR env-basic OR api-key as principals (current_principal).
# SessionMiddleware is registered LAST so it is OUTERMOST: it populates
# scope["session"] before the gate reads it.
app.add_middleware(
    AuthGateMiddleware,
    settings=settings,
    get_store=lambda: getattr(app.state, "auth_store", None),
)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    # #411: instance-distinct cookie name (derived from the secret) so two subarr
    # copies on the same host don't clobber each other's login cookie.
    session_cookie=_session_cookie_name(_session_secret),
    same_site=settings.cookie_samesite,
    https_only=(settings.cookie_samesite == "none"),
    max_age=14 * 24 * 3600,
)

app.include_router(r_auth.router)
app.include_router(r_api_keys.router)
app.include_router(browse.router)
app.include_router(mode.router)
app.include_router(queue.router)
app.include_router(scan.router)
app.include_router(gpu.router)
app.include_router(logs.router)
app.include_router(admin.router)
app.include_router(integrations.router)
app.include_router(r_instances.router)
app.include_router(r_libraries.router)
app.include_router(coverage.router)
app.include_router(coverage_actions.router)
app.include_router(r_languages.router)
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
app.include_router(r_audio_audit.router)
app.include_router(r_forced_segment.router)
app.include_router(r_aftercare.router)
app.include_router(r_subgen_setup.router)


@app.get("/api/health")
def health() -> dict:
    # This is the ONE endpoint reachable before auth (allowlisted in auth.py).
    # Keep it to a liveness signal — don't leak config (media_root / subgen_url)
    # to an unauthenticated caller. Configured paths/URLs are available behind
    # auth via the integrations + settings endpoints.
    return {"status": "ok", "version": __version__}


@app.get("/api/health/tasks")
def health_tasks() -> dict:
    """#157: per-loop background-task health. Powers the header pill (any
    unhealthy) + the Health page (per-task status, last error, traceback).
    #234: each task carries `can_run_now` so the page shows a Run-now button
    on the loops that support an on-demand trigger."""
    from .jobs import can_run_now

    th = getattr(app.state, "task_health", None)
    states = th.states() if th is not None else []
    return {
        "tasks": [{**t.to_dict(), "can_run_now": can_run_now(t.task_name)} for t in states],
        "any_unhealthy": any(t.is_unhealthy for t in states),
        "unhealthy_count": sum(1 for t in states if t.is_unhealthy),
        "version": __version__,  # for the "Report a problem" prefill
    }


@app.post("/api/health/tasks/{task_name}/run")
async def run_health_task(task_name: str) -> dict:
    """#234: trigger a background loop on demand. 400 if the job isn't
    runnable; 409 if its component isn't available yet (early boot)."""
    from fastapi import HTTPException

    from .jobs import can_run_now, run_job

    if not can_run_now(task_name):
        raise HTTPException(400, detail=f"job {task_name!r} does not support run-now")
    if not await run_job(app.state, task_name):
        raise HTTPException(409, detail=f"job {task_name!r} could not be triggered right now")
    return {"ran": True, "task_name": task_name}


_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", RevalidatingStaticFiles(directory=_STATIC_DIR), name="static")

    # Browsers request /favicon.ico unconditionally (#138). Serve the
    # existing 32px PNG; the .ico extension in the URL is just convention.
    # 1-week revalidated cache to match the other version-stable favicons.
    _FAVICON_PNG = _STATIC_DIR / "v1" / "favicon-32.png"
    if _FAVICON_PNG.is_file():

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(
                _FAVICON_PNG,
                media_type="image/png",
                headers={"Cache-Control": "max-age=604800, must-revalidate"},
            )

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
            "/login": "auth.html",  # #238: forced-auth setup/login (gate-bypassed)
            "/setup": "auth.html",
            "/home": "home.html",
            "/coverage": "coverage.html",
            "/onboarding": "onboarding.html",
            "/rules": "rules.html",
            "/settings": "settings.html",
            "/file-modal": "file-modal.html",
            "/queue": "queue.html",
            "/library": "library.html",
            "/logs": "logs.html",
            "/review": "review.html",  # v1.1.1: dedicated audio-lang review queue
            "/arena": "arena.html",  # #131: tuning-lab config sweep
            "/health": "health.html",  # #157: background-task health
            "/aftercare": "aftercare.html",  # #156: job aftercare review
        }

        def _make_v1_route(html_file: str):
            # #165: forward the query string — deep-links like /arena?path=…
            # must survive the redirect or the page mounts with an empty
            # window.location.search and the pre-seed silently no-ops.
            def _v1_screen(request: Request):
                q = f"?{request.url.query}" if request.url.query else ""
                return RedirectResponse(url=f"/static/v1/{html_file}{q}", status_code=302)

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
                # #262: only force the wizard on a genuine first run. An install
                # configured via env vars never completes the wizard, so gating
                # on is_complete alone dragged established users into a blank
                # wizard after login. Explicit "Re-run setup" links straight to
                # /onboarding and so is unaffected by this gate.
                if not state.is_complete and not install_is_configured(settings):
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
