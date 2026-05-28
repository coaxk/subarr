"""FastAPI app entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
from .routers import (
    admin, bazarr_sync, browse, coverage, coverage_actions,
    enrichment as r_enrichment, gpu, integrations, logs, mode,
    probe as r_probe, provenance as r_provenance, queue, scan,
    schedule as r_schedule,
)
from .scan_runner import ScanRunner
from .scan_store import ScanStore
from .schedule_store import ScheduleStore
from .scheduler import Scheduler
from .subgen_client import SubgenClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


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
    app_.state.scans = ScanStore(settings.db_path)
    app_.state.scans.init_schema()
    app_.state.runner = ScanRunner(app_.state.subgen, app_.state.scans)
    app_.state.docker = DockerOps()
    app_.state.integrations = IntegrationBundle()
    app_.state.provenance = ProvenanceStore(settings.db_path)
    app_.state.provenance.init_schema()
    app_.state.watcher = CompletionWatcher(
        subgen=app_.state.subgen,
        bazarr=app_.state.integrations.bazarr,
        provenance=app_.state.provenance,
    )
    app_.state.watcher.start()
    app_.state.schedule = ScheduleStore(settings.db_path)
    app_.state.schedule.init_schema()
    app_.state.ollama = OllamaClient()
    app_.state.enrichment = EnrichmentStore(settings.db_path)
    app_.state.enrichment.init_schema()
    app_.state.probe_store = ProbeStore(settings.db_path)
    app_.state.probe_store.init_schema()
    app_.state.probe_walker = ProbeWalker(app_.state.probe_store)
    app_.state.pending = PendingStore(settings.db_path)
    app_.state.pending.init_schema()
    app_.state.scheduler = Scheduler(
        schedule_store=app_.state.schedule,
        bundle=app_.state.integrations,
        scan_store=app_.state.scans,
        runner=app_.state.runner,
        provenance=app_.state.provenance,
        probe_walker=app_.state.probe_walker,
        pending_store=app_.state.pending,
    )
    app_.state.scheduler.start()
    try:
        yield
    finally:
        await app_.state.probe_walker.aclose()
        await app_.state.scheduler.stop()
        await app_.state.watcher.stop()
        await app_.state.runner.aclose()
        await app_.state.subgen.aclose()
        await app_.state.integrations.aclose()
        await app_.state.ollama.aclose()
        app_.state.scans.close()
        app_.state.provenance.close()
        app_.state.schedule.close()
        app_.state.enrichment.close()
        app_.state.probe_store.close()
        app_.state.pending.close()
        app_.state.docker.close()


app = FastAPI(title="subarr", version=__version__, lifespan=lifespan)

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
app.include_router(bazarr_sync.router)


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
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    # Index template is rendered with a startup-time cache-bust string baked
    # into static asset query strings (?v=<version>-<startupTs>). Same-
    # version rebuilds (in-place compose recreates) still bust browser
    # caches because the container's startup timestamp changes every boot.
    # Pure version-based busting was insufficient — caught when stuck-walk
    # badge showed stale data after a rebuild reused the same version
    # string. (2026-05-28)
    import time as _time
    _INDEX_TEMPLATE = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
    _CACHE_BUST = f"{__version__}-{int(_time.time())}"
    _INDEX_RENDERED = _INDEX_TEMPLATE.replace("__SUBARR_VERSION__", _CACHE_BUST)

    from fastapi.responses import HTMLResponse

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(_INDEX_RENDERED)

    # v1.0 screens — high-fidelity React mockups from Claude Design.
    # All live under /static/v1/ so embedded relative paths
    # (home-hifi/tokens.css, home-hifi/atoms.jsx, etc.) resolve via the
    # existing StaticFiles mount. Each route below is a tidy URL that
    # redirects to the underlying .html so cross-screen <a href="/coverage">
    # navigation in the React chrome resolves naturally.
    # Coexists with the legacy vanilla-JS UI at / during the migration.
    _V1_DIR = _STATIC_DIR / "v1"
    if _V1_DIR.is_dir():
        from fastapi.responses import RedirectResponse

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
        }

        def _make_v1_route(html_file: str):
            def _v1_screen():
                return RedirectResponse(url=f"/static/v1/{html_file}", status_code=302)
            return _v1_screen

        for _path, _html in _V1_SCREENS.items():
            if (_V1_DIR / _html).is_file():
                app.add_api_route(_path, _make_v1_route(_html), methods=["GET"])
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
    uvicorn.run("subarr.app:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
