"""Shared fixtures: env isolation + module reload + module-level subgen stub."""
from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import pytest


def _make_compose(p: Path) -> None:
    p.write_text(
        "services:\n"
        "  subgen:\n"
        "    environment:\n"
        "      SUBGEN_KWARGS: '{\"patience\": 1.5, \"length_penalty\": 0.85}'\n"
        "      SUBGEN_KWARGS_LANG_JA: '{\"patience\": 1.0}'\n"
    )


@pytest.fixture
def media_root(tmp_path: Path) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    (root / "TV").mkdir()
    (root / "TV" / "Show").mkdir()
    (root / "TV" / "Show" / "ep.mkv").write_bytes(b"")
    (root / "TV" / "Show" / "ep.en.srt").write_text("1\n")
    return root


@pytest.fixture
def subarr_env(monkeypatch, tmp_path: Path, media_root: Path):
    compose = tmp_path / "compose.yaml"
    _make_compose(compose)
    monkeypatch.setenv("SUBARR_MEDIA_ROOT", str(media_root))
    monkeypatch.setenv("SUBGEN_COMPOSE_PATH", str(compose))
    monkeypatch.setenv("SUBARR_DB_PATH", str(tmp_path / "subarr.db"))
    monkeypatch.setenv("SUBGEN_URL", "http://subgen.test:9000")
    monkeypatch.setenv("PLEX_URL", "http://plex.test:32400")
    monkeypatch.setenv("PLEX_TOKEN", "test-token")
    monkeypatch.setenv("PLEX_SECTION", "all")

    from subarr import app as app_mod
    from subarr import config, docker_client, paths, scan_runner, scan_store, subgen_client
    from subarr.routers import admin, browse, gpu, logs, mode, queue, scan

    for m in [
        config, paths, scan_store, subgen_client, scan_runner, docker_client,
        browse, mode, queue, scan, gpu, logs, admin, app_mod,
    ]:
        importlib.reload(m)

    yield


@pytest.fixture
def app_with_stub(subarr_env, request):
    """Build a TestClient whose subgen client is a fake driven by `subgen_handler`.

    Override per-test by setting `request.node.subgen_handler` or via
    `pytest.mark.subgen(handler=...)`.
    """
    from fastapi.testclient import TestClient

    from subarr.app import app
    from subarr.subgen_client import SubgenClient

    marker = request.node.get_closest_marker("subgen")
    handler = marker.kwargs.get("handler") if marker else None

    def default_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/queue":
            return httpx.Response(200, json={
                "queued": [], "processing": [], "queued_count": 0,
                "processing_count": 0, "idle": True, "version": "test",
            })
        if req.url.path == "/batch":
            return httpx.Response(200, json={
                "walked": 1, "queued": 1, "skipped": 0, "already_in_queue": 0,
                "no_audio": 0, "pending_language_detect": 0,
                "path": req.url.params.get("directory"), "reverse": False,
            })
        return httpx.Response(404, json={"detail": "stub: unhandled"})

    real_handler = handler or default_handler
    transport = httpx.MockTransport(real_handler)

    class _StubClient(SubgenClient):
        def __init__(self):
            super().__init__()
            # swap the transport on the inner httpx.AsyncClient
            self._client = httpx.AsyncClient(base_url="http://subgen.test:9000", transport=transport)

    # TestClient triggers lifespan; intercept the subgen client + docker ops.
    import subarr.app as app_mod
    app_mod.SubgenClient = _StubClient  # type: ignore[attr-defined]

    docker_marker = request.node.get_closest_marker("docker_stub")
    docker_kwargs = docker_marker.kwargs if docker_marker else {}
    app_mod.DockerOps = _make_docker_stub(**docker_kwargs)  # type: ignore[attr-defined]

    with TestClient(app) as c:
        yield c


def _make_docker_stub(
    container_running: bool = True,
    container_unavailable: bool = False,
    log_lines: list[str] | None = None,
):
    from subarr.docker_client import DockerOps, DockerUnavailable

    class _StubDocker(DockerOps):
        def __init__(self):
            super().__init__()
            self._restart_calls = 0

        def _get(self):
            if container_unavailable:
                raise DockerUnavailable("stub: docker unavailable")
            return object()  # never actually used in stub methods below

        async def restart_subgen(self, timeout: int = 30) -> None:
            if container_unavailable:
                raise DockerUnavailable("stub: docker unavailable")
            self._restart_calls += 1

        async def container_info(self) -> dict:
            if container_unavailable:
                raise DockerUnavailable("stub: docker unavailable")
            return {
                "name": "subgen",
                "status": "running" if container_running else "exited",
                "running": container_running,
                "started_at": "2026-05-27T20:40:15Z",
                "image": "mccloud/subgen:latest",
                "id_short": "abc123def456",
            }

        async def stream_subgen_logs(self, tail: int = 200):
            if container_unavailable:
                raise DockerUnavailable("stub: docker unavailable")
            for line in (log_lines or ["INFO:root:line one", "INFO:root:line two"]):
                yield line

    return _StubDocker


def pytest_configure(config):
    config.addinivalue_line("markers", "subgen(handler=...): override subgen mock response handler")
    config.addinivalue_line(
        "markers",
        "docker_stub(container_running=..., container_unavailable=..., log_lines=...): override docker stub",
    )
