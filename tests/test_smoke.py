"""Phase-1 smoke tests. Run with: pytest -q"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path: Path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "TV").mkdir()
    (media / "TV" / "Show").mkdir()
    (media / "TV" / "Show" / "episode.mkv").write_bytes(b"")
    (media / "TV" / "Show" / "episode.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n")

    compose = tmp_path / "compose.yaml"
    compose.write_text(
        'services:\n  subgen:\n    environment:\n'
        '      SUBGEN_KWARGS: \'{"patience": 1.5, "length_penalty": 0.85}\'\n'
    )

    monkeypatch.setenv("SUBGENSCAN_MEDIA_ROOT", str(media))
    monkeypatch.setenv("SUBGEN_COMPOSE_PATH", str(compose))
    monkeypatch.setenv("SUBGENSCAN_DB_PATH", str(tmp_path / "db.sqlite"))

    # Force config reload — module-level settings cached load() output.
    import importlib

    from subgenscan_gui import config, paths
    from subgenscan_gui.routers import browse, mode

    importlib.reload(config)
    importlib.reload(paths)
    importlib.reload(browse)
    importlib.reload(mode)

    from subgenscan_gui import app as app_mod

    importlib.reload(app_mod)
    yield


def _client():
    from fastapi.testclient import TestClient
    from subgenscan_gui.app import app

    return TestClient(app)


def test_health():
    r = _client().get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_browse_root_lists_tv():
    r = _client().get("/api/browse")
    assert r.status_code == 200
    data = r.json()
    names = [e["name"] for e in data["entries"]]
    assert "TV" in names


def test_browse_counts_video_and_srt():
    r = _client().get("/api/browse", params={"path": "TV/Show"})
    assert r.status_code == 200
    entries = r.json()["entries"]
    # Show contains only files, no subdirs — entries should be empty (we only list dirs).
    assert entries == []

    r = _client().get("/api/browse", params={"path": "TV"})
    show_entry = next(e for e in r.json()["entries"] if e["name"] == "Show")
    assert show_entry["video_count"] == 1
    assert show_entry["srt_count"] == 1


def test_browse_rejects_traversal():
    r = _client().get("/api/browse", params={"path": "../etc"})
    assert r.status_code == 400


def test_mode_european():
    r = _client().get("/api/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "european"
    assert body["patience"] == 1.5
    assert body["length_penalty"] == 0.85
