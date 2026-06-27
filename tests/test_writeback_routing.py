"""#161 Phase 3 — writeback routing: writes go to the instance owning the row."""

from __future__ import annotations


def test_writeback_stack_resolves_instances(writeback_stack):
    from subarr.coverage_engine import clients_for

    b = writeback_stack.bundle
    # an @anime/ row resolves to the anime instances of all three services
    rc = clients_for(b, "@anime/Naruto/Season 1/Naruto.S01E01.mkv")
    assert rc.bazarr is b._clients["bazarr"]["anime"]
    assert rc.sonarr is b._clients["sonarr"]["anime"]
    assert rc.radarr is b._clients["radarr"]["anime"]
    # a default-library row resolves to instance 0
    rc0 = clients_for(b, "ShowTV/Season 1/ShowTV.S01E01.mkv")
    assert rc0.bazarr is b._clients["bazarr"][""]
    assert rc0.sonarr is b._clients["sonarr"][""]


def _watcher(bundle):
    from types import SimpleNamespace

    from subarr.completion_watcher import CompletionWatcher

    prov = SimpleNamespace(
        mark_bazarr_triggered=lambda _id: None,
        completed_without_bazarr=lambda max_age_s=0: [],
    )
    return CompletionWatcher(provenance=prov, bundle_provider=lambda: bundle)


def _seed_srt(canonical: str) -> None:
    from pathlib import Path

    from subarr.paths import canonical_to_fs

    fs = Path(canonical_to_fs(canonical))
    fs.parent.mkdir(parents=True, exist_ok=True)
    fs.write_bytes(b"\x00")  # the video must exist — _find_srt_sidecar checks full.exists()
    (fs.parent / (fs.stem + ".en.srt")).write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")


def _subtitle_posts(calls, key):
    return [c for c in calls.get(key, []) if c["path"] == "/api/episodes/subtitles"]


def test_subtitle_upload_routes_to_owning_bazarr(writeback_stack):
    import asyncio
    from types import SimpleNamespace

    ws = writeback_stack
    canonical = "@anime/Naruto/Season 1/Naruto.S01E01.mkv"
    _seed_srt(canonical)
    w = _watcher(ws.bundle)
    entry = SimpleNamespace(id=1, canonical_path=canonical, sonarr_episode_id=1011, series_id=11)

    assert asyncio.run(w._try_upload_to_bazarr(entry)) is True
    assert _subtitle_posts(ws.calls, ("bazarr", "anime")), "anime bazarr should receive the upload"
    assert not _subtitle_posts(ws.calls, ("bazarr", "")), "instance 0 must NOT receive it"


def test_subtitle_upload_default_row_hits_instance0(writeback_stack):
    import asyncio
    from types import SimpleNamespace

    ws = writeback_stack
    canonical = "ShowTV/Season 1/ShowTV.S01E01.mkv"
    _seed_srt(canonical)
    w = _watcher(ws.bundle)
    entry = SimpleNamespace(id=2, canonical_path=canonical, sonarr_episode_id=2201, series_id=22)

    assert asyncio.run(w._try_upload_to_bazarr(entry)) is True
    assert _subtitle_posts(ws.calls, ("bazarr", "")), "instance 0 should receive the default-row upload"
    assert not _subtitle_posts(ws.calls, ("bazarr", "anime")), "anime must NOT receive it"


def test_audio_lang_bazarr_sync_routes_to_owning_instance(writeback_stack):
    import asyncio

    from subarr.routers.audio_lang import _trigger_bazarr_sync

    ws = writeback_stack

    def triggers(key):
        return [c for c in ws.calls.get(key, []) if c["path"] == "/api/system/tasks"]

    asyncio.run(_trigger_bazarr_sync(ws.bundle, "@anime/Naruto/Season 1/Naruto.S01E01.mkv"))
    assert triggers(("bazarr", "anime")), "anime bazarr should get the sync trigger"
    assert not triggers(("bazarr", "")), "instance 0 must NOT get it"


def _fake_request(bundle):
    from types import SimpleNamespace

    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(integrations=bundle)))


def test_blacklist_episode_routes_to_owning_bazarr(writeback_stack):
    import asyncio

    from subarr.routers.blacklist import EpisodeBlacklistRequest, blacklist_episode

    ws = writeback_stack
    req = EpisodeBlacklistRequest(
        series_id=11,
        episode_id=1011,
        provider="x",
        subs_id="s",
        subtitles_path="/p.srt",
        canonical_path="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
    )
    asyncio.run(blacklist_episode(req, _fake_request(ws.bundle)))

    def bl(key):
        return [c for c in ws.calls.get(key, []) if c["path"] == "/api/episodes/blacklist"]

    assert bl(("bazarr", "anime")) and not bl(("bazarr", ""))


def test_blacklist_movie_routes_to_owning_bazarr(writeback_stack):
    import asyncio

    from subarr.routers.blacklist import MovieBlacklistRequest, blacklist_movie

    ws = writeback_stack
    req = MovieBlacklistRequest(
        radarr_id=5,
        provider="x",
        subs_id="s",
        subtitles_path="/p.srt",
        canonical_path="@anime/SomeFilm/SomeFilm.mkv",
    )
    asyncio.run(blacklist_movie(req, _fake_request(ws.bundle)))

    def bl(key):
        return [c for c in ws.calls.get(key, []) if c["path"] == "/api/movies/blacklist"]

    assert bl(("bazarr", "anime")) and not bl(("bazarr", ""))


