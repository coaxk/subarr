"""FastAPI app entry point."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .docker_client import DockerOps
from .routers import admin, browse, gpu, logs, mode, queue, scan
from .scan_runner import ScanRunner
from .scan_store import ScanStore
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
    try:
        yield
    finally:
        await app_.state.runner.aclose()
        await app_.state.subgen.aclose()
        app_.state.scans.close()
        app_.state.docker.close()


app = FastAPI(title="subarr", version=__version__, lifespan=lifespan)

app.include_router(browse.router)
app.include_router(mode.router)
app.include_router(queue.router)
app.include_router(scan.router)
app.include_router(gpu.router)
app.include_router(logs.router)
app.include_router(admin.router)


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


def main() -> None:
    import uvicorn

    log.info("subarr %s on port %d", __version__, settings.port)
    uvicorn.run("subarr.app:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
