"""#131 — tuning-lab arena: multi-clip orchestration + cross-clip aggregation.

The validated methodology: judge N SEPARATE strata clips independently, then a
config is trustworthy only if it wins ACROSS clips. Tests cover the pure
aggregation/confidence logic, the per-clip orchestration (fake runner + fake
judge), and the real AsrRunner (sampler injected) over a mocked transport.
"""
from __future__ import annotations

import json
from collections import namedtuple

import httpx
import pytest

from subarr.arena import (
    AggregateRow, ArenaUnsupported, AsrRunner, ClipResult, ConfigVariant,
    _aggregate, _confidence, run_arena,
)
from subarr.subgen_client import SubgenCapabilities, SubgenClient
from subarr.tournament import Scorecard


def _sc(label, composite, *, dq=False, qe=None):
    return Scorecard(entrant_label=label, disqualified=dq, composite=composite,
                     readability_score=70.0, cue_count=10, gen_time_s=None,
                     readability=None, qe_adequacy=qe)


def _clip(kind, winner, agreement, scores):
    """scores: {label: composite}. Builds a ClipResult with serialized cards."""
    from dataclasses import asdict
    cards = [asdict(_sc(l, c)) for l, c in scores.items()]
    return ClipResult(kind=kind, winner=winner, agreement=agreement, scorecards=cards)


# ── pure aggregation + confidence ───────────────────────────────────────────

def test_aggregate_winner_is_best_mean_composite():
    variants = [ConfigVariant("a", {}), ConfigVariant("b", {})]
    per_clip = [
        _clip("speech", "a", 0.8, {"a": 80, "b": 50}),
        _clip("silence", "a", 0.75, {"a": 70, "b": 40}),
    ]
    rows, winner, conf, consistency, agr, tie = _aggregate(per_clip, variants)
    assert winner == "a"
    assert rows[0].label == "a" and rows[0].mean_composite == 75.0
    assert rows[0].clips_won == 2
    assert consistency == 1.0            # 'a' topped both clips
    assert conf == "high"                # consistent + agreement >= 0.7
    assert tie is False                  # 75 vs 45 — clear gap


def test_flipping_winner_lowers_confidence():
    variants = [ConfigVariant("a", {}), ConfigVariant("b", {})]
    per_clip = [
        _clip("speech", "a", 0.55, {"a": 80, "b": 60}),
        _clip("silence", "b", 0.55, {"a": 40, "b": 95}),  # b wins here
    ]
    rows, winner, conf, consistency, agr, tie = _aggregate(per_clip, variants)
    # b has higher mean (77.5 vs 60) → overall winner, but only topped 1/2 clips
    assert winner == "b"
    assert consistency == 0.5
    assert conf == "low"                 # flips + weak agreement


def test_aggregate_flags_a_tie_when_scores_converge():
    variants = [ConfigVariant("a", {}), ConfigVariant("b", {})]
    per_clip = [
        _clip("speech", "a", 1.0, {"a": 43.6, "b": 43.6}),
        _clip("silence", "a", 1.0, {"a": 43.6, "b": 43.6}),
    ]
    rows, winner, conf, consistency, agr, tie = _aggregate(per_clip, variants)
    assert tie is True                   # identical output → no real winner


def test_confidence_thresholds():
    assert _confidence(1.0, 0.8, 3) == "high"
    assert _confidence(0.5, 0.6, 3) == "moderate"
    assert _confidence(0.5, 0.5, 3) == "low"
    assert _confidence(1.0, 0.9, 1) == "low"   # one clip can never be high
    assert _confidence(None, 0.9, 3) == "low"


# ── orchestration (fake runner + fake judge) ────────────────────────────────

_FakeRes = namedtuple("_FakeRes", "scorecards winner_label clip_agreement")


def _fake_judge(per_clip_scores, agreement=0.8):
    """Return a judge() that, per call, ranks the given candidates by a fixed
    score map and returns the matching winner + agreement."""
    def judge(candidates, *, speech_ranges=None, source_text=None, **kw):
        cards = [_sc(l, per_clip_scores.get(l, 0.0)) for l in candidates]
        cards.sort(key=lambda c: c.composite, reverse=True)
        winner = cards[0].entrant_label if cards else None
        return _FakeRes(cards, winner, agreement)
    return judge


class FakeRunner:
    def __init__(self, clips, responder, supported=True):
        self._clips = clips
        self._responder = responder
        self._supported = supported
        self.calls = []
        self.prepared = None
        self.cleaned = False

    async def preflight(self):
        if not self._supported:
            raise ArenaUnsupported("nope")

    async def prepare(self, media_path):
        self.prepared = media_path
        return self._clips

    async def run(self, clip_idx, *, task, kwargs):
        self.calls.append({"clip": clip_idx, "task": task, "kwargs": kwargs})
        return self._responder(clip_idx, task, kwargs)

    async def cleanup(self):
        self.cleaned = True


@pytest.mark.asyncio
async def test_preflight_gate_blocks_unsupported():
    runner = FakeRunner([], lambda *a: None, supported=False)
    with pytest.raises(ArenaUnsupported):
        await run_arena("/x.mkv", [ConfigVariant("a", {})], runner=runner)


@pytest.mark.asyncio
async def test_runs_every_recipe_on_every_clip_then_aggregates():
    clips = [{"kind": "speech", "ranges": [(0, 3)]}, {"kind": "silence", "ranges": []}]
    # every run() returns a non-empty srt so all recipes are scored on all clips
    runner = FakeRunner(clips, lambda ci, task, kw: "1\n00:00:00,000 --> 00:00:01,000\nx\n")
    variants = [ConfigVariant("a", {}), ConfigVariant("b", {})]
    # judge: a wins both clips
    res = await run_arena("/m.mkv", variants, runner=runner, judge=_fake_judge({"a": 90, "b": 50}))

    assert runner.prepared == "/m.mkv" and runner.cleaned is True
    # 2 clips × (1 source + 2 recipes) = 6 run() calls
    assert len(runner.calls) == 6
    assert sum(1 for c in runner.calls if c["task"] == "transcribe") == 2  # source per clip
    assert len(res.per_clip) == 2
    assert res.winner == "a" and res.confidence == "high"
    assert [r.label for r in res.aggregate][0] == "a"


