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

    from subarr import app as app_mod
    from subarr import config, paths, scan_runner, scan_store, subgen_client
    from subarr.routers import browse, mode, queue, scan

    for m in [
        config, paths, scan_store, subgen_client, scan_runner,
        browse, mode, queue, scan, app_mod,
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

    # TestClient triggers lifespan; intercept the subgen client creation.
    import subarr.app as app_mod
    app_mod.SubgenClient = _StubClient  # type: ignore[attr-defined]

    with TestClient(app) as c:
        yield c


def pytest_configure(config):
    config.addinivalue_line("markers", "subgen(handler=...): override subgen mock response handler")
