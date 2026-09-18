"""#562: Coverage kept showing rows as Analyzing after their eager probe had
already finished, until an unrelated later rebuild.

`CoverageCache.refresh` publishes its snapshot, then starts the eager probes,
and nothing asked for a new snapshot when they finished. Now a finished eager
batch that wrote new results requests one coalesced follow-up build. When the
batch covered the whole backlog it runs within a short grace instead of the
min-interval spacing; a batch that hit the cap keeps the normal spacing so a
large library is not rebuilt back to back.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


# ---- ProbeWalker: completion callback and what counts as a result --------


@pytest.fixture
def walker(subarr_env, tmp_path):
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore
    from subarr.probe_walker import ProbeWalker

    db = tmp_path / "probe.db"
    run_migrations(db)
    store = ProbeStore(db)
    return ProbeWalker(store), store


def _make_file(canonical: str):
    from subarr.config import settings

    p = settings.media_root / canonical
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"data")
    return p


def _stub_probe(monkeypatch, fail=False):
    from subarr import probe_walker as pw
    from subarr.media_probe import ProbeResult

    async def fake_probe(path, timeout_s=30.0):
        if fail:
            raise RuntimeError("ffprobe could not read it")
        return ProbeResult(canonical_path="")

    monkeypatch.setattr(pw, "probe", fake_probe)


def _run_walk(wk, paths):
    done = []

    async def go():
        state = await wk.probe_paths(paths, on_done=done.append)
        while state.status == "running":
            await asyncio.sleep(0.01)
        await asyncio.sleep(0)  # let the callback run
        return state

    state = asyncio.run(go())
    return state, done


def test_on_done_fires_once_with_the_finished_state(walker, monkeypatch):
    wk, _ = walker
    _make_file("TV/A/a.mkv")
    _stub_probe(monkeypatch)
    state, done = _run_walk(wk, ["TV/A/a.mkv"])
    assert done == [state]
    assert state.status == "done" and state.probed == 1


def test_recorded_failures_are_counted_separately_from_stat_errors(walker, monkeypatch):
    wk, _ = walker
    _make_file("TV/A/bad.mkv")
    _stub_probe(monkeypatch, fail=True)
    state, _ = _run_walk(wk, ["TV/A/bad.mkv", "TV/Ghost/missing.mkv"])
    assert state.failures_recorded == 1  # ffprobe failure, written to the store
    assert len(state.errors) == 2  # plus the missing file, which is not


# ---- CoverageCache: follow-up build after an eager batch -----------------


class _Report:
    def __init__(self, items):
        self._items = items

    def to_dict(self):
        return {"items": self._items, "totals": {}, "sources": {}}


def _cache(tmp_path, monkeypatch, items):
    from subarr import coverage_engine
    from subarr.coverage_cache import CoverageCache
    from subarr.migrate import run_migrations

    async def fake_build(*a, **k):
        return _Report(items)

    monkeypatch.setattr(coverage_engine, "build_coverage", fake_build)
    db = tmp_path / "cov.db"
    run_migrations(db)
    return CoverageCache(db)


class _Walker:
    """Finishes each batch at once with the given counts."""

    def __init__(self, probed=0, failures_recorded=0, status="done"):
        self.probed, self.failures_recorded, self.status = probed, failures_recorded, status
        self.batches = []

    async def probe_paths(self, paths, on_done=None, **k):
        self.batches.append(list(paths))
        state = SimpleNamespace(
            status=self.status, probed=self.probed, failures_recorded=self.failures_recorded, errors=[]
        )
        if on_done:
            on_done(state)
        return state


def _unprobed(n):
    return [
        {
            "file_canonical_path": f"TV/S/S - S01E{i:02d}.mkv",
            "verification_state": "unprobed",
            "media_type": "episode",
        }
        for i in range(n)
    ]


def _spy(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(cache, "request_refresh", lambda *a, **k: calls.append(k))
    return calls


def _refresh(cache, walker):
    asyncio.run(cache.refresh(bundle=None, probe_store=None, audio_lang_store=None, probe_walker=walker))


def test_new_results_request_a_prompt_follow_up(subarr_env, tmp_path, monkeypatch):
    from subarr.coverage_cache import EAGER_FOLLOWUP_MAX_WAIT_S

    cache = _cache(tmp_path, monkeypatch, _unprobed(2))
    calls = _spy(cache, monkeypatch)
    _refresh(cache, _Walker(probed=2))
    assert len(calls) == 1
    assert calls[0]["max_wait_s"] == EAGER_FOLLOWUP_MAX_WAIT_S
    assert calls[0]["probe_walker"] is not None


def test_recorded_failures_also_request_a_follow_up(subarr_env, tmp_path, monkeypatch):
    cache = _cache(tmp_path, monkeypatch, _unprobed(1))
    calls = _spy(cache, monkeypatch)
    _refresh(cache, _Walker(failures_recorded=1))
    assert len(calls) == 1


def test_all_cached_batch_requests_nothing(subarr_env, tmp_path, monkeypatch):
    """Nothing new was learned, so a rebuild would change nothing. This is also
    what stops a refresh loop: the follow-up build's own eager batch is cached."""
    cache = _cache(tmp_path, monkeypatch, _unprobed(3))
    calls = _spy(cache, monkeypatch)
    _refresh(cache, _Walker(probed=0))
    assert calls == []


