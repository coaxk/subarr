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
