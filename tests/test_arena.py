"""#131 — tuning-lab arena orchestrator.

Proves the ORCHESTRATION logic against a fake runner: preflight gate, sample-
once (prepare), source-transcribe, per-variant translate on the sample,
judging, bad-variant survival, cleanup — plus the real `AsrRunner` (sampler
injected) over a mocked subgen transport in /asr upload mode.
"""
from __future__ import annotations

import json

import httpx
import pytest

from subarr.arena import ArenaUnsupported, AsrRunner, ConfigVariant, run_arena
from subarr.subgen_client import SubgenCapabilities, SubgenClient


def _srt(line: str) -> str:
    return f"1\n00:00:00,000 --> 00:00:03,000\n{line}\n"


class FakeRunner:
    """Records run() calls; returns a preset SRT per call. First run() is the
    source transcribe, then one per variant. None = 'no subtitle produced'."""
    def __init__(self, outputs, supported=True, ranges=None):
        self._outputs = list(outputs)
        self._supported = supported
        self._ranges = ranges if ranges is not None else [(0.0, 3.0)]
        self.calls = []
        self.preflighted = False
        self.prepared = None
        self.cleaned = False

    async def preflight(self):
        self.preflighted = True
        if not self._supported:
            raise ArenaUnsupported("nope")

    async def prepare(self, media_path):
        self.prepared = media_path
        return self._ranges

    async def run(self, *, task, kwargs):
        self.calls.append({"task": task, "kwargs": kwargs})
        return self._outputs.pop(0) if self._outputs else None

    async def cleanup(self):
        self.cleaned = True


@pytest.mark.asyncio
async def test_preflight_gate_blocks_unsupported_subgen():
    runner = FakeRunner([], supported=False)
    with pytest.raises(ArenaUnsupported):
        await run_arena("/x.mkv", [ConfigVariant("a", {})], runner=runner)
    assert runner.preflighted


@pytest.mark.asyncio
async def test_samples_once_then_source_then_each_variant():
    runner = FakeRunner([
        _srt("the reactor is overheating"),   # source transcribe
        _srt("reactor overheating now"),       # variant noisy
        _srt("the weather is fine"),           # variant clean
    ])
    variants = [ConfigVariant("noisy", {"vad_filter": True}),
                ConfigVariant("clean", {"beam_size": 5})]

    res = await run_arena("/media/clip.mkv", variants, runner=runner)

    assert runner.prepared == "/media/clip.mkv"     # sampled the file once
    assert runner.cleaned is True                    # temp clip cleaned up
    assert runner.calls[0] == {"task": "transcribe", "kwargs": {}}
    variant_calls = runner.calls[1:]
    assert all(c["task"] == "translate" for c in variant_calls)
    assert {frozenset(c["kwargs"].items()) for c in variant_calls} == {
        frozenset({"vad_filter": True}.items()), frozenset({"beam_size": 5}.items()),
    }
    assert res.source_text == "the reactor is overheating"
    assert {o.label for o in res.outcomes} == {"noisy", "clean"}
    assert res.tournament is not None


@pytest.mark.asyncio
async def test_clip_speech_ranges_reach_the_judge():
    # prepare() returns ranges that cover the whole cue; the silence judge
    # should then see full coverage (no uncovered speech) — proves ranges flow.
    seen = {}

    def judge(candidates, *, speech_ranges=None, source_text=None, **kw):
        seen["ranges"] = speech_ranges
        from subarr.tournament import run_tournament
        from subarr.tournament_harness import judge_candidates
        return judge_candidates(candidates, speech_ranges=speech_ranges, source_text=source_text)

    runner = FakeRunner([_srt("hi"), _srt("hi there")], ranges=[(1.0, 9.0)])
    await run_arena("/m.mkv", [ConfigVariant("a", {})], runner=runner, judge=judge)
    assert seen["ranges"] == [(1.0, 9.0)]


@pytest.mark.asyncio
async def test_variant_with_no_subtitle_is_dropped_from_judging():
    runner = FakeRunner([_srt("source line"), _srt("a translated line"), None])
    variants = [ConfigVariant("good", {}), ConfigVariant("broken", {})]
    res = await run_arena("/media/clip.mkv", variants, runner=runner)

    broken = next(o for o in res.outcomes if o.label == "broken")
    assert broken.srt_text is None and broken.error is not None
    judged = {sc.entrant_label for sc in res.tournament.scorecards}
    assert "broken" not in judged and "good" in judged


@pytest.mark.asyncio
async def test_one_variant_raising_does_not_sink_the_sweep():
    class FlakyRunner(FakeRunner):
        async def run(self, *, task, kwargs):
            if kwargs.get("boom"):
                raise RuntimeError("subgen exploded")
            return await super().run(task=task, kwargs=kwargs)

    runner = FlakyRunner([_srt("src"), _srt("ok line")])
    variants = [ConfigVariant("boom", {"boom": True}), ConfigVariant("ok", {})]
    res = await run_arena("/x.mkv", variants, runner=runner)

    boom = next(o for o in res.outcomes if o.label == "boom")
    assert "subgen exploded" in boom.error
    assert {sc.entrant_label for sc in res.tournament.scorecards} == {"ok"}
    assert runner.cleaned is True  # cleanup runs even when a variant errors


@pytest.mark.asyncio
async def test_empty_variants_rejected():
    with pytest.raises(ValueError):
        await run_arena("/x.mkv", [], runner=FakeRunner([]))


# ── AsrRunner (sampler injected) over a mocked transport ────────────────────

def _client(handler) -> SubgenClient:
    c = SubgenClient(base_url="http://fake:9000")
    c._client = httpx.AsyncClient(base_url="http://fake:9000", transport=httpx.MockTransport(handler))
    return c


def _caps(asr_arena):
    return SubgenCapabilities(reachable=True, version="x", has_queue=True,
                              has_batch=True, is_subarr_subgen=True, asr_arena=asr_arena)


@pytest.mark.asyncio
async def test_asr_runner_preflight_requires_asr_arena():
    runner = AsrRunner(_client(lambda r: httpx.Response(200)), capabilities=_caps(False))
    with pytest.raises(ArenaUnsupported):
        await runner.preflight()


@pytest.mark.asyncio
async def test_asr_runner_samples_then_uploads_clip(tmp_path):
    clip = tmp_path / "sample.wav"
    clip.write_bytes(b"RIFFfakeaudio")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["params"] = dict(req.url.params)
        seen["uploaded"] = b"audio_file" in req.content  # multipart field present
        return httpx.Response(200, text=_srt("translated"), headers={"content-type": "text/plain"})

    runner = AsrRunner(_client(handler), capabilities=_caps(True), source_language="ko",
                       to_fs_path=lambda p: p, sampler=lambda fs: (str(clip), [(0.0, 2.0)]))

    await runner.preflight()
    ranges = await runner.prepare("TV/Show/ep.mkv")
    assert ranges == [(0.0, 2.0)]

    out = await runner.run(task="translate", kwargs={"beam_size": 5})
    assert out == _srt("translated")
    assert seen["path"] == "/asr"
    assert seen["uploaded"] is True
    assert "path" not in seen["params"]            # upload mode → no path param
    assert seen["params"]["task"] == "translate"
    assert seen["params"]["language"] == "ko"
    assert json.loads(seen["params"]["kwargs"]) == {"beam_size": 5}

    await runner.cleanup()
