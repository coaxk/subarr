"""#364 slice 1 — at-import hook. Enabled+wired schedules a background scan;
disabled is a no-op; the hook NEVER blocks (it only schedules)."""

from __future__ import annotations

import asyncio
import importlib

import pytest


class _Entry:
    canonical_path = "TV/Show/ep.mkv"
    id = 1


class _FakeGen:
    def __init__(self):
        self.calls = []

    async def process(self, canonical_path):
        self.calls.append(canonical_path)


def _watcher():
    from subarr.completion_watcher import CompletionWatcher

    return CompletionWatcher()


@pytest.mark.asyncio
async def test_hook_schedules_when_enabled(subarr_env, monkeypatch):
    from subarr import config, completion_watcher

    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "1")
    importlib.reload(config)
    importlib.reload(completion_watcher)
    w = completion_watcher.CompletionWatcher()
    gen = _FakeGen()
    w._forced_segment = gen
    w._maybe_forced_segment(_Entry())
    await asyncio.sleep(0)  # let the scheduled task run
    assert gen.calls == ["TV/Show/ep.mkv"]


@pytest.mark.asyncio
async def test_hook_noop_when_disabled(subarr_env, monkeypatch):
    from subarr import config, completion_watcher

    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "0")
    importlib.reload(config)
    importlib.reload(completion_watcher)
    w = completion_watcher.CompletionWatcher()
    gen = _FakeGen()
    w._forced_segment = gen
    w._maybe_forced_segment(_Entry())
    await asyncio.sleep(0)
    assert gen.calls == []


@pytest.mark.asyncio
async def test_hook_noop_when_not_wired(subarr_env, monkeypatch):
    from subarr import config, completion_watcher

    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "1")
    importlib.reload(config)
    importlib.reload(completion_watcher)
    w = completion_watcher.CompletionWatcher()  # no _forced_segment wired
    # must not raise
    w._maybe_forced_segment(_Entry())
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_at_import_task_is_retained_and_released(subarr_env, monkeypatch):
    """The at-import fire-and-forget scan must be held on a strong reference so
    the GC cannot cancel a long scan (VAD -> per-utterance LID -> translate)
    mid-flight and silently drop the .forced.en.srt. It is released on
    completion so the set does not leak."""
    from subarr import config, completion_watcher

    monkeypatch.setenv("SUBARR_FORCED_SEGMENT_ENABLED", "1")
    importlib.reload(config)
    importlib.reload(completion_watcher)
    w = completion_watcher.CompletionWatcher()

    gate = asyncio.Event()

    class _BlockingGen:
        def __init__(self):
            self.started = False

        async def process(self, canonical_path):
            self.started = True
            await gate.wait()

    w._forced_segment = _BlockingGen()
    w._maybe_forced_segment(_Entry())
    await asyncio.sleep(0)  # let the task start and block on the event
    # Held on a strong ref while running so a GC pass cannot cancel it.
    assert len(w._forced_segment_tasks) == 1
    assert w._forced_segment.started is True

    # Release: finish the scan; the done-callback discards the ref.
    tasks = list(w._forced_segment_tasks)
    gate.set()
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)  # let the done-callback run
    assert w._forced_segment_tasks == set()