def test_arbiter_accept_routes_to_owning_bazarr(writeback_stack):
    import asyncio

    from subarr.routers.arbiter import AcceptRequest, accept_candidate

    ws = writeback_stack
    req = AcceptRequest(
        episode_id=1011,
        provider="x",
        subtitles_id="s",
        score=99,
        canonical_path="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
    )
    asyncio.run(accept_candidate(req, _fake_request(ws.bundle)))

    def dl(key):
        return [c for c in ws.calls.get(key, []) if c["path"] == "/api/providers/episodes"]

    assert dl(("bazarr", "anime")) and not dl(("bazarr", ""))


def test_bazarr_sync_disk_routes_to_owning_instance(writeback_stack):
    import asyncio

    from subarr.routers.bazarr_sync import SyncDiskRequest, sync_disk

    ws = writeback_stack
    asyncio.run(
        sync_disk(
            _fake_request(ws.bundle),
            SyncDiskRequest(canonical_path="@anime/Naruto/Season 1/Naruto.S01E01.mkv"),
        )
    )

    def trig(key):
        return [c for c in ws.calls.get(key, []) if c["path"] == "/api/system/tasks"]

    assert trig(("bazarr", "anime")) and not trig(("bazarr", ""))


def test_scheduler_poke_fans_out_per_bazarr_instance(writeback_stack):
    import asyncio
    from types import SimpleNamespace

    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    items = [
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            file_canonical_path="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
            canonical_path=None,
        ),
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
            canonical_path=None,
        ),
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    def trig(key):
        return [c for c in ws.calls.get(key, []) if c["path"] == "/api/system/tasks"]

    assert trig(("bazarr", "anime")), "anime bazarr should be poked"
    assert trig(("bazarr", "")), "instance 0 should be poked too"
    assert res["fired"] is True


def test_retry_bazarr_notify_isolates_one_instances_unexpected_error(writeback_stack):
    # #372: a NON-IntegrationError from one Bazarr's task discovery must not abort
    # the retry pass for the other instances. Anime's list_tasks blows up; the
    # default instance must still fire its scan-disk trigger.
    import asyncio
    from types import SimpleNamespace

    from subarr.completion_watcher import CompletionWatcher

    ws = writeback_stack
    # anime row first so the raising instance is processed before the survivor —
    # under the bug this aborts the whole pass before instance 0 is reached.
    entries = [
        SimpleNamespace(id=1, series_id=10, canonical_path="@anime/Naruto/Season 1/Naruto.S01E01.mkv"),
        SimpleNamespace(id=2, series_id=20, canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv"),
    ]
    marked: list[int] = []
    prov = SimpleNamespace(
        completed_without_bazarr=lambda max_age_s=0: entries,
        mark_bazarr_triggered=lambda _id: marked.append(_id),
    )
    watcher = CompletionWatcher(provenance=prov, bundle_provider=lambda: ws.bundle)

    async def boom():
        raise RuntimeError("list_tasks exploded")  # NOT an IntegrationError

    ws.bundle._clients["bazarr"]["anime"].list_tasks = boom

    asyncio.run(watcher._pass_retry_bazarr_notify())

    default_triggers = [
        c
        for c in ws.calls.get(("bazarr", ""), [])
        if c["path"] == "/api/system/tasks" and c["method"] != "GET"
    ]
    assert default_triggers, "instance 0 must still fire despite anime's non-IntegrationError"
    assert 2 in marked  # the default row was notified
    assert 1 not in marked  # the anime row was NOT (its trigger never fired)


def test_poke_cooldown_is_per_instance(writeback_stack):
    # #371: a recent poke on one Bazarr instance must NOT gate a DIFFERENT
    # instance whose rows just went stale. Cooldown is keyed per instance.
    import asyncio
    import time
    from types import SimpleNamespace

    from subarr.scheduler import Scheduler

    ws = writeback_stack
    sched = Scheduler(None, bundle=ws.bundle)
    # instance 0 (tv) was poked just now; the anime instance never was.
    cooled = time.time()
    sched._bazarr_poke_ts = {"http://bazarr-0.test": cooled}
    items = [
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            file_canonical_path="@anime/Naruto/Season 1/Naruto.S01E01.mkv",
            canonical_path=None,
        ),
        SimpleNamespace(
            suggest_bazarr_rescan=True,
            file_canonical_path="ShowTV/Season 1/ShowTV.S01E01.mkv",
            canonical_path=None,
        ),
    ]
    res = asyncio.run(sched._maybe_poke_bazarr_for_stale_disk(items))

    def trig(key):
        return [c for c in ws.calls.get(key, []) if c["path"] == "/api/system/tasks"]

    assert trig(("bazarr", "anime")), "anime is not under cooldown — it must fire"
    assert not trig(("bazarr", "")), "instance 0 is under its own cooldown — it must NOT fire"
    assert res["fired"] is True  # the op fired (anime), even though instance 0 was gated
    # per-instance advance: anime now cooled; instance 0's ts is unchanged (it didn't fire)
    assert "http://bazarr-anime.test" in sched._bazarr_poke_ts
    assert sched._bazarr_poke_ts["http://bazarr-0.test"] == cooled
