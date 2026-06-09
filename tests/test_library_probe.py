"""Tests for the Library Probe tab data + Coverage hide_embedded_en filter."""

from __future__ import annotations

import httpx
import pytest


def _seed_probe(app_with_stub, canonical: str, sub_kind: str = "full"):
    """Helper: drop a probe row into the store with the given English sub kind.
    sub_kind in {full, sdh, forced, commentary, none}"""
    from subarr.config import settings
    from subarr.media_probe import ProbeResult, SubtitleStream

    # Plant a placeholder file so canonical_to_fs would succeed if asked.
    p = settings.media_root / canonical
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    st = p.stat()

    pr = ProbeResult(canonical_path=canonical)
    pr.duration_s = 1800.0
    if sub_kind == "full":
        pr.subtitles.append(
            SubtitleStream(
                index=0,
                language="eng",
                codec="subrip",
                title="English",
                default=False,
                forced=False,
                sdh=False,
                commentary=False,
            )
        )
    elif sub_kind == "sdh":
        pr.subtitles.append(
            SubtitleStream(
                index=0,
                language="eng",
                codec="subrip",
                title="English (SDH)",
                default=False,
                forced=False,
                sdh=True,
                commentary=False,
            )
        )
    elif sub_kind == "forced":
        pr.subtitles.append(
            SubtitleStream(
                index=0,
                language="eng",
                codec="subrip",
                title="English Forced",
                default=False,
                forced=True,
                sdh=False,
                commentary=False,
            )
        )
    elif sub_kind == "commentary":
        pr.subtitles.append(
            SubtitleStream(
                index=0,
                language="eng",
                codec="subrip",
                title="Director Commentary",
                default=False,
                forced=False,
                sdh=False,
                commentary=True,
            )
        )
    # 'none' → no sub stream
    app_with_stub.app.state.probe_store.upsert(
        canonical_path=canonical,
        mtime=st.st_mtime,
        size=st.st_size,
        result=pr,
    )


def test_library_endpoint_lists_all_cached(app_with_stub):
    _seed_probe(app_with_stub, "TV/AlphaShow/Season 1/AlphaShow.S01E01.mkv", "full")
    _seed_probe(app_with_stub, "TV/BetaShow/Season 1/BetaShow.S01E01.mkv", "sdh")
    _seed_probe(app_with_stub, "TV/GammaShow/Season 1/GammaShow.S01E01.mkv", "none")

    r = app_with_stub.get("/api/probe/library")
    assert r.status_code == 200
    body = r.json()
    assert body["total_cached"] == 3
    assert body["shown"] == 3
    by_title = {i["canonical_path"].split("/")[1]: i for i in body["items"]}
    assert by_title["AlphaShow"]["english_track"] == "EN"
    assert by_title["AlphaShow"]["usable_english"] is True
    assert by_title["BetaShow"]["english_track"] == "EN(SDH)"
    assert by_title["BetaShow"]["usable_english"] is True
    assert by_title["GammaShow"]["english_track"] is None
    assert by_title["GammaShow"]["usable_english"] is False


def test_library_filter_only_with_eng(app_with_stub):
    _seed_probe(app_with_stub, "TV/Has/Has.mkv", "full")
    _seed_probe(app_with_stub, "TV/Nope/Nope.mkv", "none")
    r = app_with_stub.get("/api/probe/library?only_with_eng=true")
    body = r.json()
    titles = [i["canonical_path"].split("/")[1] for i in body["items"]]
    assert "Has" in titles
    assert "Nope" not in titles


def test_library_filter_by_kind_sdh(app_with_stub):
    _seed_probe(app_with_stub, "TV/Full/Full.mkv", "full")
    _seed_probe(app_with_stub, "TV/Sdh/Sdh.mkv", "sdh")
    _seed_probe(app_with_stub, "TV/Forced/Forced.mkv", "forced")
    r = app_with_stub.get("/api/probe/library?sub_kind=sdh")
    body = r.json()
    titles = [i["canonical_path"].split("/")[1] for i in body["items"]]
    assert titles == ["Sdh"]


def test_library_filter_text_substring(app_with_stub):
    _seed_probe(app_with_stub, "TV/Nicolas Le Floch/Season 6/x.mkv", "sdh")
    _seed_probe(app_with_stub, "TV/Other Show/x.mkv", "none")
    r = app_with_stub.get("/api/probe/library?filter_text=nicolas")
    titles = [i["canonical_path"] for i in r.json()["items"]]
    assert len(titles) == 1
    assert "Nicolas" in titles[0]


# ───── Coverage hide_embedded_en filter ────────────────────────────────


def _bazarr_with_two_episodes(req: httpx.Request) -> httpx.Response:
    p = req.url.path
    if p == "/api/system/status":
        return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
    if p == "/api/badges":
        return httpx.Response(200, json={"episodes": 2, "movies": 0, "providers": 1})
    if p == "/api/episodes/wanted":
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "seriesTitle": "Covered",
                        "episode_number": "1x1",
                        "episodeTitle": "Has EN",
                        "missing_subtitles": [{"name": "English", "code2": "en"}],
                        "sonarrSeriesId": 42,
                        "sonarrEpisodeId": 9001,
                        "tags": [],
                    },
                    {
                        "seriesTitle": "Gap",
                        "episode_number": "1x1",
                        "episodeTitle": "No EN",
                        "missing_subtitles": [{"name": "English", "code2": "en"}],
                        "sonarrSeriesId": 43,
                        "sonarrEpisodeId": 9002,
                        "tags": [],
                    },
                ]
            },
        )
    if p == "/api/movies/wanted":
        return httpx.Response(200, json={"data": []})
    return httpx.Response(404)


