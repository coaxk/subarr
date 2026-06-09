"""Tests for /api/integrations/health and /api/coverage.

All upstreams stubbed via @pytest.mark.integrations_stub(...). Filesystem
reconcile uses the same media_root fixture as other tests — we plant a
.srt next to a video to verify the 'stale Bazarr view' detection.
"""

from __future__ import annotations

import httpx
import pytest


# ───── shared canned handlers ──────────────────────────────────────────────


def _bazarr_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    if path == "/api/system/status":
        return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6", "package_version": "ls349"}})
    if path == "/api/badges":
        return httpx.Response(200, json={"episodes": 2, "movies": 0, "providers": 2, "status": 0})
    if path == "/api/episodes/wanted":
        return httpx.Response(
            200,
            json={
                "total": 2,
                "data": [
                    {
                        "seriesTitle": "Foreign Drama",
                        "episode_number": "1x3",
                        "episodeTitle": "Pilot",
                        "missing_subtitles": [
                            {"name": "English", "code2": "en", "code3": "eng", "forced": False, "hi": False}
                        ],
                        "sonarrSeriesId": 42,
                        "sonarrEpisodeId": 9001,
                        "sceneName": "Foreign.S01E03",
                        "tags": [],
                        "seriesType": "standard",
                    },
                    {
                        "seriesTitle": "Already Subbed",
                        "episode_number": "1x1",
                        "episodeTitle": "Pilot",
                        "missing_subtitles": [
                            {"name": "English", "code2": "en", "code3": "eng", "forced": False, "hi": False}
                        ],
                        "sonarrSeriesId": 43,
                        "sonarrEpisodeId": 9002,
                        "sceneName": "Already.S01E01",
                        "tags": [],
                        "seriesType": "standard",
                    },
                ],
            },
        )
    if path == "/api/movies/wanted":
        return httpx.Response(200, json={"total": 0, "data": []})
    return httpx.Response(404)


def _sonarr_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    if path == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "4.0.17.2967", "appName": "Sonarr"})
    if path == "/api/v3/series":
        return httpx.Response(
            200,
            json=[
                {
                    "id": 42,
                    "title": "Foreign Drama",
                    "tvdbId": 12345,
                    "monitored": True,
                    "path": "/data/Media/TV/Foreign Drama",
                    "originalLanguage": {"id": 11, "name": "Korean"},
                    "tags": [1],
                },
                {
                    "id": 43,
                    "title": "Already Subbed",
                    "tvdbId": 67890,
                    "monitored": True,
                    "path": "/data/Media/TV/Already Subbed",
                    "originalLanguage": {"id": 1, "name": "English"},
                    "tags": [],
                },
            ],
        )
    if path == "/api/v3/tag":
        return httpx.Response(200, json=[{"id": 1, "label": "premium"}])
    return httpx.Response(404)


def _radarr_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    if path == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "6.2.0", "appName": "Radarr"})
    if path == "/api/v3/movie":
        return httpx.Response(200, json=[])
    if path == "/api/v3/tag":
        return httpx.Response(200, json=[])
    return httpx.Response(404)


def _tautulli_handler(req: httpx.Request) -> httpx.Response:
    cmd = req.url.params.get("cmd")
    if cmd == "status":
        return httpx.Response(200, json={"response": {"result": "success", "data": None}})
    if cmd == "get_history":
        # start_date must be YYYY-MM-DD (Tautulli silently 0-rows on epoch).
        sd = req.url.params.get("start_date") or ""
        if sd and not (len(sd) == 10 and sd[4] == "-" and sd[7] == "-"):
            return httpx.Response(
                200,
                json={
                    "response": {
                        "result": "success",
                        "data": {"data": [], "recordsFiltered": 0, "recordsTotal": 0},
                    }
                },
            )
        # Foreign Drama watched yesterday -> +1000
        return httpx.Response(
            200,
            json={
                "response": {
                    "result": "success",
                    "data": {
                        "data": [
                            {
                                "date": __import__("time").time() - 86400,
                                "rating_key": 1,
                                "parent_rating_key": 2,
                                "grandparent_rating_key": 3,
                                "grandparent_title": "Foreign Drama",
                                "media_type": "episode",
                                "stopped": 1,
                                "duration": 100,
                                "user": "u",
                                "watched_status": 1,
                            },
                        ],
                        "recordsFiltered": 1,
                        "recordsTotal": 1,
                    },
                }
            },
        )
    return httpx.Response(200, json={"response": {"result": "error", "message": "stub: unknown cmd"}})


