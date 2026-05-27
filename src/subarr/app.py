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
    app_.state.scheduler = Scheduler(
        schedule_store=app_.state.schedule,
        bundle=app_.state.integrations,
        scan_store=app_.state.scans,
        runner=app_.state.runner,
        provenance=app_.state.provenance,
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

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")
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
