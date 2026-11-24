"""#680: poke Bazarr only for stale rows Bazarr lists as wanted."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

LIVE = [
    {"job_id": "update_series", "name": "Sync with Sonarr"},
    {"job_id": "movies_full_scan_subtitles", "name": "Index All Existing Movies Subtitles"},
    {"job_id": "series_full_scan_subtitles", "name": "Index All Existing Episodes Subtitles"},
]


def _trig(ws, key):
    return [c for c in ws.calls.get(key, []) if c["path"] == "/api/system/tasks"]


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


def test_wanted_and_flagged_row_fires_instance_0(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    items = [
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            bazarr_wanted=True,
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
            canonical_path=None,
        )
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert res["fired"] is True
    assert res["stale_count"] == 1
    assert res["not_bazarr_wanted"] == 0
    assert _trig(ws, ("bazarr", ""))


def test_not_wanted_flagged_row_does_not_fire(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    items = [
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            bazarr_wanted=False,
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
            canonical_path=None,
        )
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert res == {"fired": False, "reason": "no stale-disk items", "not_bazarr_wanted": 1}
    assert not _trig(ws, ("bazarr", ""))
    assert not getattr(sched, "_bazarr_poke_ts", {})


def test_row_without_bazarr_wanted_attribute_does_not_fire(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    items = [
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
            canonical_path=None,
        )
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert res == {"fired": False, "reason": "no stale-disk items", "not_bazarr_wanted": 1}
    assert not _trig(ws, ("bazarr", ""))


def test_wanted_but_not_flagged_row_does_not_fire_or_count_as_not_wanted(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    items = [
        SimpleNamespace(
            suggest_bazarr_rescan=False,
            bazarr_wanted=True,
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
            canonical_path=None,
        )
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert res == {"fired": False, "reason": "no stale-disk items", "not_bazarr_wanted": 0}
    assert not _trig(ws, ("bazarr", ""))


def test_only_the_instance_owning_a_wanted_row_fires(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    items = [
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            bazarr_wanted=True,
            file_canonical_path="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
            canonical_path=None,
        ),
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            bazarr_wanted=False,
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
            canonical_path=None,
        ),
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert _trig(ws, ("bazarr", "anime"))
    assert not _trig(ws, ("bazarr", ""))
    assert res["stale_count"] == 1
    assert res["not_bazarr_wanted"] == 1
    assert len(res["instances"]) == 1
    assert res["instances"][0]["count"] == 1


def test_not_wanted_movie_row_does_not_drag_in_the_movies_task(writeback_stack):
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    _, triggers = _serve_live(ws)
    items = [
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            bazarr_wanted=True,
            media_type="episode",
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
            canonical_path=None,
        ),
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            bazarr_wanted=False,
            media_type="movie",
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E02.mkv",
            canonical_path=None,
        ),
    ]
    asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    assert triggers == ["series_full_scan_subtitles"]


def test_real_coverage_items_behave_like_the_stubs(writeback_stack):
    from subarr.coverage_engine import CoverageItem
    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    wanted = CoverageItem(
        media_type="episode",
        title="A",
        file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
        bazarr_wanted=True,
    )
    wanted.suggest_bazarr_rescan = True
    not_wanted = CoverageItem(
        media_type="episode",
        title="B",
        file_canonical_path="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
        bazarr_wanted=False,
    )
    not_wanted.suggest_bazarr_rescan = True
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk([wanted, not_wanted]))

    assert _trig(ws, ("bazarr", ""))
    assert not _trig(ws, ("bazarr", "anime"))
    assert res["not_bazarr_wanted"] == 1
