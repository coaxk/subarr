"""Tests for /api/probe + /api/probe/walk + /api/bazarr/sync-disk."""

from __future__ import annotations


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
    cached_result.subtitles.append(
        SubtitleStream(
            index=0,
            language="eng",
            codec="subrip",
            title="English",
            default=True,
            forced=False,
            sdh=False,
            commentary=False,
        )
    )
    store.upsert(
        canonical_path="Movies/TestMovie/TestMovie.mkv",
        mtime=st.st_mtime,
        size=st.st_size,
        result=cached_result,
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
        mtime=f.stat().st_mtime,
        size=9999,  # mismatched
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
        return httpx.Response(
            200,
            json={
                "data": [
                    {"name": "series_full_scan_subtitles", "job_id": "series_full_scan_subtitles"},
                    {"name": "movies_full_scan_subtitles", "job_id": "movies_full_scan_subtitles"},
                ]
            },
        )
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
        return httpx.Response(
            200,
            json={
                "data": [
                    {"name": "Cleanup logs", "job_id": "cleanup_logs"},
                ]
            },
        )
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
    pr.subtitles.append(
        SubtitleStream(
            index=0,
            language="eng",
            codec="subrip",
            title="English",
            default=True,
            forced=False,
            sdh=False,
            commentary=False,
        )
    )
    store.upsert(
        canonical_path="TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv",
        mtime=12345.0,
        size=100,
        result=pr,
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
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "seriesTitle": "Foreign Drama",
                            "episode_number": "1x3",
                            "episodeTitle": "Pilot",
                            "missing_subtitles": [{"name": "English", "code2": "en"}],
                            "sonarrSeriesId": 42,
                            "sonarrEpisodeId": 9001,
                            "tags": [],
                        }
                    ]
                },
            )
        if p == "/api/movies/wanted":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    def _sonarr(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v3/system/status":
            return httpx.Response(200, json={"version": "4.0"})
        if req.url.path == "/api/v3/series":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 42,
                        "title": "Foreign Drama",
                        "monitored": True,
                        "path": "/data/Media/TV/Foreign Drama",
                        "originalLanguage": {"id": 11, "name": "Korean"},
                        "tags": [],
                    }
                ],
            )
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
            transport=_httpx.MockTransport(handler),
            headers=headers,
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


# ── #506: POST /api/probe/reprobe ────────────────────────────────────────────
# Force a re-probe of specific files, bypassing the (path, mtime, size) cache.
# The cache self-invalidates when mtime or size moves, which covers most edits,
# but a language-tag correction can leave both untouched ('und' -> 'eng' is the
# same byte length), so Review keeps showing the old language with no way to
# refresh it. Reported by AztecGuyGDL after correcting files with Tdarr.


def _plant(name="ReprobeMe.mkv"):
    from subarr.config import settings

    folder = settings.media_root / "Movies" / "Reprobe"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / name
    f.write_bytes(b"x" * 64)
    return "Movies/Reprobe/" + name


def test_reprobe_requires_at_least_one_path(app_with_stub):
    r = app_with_stub.post("/api/probe/reprobe", json={"canonical_paths": []})
    assert r.status_code == 400


def test_reprobe_rejects_a_path_outside_the_media_root(app_with_stub):
    r = app_with_stub.post("/api/probe/reprobe", json={"canonical_paths": ["../../etc/passwd"]})
    assert r.status_code == 400
    assert "root" in r.json()["detail"].lower()


def test_reprobe_caps_the_batch_size(app_with_stub):
    """An unbounded list would let one click queue the whole library."""
    r = app_with_stub.post(
        "/api/probe/reprobe",
        json={"canonical_paths": [f"Movies/Reprobe/f{i}.mkv" for i in range(5000)]},
    )
    assert r.status_code == 400
    assert "too many" in r.json()["detail"].lower()


def test_reprobe_starts_a_forced_walk_and_returns_its_id(app_with_stub, monkeypatch):
    canonical = _plant()
    seen = {}

    async def _fake_probe_paths(paths, force=False):
        seen["paths"] = list(paths)
        seen["force"] = force

        class _S:
            def to_dict(self):
                return {"id": "abc123", "status": "running"}

        return _S()

    monkeypatch.setattr(app_with_stub.app.state.probe_walker, "probe_paths", _fake_probe_paths)
    r = app_with_stub.post("/api/probe/reprobe", json={"canonical_paths": [canonical]})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "abc123"
    assert seen["paths"] == [canonical]
    assert seen["force"] is True, "the whole point is that it bypasses the cache"


