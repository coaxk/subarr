"""#131 — tuning-lab arena orchestrator.

The arena drives a config sweep against the LIVE subgen model and ranks the
outputs with the validated tournament judge. These tests prove the
ORCHESTRATION logic (preflight gate, source-transcribe once, per-variant
translate, judging, bad-variant survival) against a fake runner — plus the
real `AsrRunner` against a mocked subgen transport.
"""
from __future__ import annotations

import httpx
import pytest

from subarr.arena import (
    ArenaUnsupported,
    AsrRunner,
    ConfigVariant,
    run_arena,
)
from subarr.subgen_client import SubgenCapabilities, SubgenClient


def _srt(line: str) -> str:
    return f"1\n00:00:00,000 --> 00:00:03,000\n{line}\n"


class FakeRunner:
    """Records run() calls; returns a preset SRT per task/label sequence.

    `outputs` is consumed in call order: first call is the source transcribe,
    then one per variant. A value of None simulates 'no subtitle produced'.
    """
    def __init__(self, outputs: list[str | None], supported: bool = True):
        self._outputs = list(outputs)
        self._supported = supported
        self.calls: list[dict] = []
        self.preflighted = False

    async def preflight(self):
        self.preflighted = True
        if not self._supported:
            raise ArenaUnsupported("nope")

    async def run(self, media_path, *, task, kwargs):
        self.calls.append({"media_path": media_path, "task": task, "kwargs": kwargs})
        return self._outputs.pop(0) if self._outputs else None


@pytest.mark.asyncio
async def test_preflight_gate_blocks_unsupported_subgen():
    runner = FakeRunner([], supported=False)
    with pytest.raises(ArenaUnsupported):
        await run_arena("/x.mkv", [ConfigVariant("a", {})], runner=runner)
    assert runner.preflighted


@pytest.mark.asyncio
async def test_source_transcribed_once_then_each_variant_translated():
    runner = FakeRunner([
        _srt("the reactor is overheating"),   # source transcribe
        _srt("reactor overheating now"),       # variant noisy
        _srt("the weather is fine"),           # variant clean
    ])
    variants = [ConfigVariant("noisy", {"vad_filter": True}),
                ConfigVariant("clean", {"beam_size": 5})]

    res = await run_arena("/media/clip.mkv", variants, runner=runner)

    tasks = [c["task"] for c in runner.calls]
    assert tasks == ["transcribe", "translate", "translate"]
    # source call carries no per-request kwargs; variants carry their own
    assert runner.calls[0]["kwargs"] == {}
    assert runner.calls[1]["kwargs"] == {"vad_filter": True}
    assert runner.calls[2]["kwargs"] == {"beam_size": 5}
    # source transcript extracted to plain text for QE
    assert res.source_text == "the reactor is overheating"
    assert {o.label for o in res.outcomes} == {"noisy", "clean"}
    assert res.tournament is not None


@pytest.mark.asyncio
async def test_variant_with_no_subtitle_is_dropped_from_judging():
    runner = FakeRunner([
        _srt("source line"),     # source
        _srt("a translated line"),  # good
        None,                    # broken → no sub
    ])
    variants = [ConfigVariant("good", {}), ConfigVariant("broken", {})]

    res = await run_arena("/media/clip.mkv", variants, runner=runner)

    broken = next(o for o in res.outcomes if o.label == "broken")
    assert broken.srt_text is None and broken.error is not None
    judged = {sc.entrant_label for sc in res.tournament.scorecards}
    assert "broken" not in judged and "good" in judged


@pytest.mark.asyncio
async def test_one_variant_raising_does_not_sink_the_sweep():
    class FlakyRunner(FakeRunner):
        async def run(self, media_path, *, task, kwargs):
            if kwargs.get("boom"):
                raise RuntimeError("subgen exploded")
            return await super().run(media_path, task=task, kwargs=kwargs)

    runner = FlakyRunner([_srt("src"), _srt("ok line")])
    variants = [ConfigVariant("boom", {"boom": True}), ConfigVariant("ok", {})]
    res = await run_arena("/x.mkv", variants, runner=runner)

    boom = next(o for o in res.outcomes if o.label == "boom")
    assert "subgen exploded" in boom.error
    assert {sc.entrant_label for sc in res.tournament.scorecards} == {"ok"}


@pytest.mark.asyncio
async def test_empty_variants_rejected():
    with pytest.raises(ValueError):
        await run_arena("/x.mkv", [], runner=FakeRunner([]))


# ── AsrRunner over a real mocked transport ─────────────────────────────────

def _client(handler) -> SubgenClient:
    c = SubgenClient(base_url="http://fake:9000")
    c._client = httpx.AsyncClient(base_url="http://fake:9000", transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_asr_runner_preflight_requires_asr_arena():
    caps = SubgenCapabilities(reachable=True, version="x", has_queue=True,
                              has_batch=True, is_subarr_subgen=True, asr_arena=False)
    runner = AsrRunner(_client(lambda r: httpx.Response(200)), capabilities=caps)
    with pytest.raises(ArenaUnsupported):
        await runner.preflight()


@pytest.mark.asyncio
async def test_asr_runner_maps_path_and_forwards_task_kwargs():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, text=_srt("translated"),
                              headers={"content-type": "text/plain"})

    caps = SubgenCapabilities(reachable=True, version="x", has_queue=True,
                              has_batch=True, is_subarr_subgen=True, asr_arena=True)
    runner = AsrRunner(_client(handler), capabilities=caps, source_language="ko",
                       to_subgen_path=lambda p: "/media/" + p.strip("/"))

    await runner.preflight()
    out = await runner.run("TV/Show/ep.mkv", task="translate", kwargs={"beam_size": 5})

    assert out == _srt("translated")
    assert seen["path"] == "/asr"
    assert seen["params"]["task"] == "translate"
    assert seen["params"]["path"] == "/media/TV/Show/ep.mkv"
    assert seen["params"]["language"] == "ko"
    import json
    assert json.loads(seen["params"]["kwargs"]) == {"beam_size": 5}