_ALL_STUB = {
    "bazarr_handler": _bazarr_handler,
    "sonarr_handler": _sonarr_handler,
    "radarr_handler": _radarr_handler,
    "tautulli_handler": _tautulli_handler,
}


# ───── integrations/health ─────────────────────────────────────────────────


def _stub_plex_online(app, version="1.40.0.0"):
    """The test bundle stubs Plex as unconfigured; configure it + stub its
    status() (PlexClient uses inline httpx, not the MockTransport the other
    clients get) so it reports online for the health probe."""
    p = app.state.integrations.plex
    p._base_url = "http://plex.test:32400"
    p._token = "test-token"

    async def fake_status():
        return {"version": version, "machine_id": "test-machine"}

    p.status = fake_status


@pytest.mark.integrations_stub(**_ALL_STUB)
def test_integrations_health_all_up(app_with_stub):
    _stub_plex_online(app_with_stub.app)
    r = app_with_stub.get("/api/integrations/health")
    assert r.status_code == 200
    by_name = {it["name"]: it for it in r.json()["integrations"]}
    assert {"bazarr", "sonarr", "radarr", "tautulli", "plex"} <= set(by_name)
    for name in by_name:
        assert by_name[name]["online"] is True, f"{name}: {by_name[name]}"
    assert by_name["bazarr"]["version"] == "1.5.6"
    assert by_name["bazarr"]["badges"]["episodes"] == 2
    assert by_name["sonarr"]["version"] == "4.0.17.2967"
    assert by_name["plex"]["version"] == "1.40.0.0"


@pytest.mark.integrations_stub(**_ALL_STUB)
def test_integrations_health_plex_unconfigured(monkeypatch, app_with_stub):
    # No token → Plex reports configured:false (not an error), like any
    # other unconfigured integration.
    app_with_stub.app.state.integrations.plex._token = ""
    r = app_with_stub.get("/api/integrations/health")
    by_name = {it["name"]: it for it in r.json()["integrations"]}
    assert by_name["plex"]["configured"] is False
    assert by_name["plex"]["online"] is False


@pytest.mark.integrations_stub(bazarr_handler=_bazarr_handler)
def test_integrations_health_some_unconfigured(app_with_stub):
    r = app_with_stub.get("/api/integrations/health")
    by_name = {it["name"]: it for it in r.json()["integrations"]}
    assert by_name["bazarr"]["online"] is True
    assert by_name["sonarr"]["configured"] is False
    assert by_name["radarr"]["configured"] is False
    assert by_name["tautulli"]["configured"] is False


def _bazarr_500(req):
    return httpx.Response(500, text="kaboom")


@pytest.mark.integrations_stub(
    bazarr_handler=_bazarr_500,
    sonarr_handler=_sonarr_handler,
    radarr_handler=_radarr_handler,
    tautulli_handler=_tautulli_handler,
)
def test_integrations_health_isolates_one_failure(app_with_stub):
    r = app_with_stub.get("/api/integrations/health")
    by_name = {it["name"]: it for it in r.json()["integrations"]}
    assert by_name["bazarr"]["online"] is False
    assert "500" in by_name["bazarr"].get("error", "")
    assert by_name["sonarr"]["online"] is True


# ───── /api/coverage ───────────────────────────────────────────────────────


