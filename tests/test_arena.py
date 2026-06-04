"""#131 — tuning-lab arena orchestrator.

The arena drives a config sweep against the LIVE subgen model and ranks the
outputs with the validated tournament judge. These tests prove the
ORCHESTRATION logic (capability gating, source-transcribe-once,
per-variant translate with isolated scratch dirs, judging) against a fake
workspace + a recording subgen client — no real subgen / filesystem.
"""
from __future__ import annotations

import pytest

from subarr.arena import (
    ArenaUnsupported,
    ConfigVariant,
    run_arena,
)
from subarr.subgen_client import SubgenCapabilities


def _caps(*, kwargs=True, task=True, batch=True) -> SubgenCapabilities:
    return SubgenCapabilities(
        reachable=True, version="2026.05.3",
        has_queue=True, has_batch=batch, is_subarr_subgen=True,
        per_request_kwargs=kwargs, per_request_task=task,
    )


def _srt(line: str) -> str:
    return f"1\n00:00:00,000 --> 00:00:03,000\n{line}\n"


class RecordingSubgen:
    """Fake SubgenClient: records every batch() call, returns a queued count."""
    def __init__(self, caps: SubgenCapabilities):
        self._caps = caps
        self.calls: list[dict] = []

    async def probe_capabilities(self) -> SubgenCapabilities:
        return self._caps

    async def batch(self, directory, *, task=None, kwargs=None,
                    force_language=None, **rest):
        self.calls.append({"directory": directory, "task": task,
                           "kwargs": kwargs, "force_language": force_language})
        return 200, {"walked": 1, "queued": 1}


class FakeWorkspace:
    """Stages clips into labelled dirs; returns a preset SRT per label.

    `subtitles` maps label → srt text (or None to simulate 'no output').
    Missing label → falls back to `default_srt`.
    """
    def __init__(self, subtitles: dict[str, str | None], default_srt: str | None = None):
        self._subs = subtitles
        self._default = default_srt
        self.staged: list[str] = []
        self.cleaned = False

    async def stage(self, media_path, label):
        self.staged.append(label)
        from subarr.arena import StagedClip
        return StagedClip(label=label, subgen_dir=f"/scratch/{label}")

    async def await_subtitle(self, clip, *, timeout_s: float = 600):
        return self._subs.get(clip.label, self._default)

    async def cleanup(self):
        self.cleaned = True


@pytest.mark.asyncio
async def test_arena_unsupported_when_task_capability_missing():
    sg = RecordingSubgen(_caps(task=False))
    ws = FakeWorkspace({})
    with pytest.raises(ArenaUnsupported):
        await run_arena("/media/clip.mkv", [ConfigVariant("a", {})],
                        subgen=sg, workspace=ws)


@pytest.mark.asyncio
async def test_arena_unsupported_when_kwargs_capability_missing():
    sg = RecordingSubgen(_caps(kwargs=False))
    ws = FakeWorkspace({})
    with pytest.raises(ArenaUnsupported):
        await run_arena("/media/clip.mkv", [ConfigVariant("a", {})],
                        subgen=sg, workspace=ws)


@pytest.mark.asyncio
async def test_source_transcribed_once_then_each_variant_translated():
    sg = RecordingSubgen(_caps())
    ws = FakeWorkspace({
        "__source__": _srt("the reactor is overheating"),
        "noisy": _srt("reactor overheating now"),
        "clean": _srt("the weather is fine"),
    })
    variants = [ConfigVariant("noisy", {"vad_filter": True}),
                ConfigVariant("clean", {"beam_size": 5})]

    res = await run_arena("/media/clip.mkv", variants, subgen=sg,
                          workspace=ws, source_language="ko")

    # one transcribe (source) + two translate (variants)
    tasks = [c["task"] for c in sg.calls]
    assert tasks == ["transcribe", "translate", "translate"]
    # source call seeded the source language
    assert sg.calls[0]["force_language"] == "ko"
    # each variant forwarded its own kwargs
    assert sg.calls[1]["kwargs"] == {"vad_filter": True}
    assert sg.calls[2]["kwargs"] == {"beam_size": 5}
    # source transcript extracted to plain text for QE
    assert res.source_text == "the reactor is overheating"
    # judged both candidates
    assert {o.label for o in res.outcomes} == {"noisy", "clean"}
    assert res.tournament is not None


@pytest.mark.asyncio
async def test_variant_with_no_subtitle_is_dropped_from_judging():
    sg = RecordingSubgen(_caps())
    ws = FakeWorkspace({
        "__source__": _srt("source line"),
        "good": _srt("a translated line"),
        "broken": None,           # produced no subtitle
    })
    variants = [ConfigVariant("good", {}), ConfigVariant("broken", {})]

    res = await run_arena("/media/clip.mkv", variants, subgen=sg, workspace=ws)

    broken = next(o for o in res.outcomes if o.label == "broken")
    assert broken.srt_text is None
    assert broken.error is not None
    # the broken variant must not have reached the judge
    judged = {sc.entrant_label for sc in res.tournament.scorecards}
    assert "broken" not in judged
    assert "good" in judged


@pytest.mark.asyncio
async def test_empty_variants_rejected():
    sg = RecordingSubgen(_caps())
    with pytest.raises(ValueError):
        await run_arena("/media/clip.mkv", [], subgen=sg, workspace=FakeWorkspace({}))