# ── #506 follow-up: a forced re-probe must refresh the coverage classification ─
# The endpoint shipped in #509 updated the probe cache and stopped there. The
# rows the user is staring at are rendered from the COVERAGE snapshot, which is
# built from the probe cache, so without a rebuild Review keeps showing
# `audio: und` after a successful re-probe - the exact symptom force re-probe
# exists to clear. Step 4 of the issue ("refresh the Review/Coverage
# classification") was simply missing.
#
# Two tests, deliberately: one on the worker's ORDER, one on the WIRING. #505
# was a flag that was computed, serialised, rendered and asserted a dozen times
# while nothing consumed it, so asserting the helper alone is not enough.


@pytest.mark.asyncio
async def test_the_refresh_worker_awaits_the_probe_before_rebuilding():
    """Ordering is the whole point: the rebuild READS the probe cache, so
    refreshing before the walk lands just re-reads the stale entry it was
    supposed to replace."""
    from subarr.routers.probe import _refresh_coverage_after_walk

    order = []

    async def _walk():
        order.append("probe")

    task = None

    class _Walker:
        def __init__(self):
            self._tasks = {}

    class _Cov:
        def request_refresh(self, *a):
            order.append("refresh")

    class _State:
        probe_walker = _Walker()
        coverage_cache = _Cov()
        integrations = object()
        probe_store = object()
        audio_lang = object()

    class _App:
        state = _State()

    class _Req:
        app = _App()

    import asyncio as _asyncio

    task = _asyncio.ensure_future(_walk())
    _State.probe_walker._tasks["w1"] = task

    await _refresh_coverage_after_walk(_Req(), "w1")
    assert order == ["probe", "refresh"], f"got {order}"


@pytest.mark.asyncio
async def test_the_refresh_worker_never_escalates_a_failure():
    """A rebuild is a courtesy on top of a probe that already happened. If the
    walk raised, this must not surface as an unhandled task exception."""
    from subarr.routers.probe import _refresh_coverage_after_walk

    async def _boom():
        raise RuntimeError("ffprobe exploded")

    class _Walker:
        def __init__(self):
            self._tasks = {}

    class _State:
        probe_walker = _Walker()
        coverage_cache = None
        integrations = object()
        probe_store = object()
        audio_lang = object()

    class _App:
        state = _State()

    class _Req:
        app = _App()

    import asyncio as _asyncio

    _State.probe_walker._tasks["w1"] = _asyncio.ensure_future(_boom())
    await _refresh_coverage_after_walk(_Req(), "w1")  # must not raise


def test_reprobe_endpoint_actually_fires_the_refresh(app_with_stub, monkeypatch):
    """The consumption test. Records at CALL time with a sync spy returning a
    throwaway coroutine, because the real call is create_task'd and its body
    would not run until the loop yields - well after the response returns."""
    from subarr.routers import probe as mod

    canonical = _plant()
    calls = {}

    def _spy(request, walk_id):
        calls["walk_id"] = walk_id

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(mod, "_refresh_coverage_after_walk", _spy)

    async def _fake_probe_paths(paths, force=False):
        class _S:
            def to_dict(self):
                return {"id": "walk-xyz", "status": "running"}

        return _S()

    monkeypatch.setattr(app_with_stub.app.state.probe_walker, "probe_paths", _fake_probe_paths)
    r = app_with_stub.post("/api/probe/reprobe", json={"canonical_paths": [canonical]})
    assert r.status_code == 200, r.text
    assert calls.get("walk_id") == "walk-xyz", "the endpoint never scheduled the coverage refresh"


def test_ui_reprobe_cap_matches_the_server_cap():
    """Cross-boundary contract lock (#506).

    The selection cap the UI enforces lives in review.jsx and the 400 that
    rejects an oversized batch lives here. Nothing connected them, which is the
    same gap that let the page-size unit drift in #514. If the UI cap were the
    larger of the two, a user could select a batch the server refuses and get an
    error instead of the feature.
    """
    import re
    from pathlib import Path

    import subarr
    from subarr.routers.probe import MAX_REPROBE_PATHS

    jsx = Path(subarr.__file__).parent / "static" / "v1" / "home-hifi" / "review.jsx"
    m = re.search(r"export const MAX_REPROBE_PATHS\s*=\s*(\d+)", jsx.read_text(encoding="utf-8"))
    assert m, "MAX_REPROBE_PATHS not found in review.jsx"
    ui_cap = int(m.group(1))
    assert ui_cap == MAX_REPROBE_PATHS, (
        f"review.jsx caps a re-probe selection at {ui_cap} but the server rejects above {MAX_REPROBE_PATHS}"
    )
