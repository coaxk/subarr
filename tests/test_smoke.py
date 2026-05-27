"""Phase-1 smoke tests. Run with: pytest -q"""
from __future__ import annotations

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
        "services:\n"
        "  subgen:\n"
        "    environment:\n"
        "      SUBGEN_KWARGS: '{\"patience\": 1.5, \"length_penalty\": 0.85, \"beam_size\": 5}'\n"
        "      SUBGEN_KWARGS_LANG_JA: '{\"patience\": 1.0, \"length_penalty\": 1.3}'\n"
        "      SUBGEN_KWARGS_LANG_DE: '{\"patience\": 1.8, \"length_penalty\": 0.8}'\n"
        "      UNRELATED_VAR: hello\n"
    )

    monkeypatch.setenv("SUBARR_MEDIA_ROOT", str(media))
    monkeypatch.setenv("SUBGEN_COMPOSE_PATH", str(compose))
    monkeypatch.setenv("SUBARR_DB_PATH", str(tmp_path / "db.sqlite"))

    import importlib

    from subarr import config, paths
    from subarr.routers import browse, mode

    importlib.reload(config)
    importlib.reload(paths)
    importlib.reload(browse)
    importlib.reload(mode)

    from subarr import app as app_mod

    importlib.reload(app_mod)
    yield


def _client():
    from fastapi.testclient import TestClient
    from subarr.app import app

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
    # Browsing into the show folder returns the .mkv as a file entry now
    # (the .srt is not a leaf — only video files surface — but the file's
    # has_sibling_srt flag picks up the .srt sibling).
    r = _client().get("/api/browse", params={"path": "TV/Show"})
    assert r.status_code == 200
    entries = r.json()["entries"]
    files = [e for e in entries if not e["is_dir"]]
    assert len(files) == 1
    assert files[0]["name"] == "episode.mkv"
    assert files[0]["has_sibling_srt"] is True

    r = _client().get("/api/browse", params={"path": "TV"})
    show_entry = next(e for e in r.json()["entries"] if e["name"] == "Show")
    assert show_entry["video_count"] == 1
    assert show_entry["srt_count"] == 1


def test_browse_rejects_traversal():
    r = _client().get("/api/browse", params={"path": "../etc"})
    assert r.status_code == 400


def test_mode_returns_top_level_kwargs():
    r = _client().get("/api/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["top_level_kwargs"]["patience"] == 1.5
    assert body["top_level_kwargs"]["length_penalty"] == 0.85
    assert body["top_level_kwargs"]["beam_size"] == 5
    assert body["top_level_parse_error"] is None


def test_mode_returns_per_language_kwargs():
    r = _client().get("/api/mode")
    body = r.json()
    codes = [lk["code"] for lk in body["per_language_kwargs"]]
    assert codes == ["DE", "JA"]  # sorted alphabetically
    ja = next(lk for lk in body["per_language_kwargs"] if lk["code"] == "JA")
    assert ja["parsed"]["patience"] == 1.0
    assert ja["parsed"]["length_penalty"] == 1.3
    assert ja["parse_error"] is None