def test_a_failed_walk_requests_nothing(subarr_env, tmp_path, monkeypatch):
    cache = _cache(tmp_path, monkeypatch, _unprobed(1))
    calls = _spy(cache, monkeypatch)
    _refresh(cache, _Walker(probed=1, status="error"))
    assert calls == []


def test_a_capped_batch_keeps_the_normal_spacing(subarr_env, tmp_path, monkeypatch):
    from subarr.coverage_cache import _EAGER_PROBE_CAP

    cache = _cache(tmp_path, monkeypatch, _unprobed(_EAGER_PROBE_CAP + 5))
    calls = _spy(cache, monkeypatch)
    _refresh(cache, _Walker(probed=10))
    assert len(calls) == 1
    assert calls[0]["max_wait_s"] is None


def test_no_unprobed_rows_starts_no_walk(subarr_env, tmp_path, monkeypatch):
    cache = _cache(tmp_path, monkeypatch, [])
    calls = _spy(cache, monkeypatch)
    wk = _Walker(probed=1)
    _refresh(cache, wk)
    assert wk.batches == [] and calls == []


# ---- request_refresh: max_wait_s shortens the debounce --------------------


def test_max_wait_shortens_the_debounce(subarr_env, tmp_path, monkeypatch):
    cache = _cache(tmp_path, monkeypatch, [])
    cache.set_min_interval_s(120)
    started = []
    monkeypatch.setattr(cache, "_start_refresh_task", lambda args: started.append(args))

    async def go():
        loop = asyncio.get_running_loop()
        cache._last_refresh_done = loop.time()  # a build just finished
        cache.request_refresh(None, None, None, max_wait_s=0.05)
        await asyncio.sleep(0.2)

    asyncio.run(go())
    assert len(started) == 1


def test_without_max_wait_the_debounce_is_unchanged(subarr_env, tmp_path, monkeypatch):
    cache = _cache(tmp_path, monkeypatch, [])
    cache.set_min_interval_s(120)
    started = []
    monkeypatch.setattr(cache, "_start_refresh_task", lambda args: started.append(args))

    async def go():
        loop = asyncio.get_running_loop()
        cache._last_refresh_done = loop.time()
        cache.request_refresh(None, None, None)
        await asyncio.sleep(0.2)
        assert cache._debounce_handle is not None
        cache._debounce_handle.cancel()

    asyncio.run(go())
    assert started == []


def test_max_wait_pulls_an_already_armed_timer_forward(subarr_env, tmp_path, monkeypatch):
    cache = _cache(tmp_path, monkeypatch, [])
    cache.set_min_interval_s(120)
    started = []
    monkeypatch.setattr(cache, "_start_refresh_task", lambda args: started.append(args))

    async def go():
        loop = asyncio.get_running_loop()
        cache._last_refresh_done = loop.time()
        cache.request_refresh(None, None, None)  # arms a ~120 s timer
        cache.request_refresh(None, None, None, max_wait_s=0.05)
        await asyncio.sleep(0.2)

    asyncio.run(go())
    assert len(started) == 1


def test_max_wait_still_coalesces_behind_an_in_flight_build(subarr_env, tmp_path, monkeypatch):
    cache = _cache(tmp_path, monkeypatch, [])
    cache._refreshing = True
    started = []
    monkeypatch.setattr(cache, "_start_refresh_task", lambda args: started.append(args))

    async def go():
        cache.request_refresh(None, None, None, max_wait_s=0.0)

    asyncio.run(go())
    assert started == [] and cache._pending_again is True
