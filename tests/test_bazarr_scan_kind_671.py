"""#671: Bazarr scan-kind selection and the bazarr_wanted coverage flag."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

LIVE = [
    {"job_id": "update_series", "name": "Sync with Sonarr"},
    {"job_id": "movies_full_scan_subtitles", "name": "Index All Existing Movies Subtitles"},
    {"job_id": "series_full_scan_subtitles", "name": "Index All Existing Episodes Subtitles"},
]
STUB = [
    {"job_id": "series_full_scan_subtitles", "name": "Scan disk"},
    {"job_id": "update_series", "name": "Sync with Sonarr"},
]


# ───── Unit: scan_task_id ────────────────────────────────────────────────


def test_scan_task_id_picks_each_kind_from_the_live_task_list():
    from subarr.integrations.bazarr import scan_task_id

    assert scan_task_id(LIVE, "series") == "series_full_scan_subtitles"
    assert scan_task_id(LIVE, "movies") == "movies_full_scan_subtitles"


def test_scan_task_id_never_takes_a_generic_scan_disk_task_for_movies():
    from subarr.integrations.bazarr import scan_task_id

    assert scan_task_id(STUB, "movies") is None
    assert scan_task_id(STUB, "series") == "series_full_scan_subtitles"


def test_scan_task_id_matches_name_only_tasks():
    from subarr.integrations.bazarr import scan_task_id

    assert scan_task_id([{"name": "Index All Existing Movies Subtitles", "id": "m1"}], "movies") == "m1"
    name = "index all existing episodes subtitles"
    assert scan_task_id([{"name": name}], "series") == name


def test_scan_task_id_handles_empty_and_non_dict_tasks():
    from subarr.integrations.bazarr import scan_task_id

    assert scan_task_id([], "series") is None
    assert scan_task_id([], "movies") is None
    tasks = [None, "x", {"job_id": "series_full_scan_subtitles"}]
    assert scan_task_id(tasks, "series") == "series_full_scan_subtitles"


def test_scan_task_id_rejects_unknown_kind():
    from subarr.integrations.bazarr import scan_task_id

    with pytest.raises(ValueError):
        scan_task_id(LIVE, "episode")


# ───── Route: POST /api/bazarr/sync-disk ─────────────────────────────────

POSTED: list[str] = []


def _handler_for(tasks, *, fail_on: str | None = None):
    def _h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/system/status":
            return httpx.Response(200, json={"data": {"bazarr_version": "1.5.6"}})
        if req.url.path == "/api/badges":
            return httpx.Response(200, json={"episodes": 0, "movies": 0, "providers": 1})
        if req.url.path == "/api/system/tasks" and req.method == "GET":
            return httpx.Response(200, json={"data": tasks})
        if req.url.path == "/api/system/tasks" and req.method == "POST":
            task_id = req.url.params.get("taskid")
            POSTED.append(task_id)
            if task_id == fail_on:
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={"data": "triggered"})
        return httpx.Response(404)

    return _h


@pytest.mark.integrations_stub(bazarr_handler=_handler_for(LIVE))
def test_sync_disk_empty_body_triggers_series_then_movies(app_with_stub):
    POSTED.clear()
    r = app_with_stub.post("/api/bazarr/sync-disk", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_ids"] == ["series_full_scan_subtitles", "movies_full_scan_subtitles"]
    assert body["task_id"] == "series_full_scan_subtitles"
    assert POSTED == ["series_full_scan_subtitles", "movies_full_scan_subtitles"]


@pytest.mark.integrations_stub(bazarr_handler=_handler_for(LIVE))
def test_sync_disk_movie_media_type_triggers_only_the_movies_index(app_with_stub):
    POSTED.clear()
    r = app_with_stub.post("/api/bazarr/sync-disk", json={"media_type": "movie"})
    assert r.status_code == 200, r.text
    assert r.json()["task_ids"] == ["movies_full_scan_subtitles"]
    assert POSTED == ["movies_full_scan_subtitles"]


@pytest.mark.integrations_stub(bazarr_handler=_handler_for(LIVE))
def test_sync_disk_episode_media_type_triggers_only_the_series_index(app_with_stub):
    POSTED.clear()
    r = app_with_stub.post("/api/bazarr/sync-disk", json={"media_type": "episode"})
    assert r.status_code == 200, r.text
    assert r.json()["task_ids"] == ["series_full_scan_subtitles"]
    assert POSTED == ["series_full_scan_subtitles"]


def test_sync_disk_unknown_media_type_is_422_and_posts_nothing(app_with_stub):
    POSTED.clear()
    r = app_with_stub.post("/api/bazarr/sync-disk", json={"media_type": "film"})
    assert r.status_code == 422
    assert POSTED == []


@pytest.mark.integrations_stub(bazarr_handler=_handler_for(STUB))
def test_sync_disk_movie_502_when_only_a_series_task_exists(app_with_stub):
    POSTED.clear()
    r = app_with_stub.post("/api/bazarr/sync-disk", json={"media_type": "movie"})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail.startswith("no Bazarr task matched scan-disk hints")
    assert "movies_full_scan_subtitles" in detail
    assert POSTED == []

    r = app_with_stub.post("/api/bazarr/sync-disk", json={})
    assert r.status_code == 200, r.text
    assert r.json()["task_ids"] == ["series_full_scan_subtitles"]


@pytest.mark.integrations_stub(
    bazarr_handler=_handler_for(
        [
            {
                "job_id": "full_scan",
                "name": "Index All Existing Episodes Subtitles and index all existing movies",
            }
        ]
    )
)
def test_sync_disk_triggers_a_shared_task_id_only_once(app_with_stub):
    POSTED.clear()
    r = app_with_stub.post("/api/bazarr/sync-disk", json={})
    assert r.status_code == 200, r.text
    assert r.json()["task_ids"] == ["full_scan"]
    assert POSTED == ["full_scan"]


@pytest.mark.integrations_stub(
    bazarr_handler=_handler_for(["junk", {"name": "Cleanup logs", "job_id": "cleanup_logs"}])
)
def test_sync_disk_non_dict_task_entries_do_not_500(app_with_stub):
    POSTED.clear()
    r = app_with_stub.post("/api/bazarr/sync-disk", json={})
    assert r.status_code == 502
    assert "Cleanup logs" in r.json()["detail"]


@pytest.mark.integrations_stub(bazarr_handler=_handler_for(LIVE, fail_on="movies_full_scan_subtitles"))
def test_sync_disk_movie_trigger_failure_is_502_after_the_series_post(app_with_stub):
    POSTED.clear()
    r = app_with_stub.post("/api/bazarr/sync-disk", json={})
    assert r.status_code == 502
    assert "trigger_task(movies_full_scan_subtitles)" in r.json()["detail"]
    assert POSTED == ["series_full_scan_subtitles", "movies_full_scan_subtitles"]


# ───── Scheduler: the stale-disk poke ────────────────────────────────────


def _serve_live(ws, *, failing: tuple[str, ...] = ()):
    bz = ws.bundle._clients["bazarr"][""]
    triggers: list[str] = []

    async def list_tasks():
        return LIVE

    async def trigger_task(task_id):
        if task_id in failing:
            raise RuntimeError("boom")
        triggers.append(task_id)

    bz.list_tasks = list_tasks
    bz.trigger_task = trigger_task
    return bz, triggers


def _row(canonical: str, media_type: str | None = None):
    fields = {
        "suggest_bazarr_rescan": True,
        "file_canonical_path": canonical,
        "canonical_path": None,
    }
    if media_type is not None:
        fields["media_type"] = media_type
    return SimpleNamespace(**fields)


def test_scheduler_pokes_the_movies_index_for_a_movie_row(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    _, triggers = _serve_live(ws)
    items = [_row("ShowTV/Season 1/ShowTV.S01E01.mkv", "movie")]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert triggers == ["movies_full_scan_subtitles"]
    rec = res["instances"][0]
    assert rec["fired"] is True
    assert rec["task_id"] == "movies_full_scan_subtitles"
    assert rec["task_ids"] == ["movies_full_scan_subtitles"]


def test_scheduler_pokes_series_then_movies_for_mixed_rows(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    _, triggers = _serve_live(ws)
    items = [
        _row("ShowTV/Season 1/ShowTV.S01E01.mkv", "movie"),
        _row("ShowTV/Season 1/ShowTV.S01E02.mkv", "episode"),
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert triggers == ["series_full_scan_subtitles", "movies_full_scan_subtitles"]
    assert res["instances"][0]["count"] == 2


def test_scheduler_row_without_media_type_pokes_only_series(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    _, triggers = _serve_live(ws)
    items = [_row("ShowTV/Season 1/ShowTV.S01E01.mkv")]
    asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert triggers == ["series_full_scan_subtitles"]


def test_scheduler_movie_row_on_a_stub_instance_fires_nothing(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    items = [_row("ShowTV/Season 1/ShowTV.S01E01.mkv", "movie")]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    rec = res["instances"][0]
    assert rec["fired"] is False
    assert rec["reason"] == "no scan-disk task id found"
    assert [c for c in ws.calls[("bazarr", "")] if c["path"] == "/api/system/tasks"] == []
    assert "http://bazarr-0.test" not in sched._bazarr_poke_ts


def test_scheduler_later_trigger_failure_keeps_the_fired_record(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    _serve_live(ws, failing=("movies_full_scan_subtitles",))
    items = [
        _row("ShowTV/Season 1/ShowTV.S01E01.mkv", "movie"),
        _row("ShowTV/Season 1/ShowTV.S01E02.mkv", "episode"),
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    rec = res["instances"][0]
    assert rec["fired"] is True
    assert rec["task_ids"] == ["series_full_scan_subtitles"]
    assert rec["error"] == "boom"
    assert "http://bazarr-0.test" in sched._bazarr_poke_ts


def test_scheduler_first_trigger_failure_does_not_advance_the_cooldown(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    _serve_live(ws, failing=("series_full_scan_subtitles", "movies_full_scan_subtitles"))
    items = [_row("ShowTV/Season 1/ShowTV.S01E01.mkv", "episode")]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    rec = res["instances"][0]
    assert rec["fired"] is False
    assert rec["reason"] == "boom"
    assert "http://bazarr-0.test" not in sched._bazarr_poke_ts
    assert res["fired"] is False


# ───── Flag: bazarr_wanted ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_coverage_flags_the_wanted_episode_and_movie_rows(coverage_bundle):
    from test_coverage_characterization import _bazarr, _radarr, _sonarr

    from subarr.coverage_engine import build_coverage

    bundle = coverage_bundle(sonarr_handler=_sonarr, bazarr_handler=_bazarr, radarr_handler=_radarr)
    report = await build_coverage(
        bundle, use_tautulli=False, probe_store=None, audio_lang_store=None, subgen_caps=None
    )
    items = report.to_dict()["items"]
    severance = next(i for i in items if i["title"] == "Severance")
    dune = next(i for i in items if i["title"] == "Dune")
    assert severance["bazarr_wanted"] is True
    assert dune["bazarr_wanted"] is True


def _movie(rid, title):
    return {
        "id": rid,
        "title": title,
        "hasFile": True,
        "monitored": True,
        "path": f"/data/Media/Movies/{title}",
        "movieFile": {"path": f"/data/Media/Movies/{title}/{title}.mkv"},
        "originalLanguage": {"name": "English"},
        "tags": [],
    }


def _run(movies, items, idx):
    from subarr.coverage_engine import _add_radarr_blind_movie_rows

    activity = {"now_playing_titles": set(), "transcoding_titles": set(), "audio_lang_hints": {}}
    return asyncio.run(
        _add_radarr_blind_movie_rows(
            movies,
            items,
            radarr_tags={},
            radarr_missing_ids=set(),
            radarr_recent_ids={},
            activity=activity,
            tt_signals={},
            probe_by_series_prefix=idx,
            failed_idx={},
            user_verifications={},
            whisper_verifications={},
            plex_hints={},
            sources={},
            ignore_forced_subtitles=False,
            ignore_image_subtitles=False,
        )
    )


def test_radarr_blind_movie_rows_are_not_bazarr_wanted(subarr_env):
    rows = _run([_movie(1, "GapMovie")], [], {})
    assert rows
    assert all(it.bazarr_wanted is False for it in rows)
    assert all(it.to_dict()["bazarr_wanted"] is False for it in rows)


def test_bazarr_wanted_round_trips_through_the_snapshot_rebuild():
    from subarr.coverage_engine import CoverageItem
    from subarr.routers.schedule import _COVERAGE_ITEM_FIELDS

    d = CoverageItem(media_type="movie", title="X", bazarr_wanted=True).to_dict()
    assert d["bazarr_wanted"] is True
    rebuilt = CoverageItem(**{k: v for k, v in d.items() if k in _COVERAGE_ITEM_FIELDS})
    assert rebuilt.bazarr_wanted is True

    d = CoverageItem(media_type="movie", title="X").to_dict()
    rebuilt = CoverageItem(**{k: v for k, v in d.items() if k in _COVERAGE_ITEM_FIELDS})
    assert rebuilt.bazarr_wanted is False
