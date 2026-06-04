"""#131 — ArenaService: background run lifecycle + SSE event stream.

Mirrors ScanRunner's shape (create → start → _run task → subscribe()). Driven
here by a fake CandidateRunner so the lifecycle is tested without subgen.
"""
from __future__ import annotations

import asyncio

import pytest

from subarr.arena import ArenaUnsupported, ConfigVariant
from subarr.arena_service import ArenaRun, ArenaService


def _srt(line: str) -> str:
    return f"1\n00:00:00,000 --> 00:00:03,000\n{line}\n"


class FakeRunner:
    def __init__(self, outputs, supported=True):
        self._outputs = list(outputs)
        self._supported = supported

    async def preflight(self):
        if not self._supported:
            raise ArenaUnsupported("needs v4.10")

    async def run(self, media_path, *, task, kwargs):
        return self._outputs.pop(0) if self._outputs else None


def _service(outputs, supported=True) -> ArenaService:
    return ArenaService(build_runner=lambda run: FakeRunner(outputs, supported))


def test_create_returns_pending_run():
    svc = _service([])
    run = svc.create("/media/clip.mkv",
                     [ConfigVariant("a", {"beam_size": 5})], source_language="ko")
    assert run.status == "pending"
    assert run.id and svc.get(run.id) is run
    d = run.to_dict()
    assert d["media_path"] == "/media/clip.mkv"
    assert d["variants"] == [{"label": "a", "kwargs": {"beam_size": 5}}]
    assert d["source_language"] == "ko"


@pytest.mark.asyncio
async def test_run_completes_and_records_result():
    svc = _service([_srt("source line"), _srt("cand a"), _srt("cand b")])
    run = svc.create("/m.mkv", [ConfigVariant("a", {}), ConfigVariant("b", {})])
    svc.start(run)
    await svc._tasks[run.id]

    assert run.status == "done"
    assert run.source_text == "source line"
    assert [o["label"] for o in run.outcomes] == ["a", "b"]
    assert all(o["ok"] for o in run.outcomes)
    assert run.result is not None
    assert "scorecards" in run.result and "winner_label" in run.result


@pytest.mark.asyncio
async def test_preflight_failure_marks_error():
    svc = _service([], supported=False)
    run = svc.create("/m.mkv", [ConfigVariant("a", {})])
    svc.start(run)
    await svc._tasks[run.id]

    assert run.status == "error"
    assert "v4.10" in run.error
    assert run.result is None


@pytest.mark.asyncio
async def test_events_streamed_to_subscriber():
    svc = _service([_srt("src"), _srt("a"), None])
    run = svc.create("/m.mkv", [ConfigVariant("a", {}), ConfigVariant("b", {})])

    events: list[dict] = []

    async def collect():
        async for evt in svc.subscribe(run.id):
            events.append(evt)

    consumer = asyncio.create_task(collect())
    await asyncio.sleep(0)      # let the subscriber register before the run emits
    svc.start(run)
    await svc._tasks[run.id]
    await asyncio.wait_for(consumer, timeout=2)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "start"
    assert "source" in kinds
    assert kinds.count("variant") == 2
    assert kinds[-1] == "done"


def test_list_is_newest_first_with_summaries():
    svc = _service([])
    a = svc.create("/a.mkv", [ConfigVariant("x", {})])
    a.created_at = 100.0
    b = svc.create("/b.mkv", [ConfigVariant("y", {}), ConfigVariant("z", {})])
    b.created_at = 200.0
    listed = svc.list()
    assert [r.id for r in listed] == [b.id, a.id]  # newest first
    s = b.summary()
    assert s["recipe_count"] == 2 and s["status"] == "pending" and "winner" in s
    assert "result" not in s  # summaries stay light (no scorecards)