@pytest.fixture
def coverage_media_root(monkeypatch, tmp_path, media_root):
    """Plant disk fixtures matching the bazarr/sonarr stub data so the
    reconciler has something to find."""
    # Foreign Drama → no srt on disk (real gap)
    (media_root / "TV" / "Foreign Drama").mkdir(parents=True)
    (media_root / "TV" / "Foreign Drama" / "S01E03.mkv").write_bytes(b"")
    # Already Subbed → srt exists on disk (stale Bazarr view)
    (media_root / "TV" / "Already Subbed").mkdir(parents=True)
    (media_root / "TV" / "Already Subbed" / "S01E01.mkv").write_bytes(b"")
    (media_root / "TV" / "Already Subbed" / "S01E01.en.srt").write_text("1\n")
    yield media_root


@pytest.mark.integrations_stub(**_ALL_STUB)
def test_coverage_basic(app_with_stub, coverage_media_root):
    r = app_with_stub.get("/api/coverage?fresh=true&hide_stale_disk=false&hide_english_audio=false")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["items"] == 2
    assert body["totals"]["episodes"] == 2
    assert body["totals"]["with_disk_sub"] == 1
    titles = [i["title"] for i in body["items"]]
    assert "Foreign Drama" in titles
    assert "Already Subbed" in titles
    # All 4 sources reported.
    assert {"bazarr", "sonarr", "radarr", "tautulli"} <= set(body["sources"])
    assert body["sources"]["bazarr"]["episodes_wanted"] == 2


@pytest.mark.integrations_stub(**_ALL_STUB)
def test_coverage_scores_prioritise_watched_foreign(app_with_stub, coverage_media_root):
    r = app_with_stub.get("/api/coverage?fresh=true&hide_stale_disk=false&hide_english_audio=false")
    items = r.json()["items"]
    foreign = next(i for i in items if i["title"] == "Foreign Drama")
    stale = next(i for i in items if i["title"] == "Already Subbed")
    # Foreign: +1000 (watched 1d ago) + 100 (non-english) + 50 (monitored) = 1150
    assert foreign["score"] >= 1100
    # Stale: −5000 (disk srt) outweighs everything
    assert stale["score"] < 0
    assert any("stale" in r for r in stale["score_reasons"])
    # First row should be the high-score foreign one.
    assert items[0]["title"] == "Foreign Drama"


@pytest.mark.integrations_stub(**_ALL_STUB)
def test_coverage_disk_reconcile(app_with_stub, coverage_media_root):
    r = app_with_stub.get("/api/coverage?fresh=true&hide_stale_disk=false&hide_english_audio=false")
    items = {i["title"]: i for i in r.json()["items"]}
    assert items["Already Subbed"]["has_sub_on_disk"] is True
    assert "S01E01.en.srt" in items["Already Subbed"]["sub_files_seen"]
    assert items["Foreign Drama"]["has_sub_on_disk"] is False


@pytest.mark.integrations_stub(**_ALL_STUB)
def test_coverage_cache(app_with_stub, coverage_media_root):
    r1 = app_with_stub.get("/api/coverage?fresh=true").json()
    r2 = app_with_stub.get("/api/coverage").json()
    # Both responses include the `cached` flag (router always packs it via
    # _apply_filters_and_pack → setdefault("cached", True)). The behavior
    # under test is that the cache snapshot is reused — r1 populates it via
    # the synchronous rebuild path, r2 reads it back.
    assert "cached" in r1
    assert r2["cached"] is True
    # Same totals + same generated_at — r2 is coming from the snapshot r1 stored.
    assert r1["totals"] == r2["totals"]
    assert r1["generated_at"] == r2["generated_at"]


@pytest.mark.integrations_stub(
    sonarr_handler=_sonarr_handler, radarr_handler=_radarr_handler, tautulli_handler=_tautulli_handler
)  # bazarr unconfigured
def test_coverage_handles_missing_bazarr(app_with_stub):
    r = app_with_stub.get("/api/coverage?fresh=true&hide_stale_disk=false")
    assert r.status_code == 200
    body = r.json()
    assert body["sources"]["bazarr"]["configured"] is False
    assert body["totals"]["items"] == 0
