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
    # v1.1 integrations — configured by default so is_configured() is True.
    monkeypatch.setenv("BAZARR_URL", "http://bazarr.test:6767")
    monkeypatch.setenv("BAZARR_API_KEY", "bz-test-key")
    monkeypatch.setenv("SONARR_URL", "http://sonarr.test:8989")
    monkeypatch.setenv("SONARR_API_KEY", "sn-test-key")
    monkeypatch.setenv("RADARR_URL", "http://radarr.test:7878")
    monkeypatch.setenv("RADARR_API_KEY", "rd-test-key")
    monkeypatch.setenv("TAUTULLI_URL", "http://tautulli.test:8181")
    monkeypatch.setenv("TAUTULLI_API_KEY", "tt-test-key")
    monkeypatch.setenv("ARR_PATH_PREFIX", "/data/Media/")

    from subarr import app as app_mod
    from subarr import config, coverage_engine, docker_client, paths, scan_runner, scan_store, subgen_client
    from subarr.integrations import bazarr as iz_bazarr
    from subarr.integrations import base as iz_base
    from subarr.integrations import radarr as iz_radarr
    from subarr.integrations import sonarr as iz_sonarr
    from subarr.integrations import tautulli as iz_tautulli
    from subarr.routers import (
        admin, browse, coverage, gpu, integrations as r_integrations,
        logs, mode, queue, scan,
    )

    for m in [
        config, paths, scan_store, subgen_client, scan_runner, docker_client,
        iz_base, iz_bazarr, iz_sonarr, iz_radarr, iz_tautulli, coverage_engine,
        browse, mode, queue, scan, gpu, logs, admin, r_integrations, coverage, app_mod,
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

    integ_marker = request.node.get_closest_marker("integrations_stub")
    integ_kwargs = integ_marker.kwargs if integ_marker else {}
    app_mod.IntegrationBundle = _make_integration_bundle(**integ_kwargs)  # type: ignore[attr-defined]

    with TestClient(app) as c:
        yield c


def _make_integration_bundle(
    bazarr_handler=None,
    sonarr_handler=None,
    radarr_handler=None,
    tautulli_handler=None,
):
    """Build an IntegrationBundle whose four clients use httpx.MockTransport.

    Each *_handler is a callable httpx.Request -> httpx.Response. Pass None
    to mark that integration as unconfigured (it'll return is_configured() False)."""
    from subarr.coverage_engine import IntegrationBundle
    from subarr.integrations.bazarr import BazarrClient
    from subarr.integrations.radarr import RadarrClient
    from subarr.integrations.sonarr import SonarrClient
    from subarr.integrations.tautulli import TautulliClient

    def _wrap(cls, handler, base_url, headers=None):
        c = cls.__new__(cls)
        if handler is None:
            # Mark unconfigured. base/url cleared so is_configured() == False.
            c._base_url = ""
            c._configured = False
            c._client = httpx.AsyncClient(base_url="http://void.test")
            if isinstance(c, TautulliClient):
                c._apikey = ""
            return c
        c._base_url = base_url
        c._configured = True
        c._client = httpx.AsyncClient(
            base_url=base_url,
            transport=httpx.MockTransport(handler),
            headers=headers or {},
        )
        if isinstance(c, TautulliClient):
            c._apikey = "tt-test-key"
        return c

    class _StubBundle(IntegrationBundle):
        def __init__(self):
            self.bazarr = _wrap(BazarrClient, bazarr_handler,
                                "http://bazarr.test:6767",
                                {"X-API-KEY": "bz-test-key"} if bazarr_handler else None)
            self.sonarr = _wrap(SonarrClient, sonarr_handler,
                                "http://sonarr.test:8989",
                                {"X-Api-Key": "sn-test-key"} if sonarr_handler else None)
            self.radarr = _wrap(RadarrClient, radarr_handler,
                                "http://radarr.test:7878",
                                {"X-Api-Key": "rd-test-key"} if radarr_handler else None)
            self.tautulli = _wrap(TautulliClient, tautulli_handler,
                                  "http://tautulli.test:8181")

    return _StubBundle


def _make_docker_stub(
    container_running: bool = True,
    container_unavailable: bool = False,
    log_lines: list[str] | None = None,
    progress_map: dict[str, dict] | None = None,
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

        async def recent_progress(self, tail: int = 80) -> dict[str, dict]:
            # Tests opt-in via @pytest.mark.docker_stub(progress_map=...). Default
            # is empty so the queue endpoint doesn't try to talk to docker.
            return progress_map or {}

    return _StubDocker


def pytest_configure(config):
    config.addinivalue_line("markers", "subgen(handler=...): override subgen mock response handler")
    config.addinivalue_line(
        "markers",
        "docker_stub(container_running=..., container_unavailable=..., log_lines=...): override docker stub",
    )
    config.addinivalue_line(
        "markers",
        "integrations_stub(bazarr_handler=..., sonarr_handler=..., radarr_handler=..., tautulli_handler=...)",
    )
