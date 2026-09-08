"""#506: force a re-probe that bypasses the mtime/size cache.

The probe cache is keyed on (canonical_path, mtime, size) and self-invalidates
when either changes, which covers most edits. A LANGUAGE TAG correction is the
pathological case it cannot see: `und` -> `eng` is the same byte length, so an
in-place tag edit can leave size identical, and a tool that preserves mtime
leaves both matching. The entry then stands forever and Review keeps showing
`audio: und` against a file whose metadata is already correct.

Targeted probes hit the same early return in `_run_targeted`, which is what the
reporter (AztecGuyGDL, after correcting files with Tdarr) means by "a normal
re-walk is cache-aware and does not provide a reliable way to force ffprobe".

Design note pinned by these tests: forcing SKIPS THE CACHE READ rather than
deleting the entry first. Deleting up front destroys good data if the re-probe
then fails; this way the old entry survives until a new one replaces it.
"""

from __future__ import annotations

import pytest


class _FakeStore:
    """Records whether the cache was consulted, and always reports a hit."""

    def __init__(self):
        self.get_calls = 0
        self.deleted: list[str] = []
        self.upserts: list[str] = []

    def get(self, canonical, mtime=None, size=None):
        self.get_calls += 1

        class _Hit:
            cached = True

        return _Hit()

    def delete(self, canonical):
        self.deleted.append(canonical)
        return True


async def _run_and_wait(walker, paths, **kw):
    """`probe_paths` is FIRE AND FORGET: it create_task()s and returns the
    WalkState immediately. Asserting on that state without awaiting the task
    reads it before anything ran, and the loop closing then cancels it -- which
    looks exactly like "the cache was honoured". Await the task."""
    state = await walker.probe_paths(paths, **kw)
    task = walker._tasks.get(state.id)
    if task is not None:
        await task
    return state


@pytest.mark.asyncio
async def test_force_bypasses_the_cache_read(tmp_path, monkeypatch):
    """With force, a cached-and-matching file must still be probed."""
    from subarr import probe_walker as pw

    store = _FakeStore()
    walker = pw.ProbeWalker(store)

    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x")
    monkeypatch.setattr(pw, "canonical_to_fs", lambda c: f)

    probed: list[str] = []

    async def _fake_probe_and_record(state, canonical, p, st):
        probed.append(canonical)
        state.processed += 1

    monkeypatch.setattr(walker, "_probe_and_record", _fake_probe_and_record)

    state = await _run_and_wait(walker, ["TV/S/ep.mkv"], force=True)
    assert probed == ["TV/S/ep.mkv"], "force must probe even on a cache hit"
    assert state.cached_hits == 0


@pytest.mark.asyncio
async def test_without_force_a_cache_hit_still_short_circuits(tmp_path, monkeypatch):
    """Unchanged default. The existing eager-probe caller must not start
    re-probing the whole library because this option was added."""
    from subarr import probe_walker as pw

    store = _FakeStore()
    walker = pw.ProbeWalker(store)

    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x")
    monkeypatch.setattr(pw, "canonical_to_fs", lambda c: f)

    probed: list[str] = []

    async def _fake_probe_and_record(state, canonical, p, st):
        probed.append(canonical)
        state.processed += 1

    monkeypatch.setattr(walker, "_probe_and_record", _fake_probe_and_record)

    state = await _run_and_wait(walker, ["TV/S/ep.mkv"])
    assert store.get_calls == 1, "the walk must actually have consulted the cache"
    assert probed == [], "default must still honour the cache"
    assert state.cached_hits == 1


@pytest.mark.asyncio
async def test_force_does_not_delete_the_cached_entry_up_front(tmp_path, monkeypatch):
    """The old entry must survive until a new probe replaces it, so a failed
    re-probe does not leave the row with nothing."""
    from subarr import probe_walker as pw

    store = _FakeStore()
    walker = pw.ProbeWalker(store)

    f = tmp_path / "ep.mkv"
    f.write_bytes(b"x")
    monkeypatch.setattr(pw, "canonical_to_fs", lambda c: f)

    async def _boom(state, canonical, p, st):
        state.errors.append({"path": canonical, "error": "ffprobe exploded"})
        state.processed += 1

    monkeypatch.setattr(walker, "_probe_and_record", _boom)

    await _run_and_wait(walker, ["TV/S/ep.mkv"], force=True)
    assert store.deleted == [], "force must not pre-delete the cached entry"
