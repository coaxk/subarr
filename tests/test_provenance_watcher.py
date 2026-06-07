"""Tests for the v1.1 closeout:
- Provenance ledger writes on /api/coverage/queue.
- /api/provenance/{path} joins ledger + Bazarr history + filename parse.
- Completion watcher marks completed_at when path leaves subgen queue +
  triggers Bazarr scan-disk task.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest


# Reuse the canned sonarr handler from test_progress_and_coverage_queue.
def _sonarr_resolver_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    if path == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "4.0.17.2967"})
    if path == "/api/v3/episode/9001":
        return httpx.Response(200, json={
            "id": 9001, "seriesId": 42, "seasonNumber": 1,
            "episodeNumber": 3, "title": "Pilot", "hasFile": True,
            "episodeFileId": 4242,
        })
    if path == "/api/v3/episodefile/4242":
        return httpx.Response(200, json={
            "id": 4242, "seriesId": 42,
            "path": "/data/Media/TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv",
        })
    return httpx.Response(404)


@pytest.fixture
def planted_episode_file():
    from subarr.config import settings
    folder = settings.media_root / "TV" / "Foreign Drama" / "Season 1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Foreign.Drama.S01E03.mkv").write_bytes(b"")
    yield


# ───── coverage/queue writes ledger ──────────────────────────────────────


@pytest.mark.integrations_stub(sonarr_handler=_sonarr_resolver_handler)
def test_coverage_queue_routes_to_pending_with_provenance_identity(app_with_stub, planted_episode_file):
    # #66/#116 s6: coverage/queue now routes through the pending queue rather
    # than writing provenance directly. The provenance identity (series_id +
    # sonarr_episode_id) it resolves must ride on the pending job so the feeder
    # can write a full ledger row when it drains the job to subgen.
    r = app_with_stub.post("/api/coverage/queue", json={"sonarr_episode_id": 9001})
    assert r.status_code == 202
    body = r.json()
    assert body["series_id"] == 42
    assert body["queued_to"] == "pending"

    jobs = [j for j in app_with_stub.app.state.pending_queue.list()
            if j.canonical_path == "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == "pending"
    assert job.source == "gaps"
    assert job.series_id == 42
    assert job.sonarr_episode_id == 9001

    # Provenance is NOT written yet — that's the feeder's job at drain time.
    rows = app_with_stub.app.state.provenance.query_by_path(
        "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv")
    assert rows == []


# ───── /api/provenance/{path} ────────────────────────────────────────────


def _bazarr_history_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/system/status":
        return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
    if req.url.path == "/api/badges":
        return httpx.Response(200, json={"episodes": 0, "movies": 0, "providers": 1})
    if req.url.path == "/api/episodes/history":
        ep_id = req.url.params.get("sonarrEpisodeId")
        if ep_id == "9001":
            return httpx.Response(200, json={"data": [
                {"sonarrEpisodeId": 9001, "provider": "subgen", "score": 320,
                 "language": "en", "timestamp": "2026-05-27T11:00:00Z"},
            ]})
        return httpx.Response(200, json={"data": []})
    return httpx.Response(404)


@pytest.mark.integrations_stub(sonarr_handler=_sonarr_resolver_handler,
                                bazarr_handler=_bazarr_history_handler)
def test_provenance_endpoint_joins_ledger_bazarr_and_suffix(app_with_stub, planted_episode_file):
    # Seed the ledger via the coverage/queue path.
    app_with_stub.post("/api/coverage/queue", json={"sonarr_episode_id": 9001})

    r = app_with_stub.get("/api/provenance/TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.en.subgen.srt")
    assert r.status_code == 200
    body = r.json()
    # filename suffix parsed.
    assert body["filename_suffix"]["lang"] == "en"
    assert body["filename_suffix"]["provider"] == "subgen"


@pytest.mark.integrations_stub()
def test_provenance_recent_empty(app_with_stub):
    r = app_with_stub.get("/api/provenance/recent")
    assert r.status_code == 200
    assert r.json()["entries"] == []


# ───── completion watcher ───────────────────────────────────────────────


def _subgen_empty_queue(req: httpx.Request) -> httpx.Response:
    """Subgen reports the path is no longer in queue+processing."""
    if req.url.path == "/queue":
        return httpx.Response(200, json={
            "queued": [], "processing": [],
            "queued_count": 0, "processing_count": 0,
            "idle": True, "version": "2026.05.3",
        })
    return httpx.Response(404)


@pytest.mark.subgen(handler=_subgen_empty_queue)
@pytest.mark.integrations_stub(sonarr_handler=_sonarr_resolver_handler,
                                bazarr_handler=_bazarr_history_handler)
def test_watcher_marks_completed_and_triggers_bazarr(app_with_stub, planted_episode_file):
    # Seed the ledger directly (the feeder writes provenance at drain time now;
    # this test exercises the WATCHER, so plant the in-flight row it polls).
    app_with_stub.app.state.provenance.record(
        canonical_path="TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv",
        scan_id="test-scan", source="subgenscan",
        series_id=42, sonarr_episode_id=9001,
    )

    # Drive a single watcher tick synchronously.
    watcher = app_with_stub.app.state.watcher
    asyncio.run(watcher._tick())

    rows = app_with_stub.app.state.provenance.query_by_path(
        "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"
    )
    assert len(rows) == 1
    assert rows[0].completed_at is not None, "watcher should have marked completion"


def _subgen_still_processing(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/queue":
        return httpx.Response(200, json={
            "queued": [],
            "processing": [{
                "path": "/media/TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv",
                "type": "transcribe",
            }],
            "queued_count": 0, "processing_count": 1,
            "idle": False, "version": "2026.05.3",
        })
    return httpx.Response(404)


@pytest.mark.subgen(handler=_subgen_still_processing)
@pytest.mark.integrations_stub(sonarr_handler=_sonarr_resolver_handler)
def test_watcher_leaves_in_flight_alone(app_with_stub, planted_episode_file):
    app_with_stub.app.state.provenance.record(
        canonical_path="TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv",
        scan_id="test-scan", source="subgenscan",
        series_id=42, sonarr_episode_id=9001,
    )

    watcher = app_with_stub.app.state.watcher
    asyncio.run(watcher._tick())

    rows = app_with_stub.app.state.provenance.query_by_path(
        "TV/Foreign Drama/Season 1/Foreign.Drama.S01E03.mkv"
    )
    assert rows[0].completed_at is None, "in-flight path must stay pending"
