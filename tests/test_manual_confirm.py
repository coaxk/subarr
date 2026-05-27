"""Tests for manual_confirm scheduler mode + approve/reject endpoints."""
from __future__ import annotations

import httpx
import pytest


def _bazarr_with_one_episode(req: httpx.Request) -> httpx.Response:
    p = req.url.path
    if p == "/api/system/status":
        return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
    if p == "/api/badges":
        return httpx.Response(200, json={"episodes": 1, "movies": 0, "providers": 1})
    if p == "/api/episodes/wanted":
        return httpx.Response(200, json={"data": [{
            "seriesTitle": "ManConf", "episode_number": "1x1", "episodeTitle": "Pilot",
            "missing_subtitles": [{"name": "English", "code2": "en"}],
            "sonarrSeriesId": 99, "sonarrEpisodeId": 7777, "tags": [],
        }]})
    if p == "/api/movies/wanted":
        return httpx.Response(200, json={"data": []})
    return httpx.Response(404)


def _sonarr_for_manconf(req: httpx.Request) -> httpx.Response:
    p = req.url.path
    if p == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "4.0"})
    if p == "/api/v3/series":
        return httpx.Response(200, json=[{
            "id": 99, "title": "ManConf", "monitored": True,
            "path": "/data/Media/TV/ManConf",
            "originalLanguage": {"id": 11, "name": "Korean"}, "tags": [],
        }])
    if p == "/api/v3/episode/7777":
        return httpx.Response(200, json={
            "id": 7777, "seriesId": 99, "hasFile": True, "episodeFileId": 8888,
        })
    if p == "/api/v3/episodefile/8888":
        return httpx.Response(200, json={
            "id": 8888, "path": "/data/Media/TV/ManConf/Season 1/manconf.S01E01.mkv",
        })
    if p == "/api/v3/tag":
        return httpx.Response(200, json=[])
    return httpx.Response(404)


def _radarr_empty(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "6.0"})
    if req.url.path in {"/api/v3/movie", "/api/v3/tag"}:
        return httpx.Response(200, json=[])
    return httpx.Response(404)


@pytest.fixture
def manconf_setup(app_with_stub):
    """Set mode=manual_confirm + a rule that lets ManConf through, plant
    the resolved file so _enqueue's exists() check passes."""
    from subarr.config import settings
    folder = settings.media_root / "TV" / "ManConf" / "Season 1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manconf.S01E01.mkv").write_bytes(b"x")
    app_with_stub.put("/api/schedule/rules", json={
        "mode": "manual_confirm",
        "min_score": 0,
        "deny_languages": [],
        "require_monitored": False,
    })
    return app_with_stub


@pytest.mark.integrations_stub(
    bazarr_handler=_bazarr_with_one_episode,
    sonarr_handler=_sonarr_for_manconf,
    radarr_handler=_radarr_empty,
)
def test_manual_confirm_stashes_instead_of_enqueueing(manconf_setup):
    r = manconf_setup.post("/api/schedule/coverage_walk/run-now")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "manual_confirm"
    assert body["queued"] == 0
    assert body["pending_count"] == 1
    walk_id = body["pending_walk_id"]
    assert walk_id

    # The walk should appear in /api/schedule/pending.
    r2 = manconf_setup.get("/api/schedule/pending?status=pending")
    assert r2.status_code == 200
    plist = r2.json()
    assert plist["pending_count"] == 1
    assert plist["walks"][0]["id"] == walk_id
    assert len(plist["walks"][0]["decisions"]) == 1


@pytest.mark.integrations_stub(
    bazarr_handler=_bazarr_with_one_episode,
    sonarr_handler=_sonarr_for_manconf,
    radarr_handler=_radarr_empty,
)
def test_approve_all_enqueues_and_finalises(manconf_setup):
    walk_id = manconf_setup.post("/api/schedule/coverage_walk/run-now").json()["pending_walk_id"]
    r = manconf_setup.post(f"/api/schedule/pending/{walk_id}/approve", json={"decision_ids": None})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["queued"] == 1
    assert body["final_status"] == "approved_all"

    # No more pending walks.
    r2 = manconf_setup.get("/api/schedule/pending?status=pending")
    assert r2.json()["pending_count"] == 0


@pytest.mark.integrations_stub(
    bazarr_handler=_bazarr_with_one_episode,
    sonarr_handler=_sonarr_for_manconf,
    radarr_handler=_radarr_empty,
)
def test_reject_all_finalises_without_enqueue(manconf_setup):
    walk_id = manconf_setup.post("/api/schedule/coverage_walk/run-now").json()["pending_walk_id"]
    r = manconf_setup.post(f"/api/schedule/pending/{walk_id}/reject", json={"decision_ids": None})
    assert r.status_code == 200
    body = r.json()
    assert body["rejected"] == 1
    assert body["final_status"] == "rejected"


@pytest.mark.integrations_stub(
    bazarr_handler=_bazarr_with_one_episode,
    sonarr_handler=_sonarr_for_manconf,
    radarr_handler=_radarr_empty,
)
def test_approve_selected_subset(manconf_setup):
    # Same one-row walk; approve only its decision id explicitly.
    walk_id = manconf_setup.post("/api/schedule/coverage_walk/run-now").json()["pending_walk_id"]
    pending = manconf_setup.get(f"/api/schedule/pending").json()
    decision_id = pending["walks"][0]["decisions"][0]["id"]

    r = manconf_setup.post(
        f"/api/schedule/pending/{walk_id}/approve",
        json={"decision_ids": [decision_id]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["queued"] == 1
    assert body["final_status"] == "approved_all"  # 1 of 1
