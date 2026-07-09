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