@pytest.mark.asyncio
async def test_recipe_that_never_produces_is_marked_failed():
    clips = [{"kind": "speech", "ranges": []}]
    # recipe 'b' (boom kwarg) always returns None
    def responder(ci, task, kw):
        if kw.get("boom"):
            return None
        return "1\n00:00:00,000 --> 00:00:01,000\nx\n"
    runner = FakeRunner(clips, responder)
    variants = [ConfigVariant("a", {}), ConfigVariant("b", {"boom": True})]
    res = await run_arena("/m.mkv", variants, runner=runner, judge=_fake_judge({"a": 80}))

    b = next(o for o in res.outcomes if o.label == "b")
    assert b.srt_text is None and b.error is not None


@pytest.mark.asyncio
async def test_step_and_clip_callbacks_fire():
    clips = [{"kind": "speech", "ranges": []}, {"kind": "silence", "ranges": []}]
    runner = FakeRunner(clips, lambda *a: "1\n00:00:00,000 --> 00:00:01,000\nx\n")
    steps, clips_seen = [], []
    await run_arena("/m.mkv", [ConfigVariant("a", {})], runner=runner,
                    judge=_fake_judge({"a": 80}),
                    on_clip=lambda i, k, t: clips_seen.append((i, k, t)),
                    on_step=lambda: steps.append(1))
    assert clips_seen == [(0, "speech", 2), (1, "silence", 2)]
    assert len(steps) == 2 * (1 + 1)   # 2 clips × (source + 1 recipe)


@pytest.mark.asyncio
async def test_empty_variants_rejected():
    with pytest.raises(ValueError):
        await run_arena("/x.mkv", [], runner=FakeRunner([], lambda *a: None))


# ── AsrRunner (sampler injected) over a mocked transport ────────────────────

def _client(handler):
    c = SubgenClient(base_url="http://fake:9000")
    c._client = httpx.AsyncClient(base_url="http://fake:9000", transport=httpx.MockTransport(handler))
    return c


def _caps(asr_arena):
    return SubgenCapabilities(reachable=True, version="x", has_queue=True,
                              has_batch=True, is_subarr_subgen=True, asr_arena=asr_arena)


@pytest.mark.asyncio
async def test_asr_runner_preflight_requires_capability():
    runner = AsrRunner(_client(lambda r: httpx.Response(200)), capabilities=_caps(False))
    with pytest.raises(ArenaUnsupported):
        await runner.preflight()


@pytest.mark.asyncio
async def test_asr_runner_samples_clips_then_uploads_each(tmp_path):
    c0 = tmp_path / "c0.wav"; c0.write_bytes(b"RIFF0")
    c1 = tmp_path / "c1.wav"; c1.write_bytes(b"RIFF1")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"params": dict(req.url.params), "uploaded": b"audio_file" in req.content})
        return httpx.Response(200, text="1\n00:00:00,000 --> 00:00:01,000\nx\n",
                              headers={"content-type": "text/plain"})

    runner = AsrRunner(_client(handler), capabilities=_caps(True), source_language="ko",
                       to_fs_path=lambda p: p,
                       sampler=lambda fs: [
                           {"path": str(c0), "kind": "speech", "ranges": [(0.0, 2.0)]},
                           {"path": str(c1), "kind": "silence", "ranges": []},
                       ])
    await runner.preflight()
    clips = await runner.prepare("TV/Show/ep.mkv")
    assert [c["kind"] for c in clips] == ["speech", "silence"]

    out = await runner.run(1, task="translate", kwargs={"beam_size": 5})
    assert out.startswith("1\n")
    assert seen[-1]["uploaded"] is True
    assert "path" not in seen[-1]["params"]      # upload mode
    assert seen[-1]["params"]["task"] == "translate"
    assert json.loads(seen[-1]["params"]["kwargs"]) == {"beam_size": 5}

    await runner.cleanup()


# ── ollama explainer (best-effort) ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_explain_graceful_without_ollama():
    from subarr.arena_explain import explain
    assert await explain({"aggregate": [], "per_clip": []}, "/x.mkv", None) is None


@pytest.mark.asyncio
async def test_explain_uses_ollama_when_configured():
    from subarr.arena_explain import explain

    class FakeOllama:
        def is_configured(self):
            return True
        async def generate(self, prompt, *, system=None, **kw):
            assert "43.6" in prompt and "TIE" in prompt   # result data reached the prompt
            return "  This clip is easy — settings don't matter here.  "

    out = await explain(
        {"aggregate": [{"label": "a", "mean_composite": 43.6, "clips_won": 2, "disqualified_in": 0}],
         "per_clip": [{"kind": "speech"}, {"kind": "silence"}], "tie": True, "agreement_mean": 1.0},
        "/m.mkv", FakeOllama())
    assert out == "This clip is easy — settings don't matter here."


@pytest.mark.asyncio
async def test_explain_swallows_ollama_errors():
    from subarr.arena_explain import explain

    class BoomOllama:
        def is_configured(self):
            return True
        async def generate(self, prompt, *, system=None, **kw):
            raise RuntimeError("ollama down")

    assert await explain({"aggregate": [], "per_clip": []}, "/m.mkv", BoomOllama()) is None
