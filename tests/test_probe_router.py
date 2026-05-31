"""Tests for /api/probe + /api/probe/walk + /api/bazarr/sync-disk."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest


def test_probe_missing_path_400(app_with_stub):
    r = app_with_stub.get("/api/probe")
    assert r.status_code == 400


def test_probe_unknown_path_404(app_with_stub):
    r = app_with_stub.get("/api/probe", params={"path": "TV/NotARealShow/x.mkv"})
    assert r.status_code == 404


def test_probe_uses_cache(app_with_stub, monkeypatch):
    """If we pre-populate the ProbeStore with a matching mtime+size, the
    endpoint returns the cached entry without calling ffprobe."""
    from subarr.config import settings
    from subarr.media_probe import ProbeResult, SubtitleStream

    # Plant a file
    folder = settings.media_root / "Movies" / "TestMovie"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "TestMovie.mkv"
    f.write_bytes(b"x" * 100)
    st = f.stat()

    # Seed cache
    store = app_with_stub.app.state.probe_store
    cached_result = ProbeResult(canonical_path="Movies/TestMovie/TestMovie.mkv")
    cached_result.duration_s = 1800.0
    cached_result.subtitles.append(SubtitleStream(
        index=0, language="eng", codec="subrip", title="English",
        default=True, forced=False, sdh=False, commentary=False,
    ))
    store.upsert(
        canonical_path="Movies/TestMovie/TestMovie.mkv",
        mtime=st.st_mtime, size=st.st_size, result=cached_result,
    )

    # Block any actual ffprobe call so the test is hermetic.
    async def _no_probe(*a, **kw):
        raise AssertionError("ffprobe should not be invoked when cache is fresh")

    import subarr.routers.probe as probe_router
    monkeypatch.setattr(probe_router, "run_probe", _no_probe)

    r = app_with_stub.get("/api/probe", params={"path": "Movies/TestMovie/TestMovie.mkv"})
    assert r.status_code == 200
    body = r.json()
    assert body["duration_s"] == 1800.0
    assert body["cached"] is True
    assert body["subtitles"][0]["language"] == "eng"


def test_probe_cache_invalidates_on_size_change(app_with_stub, monkeypatch):
    from subarr.config import settings
    from subarr.media_probe import ProbeResult

    folder = settings.media_root / "Movies" / "Changing"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / "Changing.mkv"
    f.write_bytes(b"x" * 100)

    store = app_with_stub.app.state.probe_store
    # Seed with a deliberately wrong size — cache should miss on next lookup.
    seeded = ProbeResult(canonical_path="Movies/Changing/Changing.mkv")
    store.upsert(
        canonical_path="Movies/Changing/Changing.mkv",
        mtime=f.stat().st_mtime, size=9999,  # mismatched
        result=seeded,
    )

    called = {}

    async def _fake_probe(p, **kw):
        called["yes"] = True
        from subarr.media_probe import ProbeResult as PR
        return PR(canonical_path="")  # router fills canonical_path

    import subarr.routers.probe as probe_router
    monkeypatch.setattr(probe_router, "run_probe", _fake_probe)

    r = app_with_stub.get("/api/probe", params={"path": "Movies/Changing/Changing.mkv"})
    assert r.status_code == 200
    assert called.get("yes") is True


def test_walk_happy_path(app_with_stub, monkeypatch):
    """Plant 2 video files + intercept ffprobe; walk should report 2 processed."""
    from subarr.config import settings
    from subarr.media_probe import ProbeResult

    folder = settings.media_root / "TV" / "WalkMe"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "S01E01.mkv").write_bytes(b"a")
    (folder / "S01E02.mkv").write_bytes(b"b")

    async def _fake_probe(p, **kw):
        return ProbeResult(canonical_path="")

    import subarr.probe_walker as probe_walker_mod
    monkeypatch.setattr(probe_walker_mod, "probe", _fake_probe)

    r = app_with_stub.post("/api/probe/walk", json={"path": "TV/WalkMe"})
    assert r.status_code == 200
    walk_id = r.json()["id"]

    # Poll until done (asyncio task runs concurrently)
    import time
    deadline = time.time() + 3.0
    while time.time() < deadline:
        r2 = app_with_stub.get(f"/api/probe/walk/{walk_id}")
        body = r2.json()
        if body["status"] in {"done", "error"}:
            break
        time.sleep(0.05)

    assert body["status"] == "done"
    assert body["total_files"] == 2
    assert body["processed"] == 2
    assert body["probed"] == 2


def _bazarr_with_scan_task(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/system/status":
        return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
    if req.url.path == "/api/badges":
        return httpx.Response(200, json={"episodes": 0, "movies": 0, "providers": 1})
    if req.url.path == "/api/system/tasks" and req.method == "GET":
        # bazarr_sync._TASK_HINTS now matches canonical Bazarr 1.5.x job_ids
        # (series_full_scan_subtitles / movies_full_scan_subtitles) first.
        # Stub returns those so _find_task_id resolves.
        # Stub uses job_id as `name` too — bazarr_sync._find_task_id
        # checks `name OR job_id` per row, and hint strings use underscores
        # to match the canonical 1.5.x job_ids.
        return httpx.Response(200, json={"data": [
            {"name": "series_full_scan_subtitles", "job_id": "series_full_scan_subtitles"},
            {"name": "movies_full_scan_subtitles", "job_id": "movies_full_scan_subtitles"},
        ]})
    if req.url.path == "/api/system/tasks" and req.method == "POST":
        return httpx.Response(200, json={"data": "triggered"})
    return httpx.Response(404)


@pytest.mark.integrations_stub(bazarr_handler=_bazarr_with_scan_task)
def test_bazarr_sync_disk_happy(app_with_stub):
    r = app_with_stub.post("/api/bazarr/sync-disk", json={"series_id": 42})
    assert r.status_code == 200
    body = r.json()
    assert body["triggered"] is True
    # Hint order in bazarr_sync: series_full_scan_subtitles is first.
    assert body["task_id"] in {"series_full_scan_subtitles", "movies_full_scan_subtitles"}


def _bazarr_no_matching_task(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/system/status":
        return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
    if req.url.path == "/api/badges":
        return httpx.Response(200, json={"episodes": 0, "movies": 0, "providers": 1})
    if req.url.path == "/api/system/tasks":
        return httpx.Response(200, json={"data": [
            {"name": "Cleanup logs", "job_id": "cleanup_logs"},
        ]})
    return httpx.Response(404)


@pytest.mark.integrations_stub(bazarr_handler=_bazarr_no_matching_task)
def test_bazarr_sync_disk_502_when_no_matching_task(app_with_stub):
    r = app_with_stub.post("/api/bazarr/sync-disk", json={"series_id": 42})
    assert r.status_code == 502
    assert "no Bazarr task matched" in r.json()["detail"]


def test_bazarr_sync_disk_503_when_unconfigured(app_with_stub):
    # Default app_with_stub leaves bazarr unconfigured if no handler supplied.
    r = app_with_stub.post("/api/bazarr/sync-disk", json={"series_id": 42})
    assert r.status_code == 503


# ───── coverage probe integration ────────────────────────────────────────


def test_coverage_attaches_embedded_en_from_probe_cache(app_with_stub, monkeypatch):
    """Plant a probe-cache entry under a series prefix; build_coverage
    should attach the embedded_en label to the matching Bazarr-wanted row
    + apply the score delta."""
    from subarr.config import settings
    from subarr.media_probe import ProbeResult, SubtitleStream

    # Plant the file on disk so canonical_to_fs resolves cleanly.
    folder = settings.media_root / "TV" / "Foreign Drama" / "Season 1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Foreign.Drama.S01E03.mkv").write_bytes(b"x" * 100)

    # Seed the cache with a full-EN sub for that file.
    store = app_with_stub.app.state.probe_store
    pr = ProbeResult(canonical_path="TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv")
    pr.subtitles.append(SubtitleStream(
        index=0, language="eng", codec="subrip", title="English",
        default=True, forced=False, sdh=False, commentary=False,
    ))
    store.upsert(
        canonical_path="TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv",
        mtime=12345.0, size=100, result=pr,
    )

    # Stub bazarr/sonarr/radarr so build_coverage emits one episode row
    # matching the planted file.
    def _bazarr(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/api/system/status":
            return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
        if p == "/api/badges":
            return httpx.Response(200, json={"episodes": 1, "movies": 0, "providers": 1})
        if p == "/api/episodes/wanted":
            return httpx.Response(200, json={"data": [{
                "seriesTitle": "Foreign Drama", "episode_number": "1x3",
                "episodeTitle": "Pilot",
                "missing_subtitles": [{"name": "English", "code2": "en"}],
                "sonarrSeriesId": 42, "sonarrEpisodeId": 9001, "tags": [],
            }]})
        if p == "/api/movies/wanted":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    def _sonarr(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/system/status":
            return httpx.Response(200, json={"version": "4.0"})
        if req.url.path == "/api/v3/series":
            return httpx.Response(200, json=[{
                "id": 42, "title": "Foreign Drama", "monitored": True,
                "path": "/data/Media/TV/Foreign Drama",
                "originalLanguage": {"id": 11, "name": "Korean"}, "tags": [],
            }])
        if req.url.path == "/api/v3/tag":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    def _radarr(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/system/status":
            return httpx.Response(200, json={"version": "6.0"})
        if req.url.path == "/api/v3/movie":
            return httpx.Response(200, json=[])
        if req.url.path == "/api/v3/tag":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    # Override the bundle in place
    import httpx as _httpx
    bundle = app_with_stub.app.state.integrations

    def _wrap(client, handler, headers):
        client._configured = True
        client._client = _httpx.AsyncClient(
            base_url=client._base_url or "http://x.test",
            transport=_httpx.MockTransport(handler), headers=headers,
        )

    _wrap(bundle.bazarr, _bazarr, {"X-API-KEY": "bz"})
    _wrap(bundle.sonarr, _sonarr, {"X-Api-Key": "sn"})
    _wrap(bundle.radarr, _radarr, {"X-Api-Key": "rd"})

    # hide_embedded_en=false so the embedded-EN row stays visible; the
    # default (true) would filter it out — that path is tested in
    # test_library_probe.test_coverage_hides_embedded_by_default.
    r = app_with_stub.get("/api/coverage?fresh=true&tautulli=false&probe=true&hide_embedded_en=false")
    assert r.status_code == 200
    body = r.json()
    items = body["items"]
    assert len(items) == 1
    item = items[0]
    assert item["embedded_en"] == "EN"
    assert item["suggest_bazarr_rescan"] is True
    assert item["file_canonical_path"] == "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"
    # Score: −3000 from embedded, +100 non-english, +50 monitored = −2850.
    assert item["score"] <= -2000
    assert any("embedded" in reason for reason in item["score_reasons"])