def _sonarr_two_series(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "4.0"})
    if req.url.path == "/api/v3/series":
        return httpx.Response(
            200,
            json=[
                {
                    "id": 42,
                    "title": "Covered",
                    "monitored": True,
                    "path": "/data/Media/TV/Covered",
                    "originalLanguage": {"id": 1, "name": "English"},
                    "tags": [],
                },
                {
                    "id": 43,
                    "title": "Gap",
                    "monitored": True,
                    "path": "/data/Media/TV/Gap",
                    "originalLanguage": {"id": 11, "name": "Korean"},
                    "tags": [],
                },
            ],
        )
    if req.url.path == "/api/v3/tag":
        return httpx.Response(200, json=[])
    return httpx.Response(404)


def _radarr_empty(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/v3/system/status":
        return httpx.Response(200, json={"version": "6.0"})
    if req.url.path in {"/api/v3/movie", "/api/v3/tag"}:
        return httpx.Response(200, json=[])
    return httpx.Response(404)


@pytest.mark.integrations_stub(
    bazarr_handler=_bazarr_with_two_episodes,
    sonarr_handler=_sonarr_two_series,
    radarr_handler=_radarr_empty,
)
def test_coverage_hides_embedded_by_default(app_with_stub):
    # Plant the Covered file with a full EN sub.
    _seed_probe(app_with_stub, "TV/Covered/Season 1/Covered.S01E01.mkv", "full")
    # Gap has no probe — stays as a gap.

    r = app_with_stub.get("/api/coverage?fresh=true&tautulli=false")
    assert r.status_code == 200
    body = r.json()
    titles = [i["title"] for i in body["items"]]
    assert "Gap" in titles
    assert "Covered" not in titles
    assert body["totals"]["suppressed_by_embedded_en"] >= 1


@pytest.mark.integrations_stub(
    bazarr_handler=_bazarr_with_two_episodes,
    sonarr_handler=_sonarr_two_series,
    radarr_handler=_radarr_empty,
)
def test_coverage_shows_suppressed_when_flag_off(app_with_stub):
    _seed_probe(app_with_stub, "TV/Covered/Season 1/Covered.S01E01.mkv", "full")

    r = app_with_stub.get(
        "/api/coverage?fresh=true&tautulli=false&hide_embedded_en=false&hide_english_audio=false"
    )
    body = r.json()
    titles = [i["title"] for i in body["items"]]
    assert "Covered" in titles
    assert "Gap" in titles


@pytest.mark.integrations_stub(
    bazarr_handler=_bazarr_with_two_episodes,
    sonarr_handler=_sonarr_two_series,
    radarr_handler=_radarr_empty,
)
def test_coverage_hides_sdh_rows_too(app_with_stub):
    """SDH collapse: SDH-only rows should be suppressed just like full EN."""
    _seed_probe(app_with_stub, "TV/Covered/Season 1/Covered.S01E01.mkv", "sdh")

    r = app_with_stub.get("/api/coverage?fresh=true&tautulli=false")
    titles = [i["title"] for i in r.json()["items"]]
    assert "Covered" not in titles


# ───── Auto-queue skip_embedded_en ─────────────────────────────────────


def test_auto_queue_skip_embedded_en(subarr_env):
    from subarr.auto_queue import evaluate
    from subarr.coverage_engine import CoverageItem
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES

    # verification_state="verified" — these rows passed the probe-gate, so
    # evaluate() reaches the embedded-EN skip logic this test exercises.
    items = [
        CoverageItem(
            media_type="episode",
            title="A",
            episode_number="1x1",
            original_language="Korean",
            monitored=True,
            canonical_path="TV/A",
            embedded_en="EN",
            score=500,
            bazarr_episode_id=1,
            verification_state="verified",
        ),
        CoverageItem(
            media_type="episode",
            title="B",
            episode_number="1x1",
            original_language="Korean",
            monitored=True,
            canonical_path="TV/B",
            embedded_en=None,
            score=500,
            bazarr_episode_id=2,
            verification_state="verified",
        ),
    ]
    rules = AutoQueueRules(
        mode=MODE_AUTO_RULES, min_score=0, deny_languages=[], require_monitored=True, skip_embedded_en=True
    )
    decisions = evaluate(items, rules)
    by_title = {d.item.title: d for d in decisions}
    assert by_title["A"].action == "skip"
    assert "embedded" in by_title["A"].reason.lower()
    assert by_title["B"].action == "queue"


def test_auto_queue_skip_embedded_en_off(subarr_env):
    from subarr.auto_queue import evaluate
    from subarr.coverage_engine import CoverageItem
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES

    items = [
        CoverageItem(
            media_type="episode",
            title="A",
            episode_number="1x1",
            original_language="Korean",
            monitored=True,
            canonical_path="TV/A",
            embedded_en="EN",
            score=500,
            bazarr_episode_id=1,
            verification_state="verified",
        ),
    ]
    rules = AutoQueueRules(
        mode=MODE_AUTO_RULES, min_score=0, deny_languages=[], require_monitored=True, skip_embedded_en=False
    )
    decisions = evaluate(items, rules)
    assert decisions[0].action == "queue"
