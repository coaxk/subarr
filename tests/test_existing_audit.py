"""#216 Phase 1: audit EXISTING external subtitles with the aftercare scorer.

These pin the runner's orchestration — skip subarr's own output, score the
rest, store under the existing_audit source, count progress, survive bad
files — without touching the filesystem or DB (deps are injected).
"""

from __future__ import annotations

import pytest

from subarr import existing_audit as ea
from subarr.aftercare import AftercareEvaluation


def _ev(flagged: bool, composite: float = 0.9) -> AftercareEvaluation:
    return AftercareEvaluation(
        composite=composite, cue_count=10, flagged=flagged, readability=None, signals=None
    )


class TestDiscoverExternalSrts:
    def test_finds_srts_recursively_skips_non_srt(self, tmp_path):
        (tmp_path / "Show.en.srt").write_text("x")
        sub = tmp_path / "Season 1"
        sub.mkdir()
        (sub / "Ep.en.srt").write_text("x")
        (tmp_path / "notes.txt").write_text("x")
        found = ea.discover_external_srts([tmp_path])
        assert found == sorted([str(tmp_path / "Show.en.srt"), str(sub / "Ep.en.srt")])

    def test_missing_root_is_skipped_not_fatal(self, tmp_path):
        (tmp_path / "A.en.srt").write_text("x")
        found = ea.discover_external_srts([tmp_path, tmp_path / "does-not-exist"])
        assert found == [str(tmp_path / "A.en.srt")]

    def test_dedups_across_overlapping_roots(self, tmp_path):
        (tmp_path / "A.en.srt").write_text("x")
        found = ea.discover_external_srts([tmp_path, tmp_path])
        assert found == [str(tmp_path / "A.en.srt")]


class TestIsSubarrGenerated:
    def test_subgen_filename_marker_is_ours(self):
        assert ea.is_subarr_generated("/m/Show.S01E01.subgen.large-v3.en.srt", set())

    def test_path_in_provenance_set_is_ours(self):
        p = "/m/Show.S01E01.en.srt"
        assert ea.is_subarr_generated(p, {p})

    def test_plain_external_sub_is_not_ours(self):
        assert not ea.is_subarr_generated("/m/Show.S01E01.en.srt", set())


@pytest.mark.asyncio
async def test_skips_generated_scores_and_stores_the_rest():
    paths = [
        "/m/A.subgen.en.srt",  # ours (marker) -> skip
        "/m/B.en.srt",  # external, clean
        "/m/C.en.srt",  # external, flagged
    ]
    flags = {"/m/B.en.srt": False, "/m/C.en.srt": True}
    stored: list[dict] = []

    async def probe(_p):
        return 1500.0

    # read_text returns the path so the fake evaluator can key the flag off it
    summary = await ea.run_existing_audit(
        paths,
        generated_paths=set(),
        read_text=lambda p: p,
        probe_duration=probe,
        evaluate=lambda text, dur: _ev(flagged=flags[text]),
        record=lambda **kw: stored.append(kw),
        now=123.0,
    )
    assert summary.total == 3
    assert summary.skipped == 1  # the .subgen. one
    assert summary.scanned == 2
    assert summary.errors == 0
    assert {s["canonical_path"] for s in stored} == {"/m/B.en.srt", "/m/C.en.srt"}
    assert all(s["source"] == ea.EXISTING_AUDIT_SOURCE for s in stored)
    assert all(s["completed_at"] == 123.0 for s in stored)


@pytest.mark.asyncio
async def test_flagged_count_and_duration_passthrough():
    seen_durations = []

    def evaluate(text, dur):
        seen_durations.append(dur)
        return _ev(flagged=True)

    async def probe(_p):
        return 42.0

    summary = await ea.run_existing_audit(
        ["/m/B.en.srt"],
        generated_paths=set(),
        read_text=lambda p: "cue",
        probe_duration=probe,
        evaluate=evaluate,
        record=lambda **kw: None,
        now=1.0,
    )
    assert summary.flagged == 1
    assert seen_durations == [42.0]  # ffprobe duration reaches the scorer


@pytest.mark.asyncio
async def test_unreadable_file_counts_as_error_and_continues():
    stored = []

    def read_text(p):
        if p == "/m/bad.en.srt":
            raise OSError("permission denied")
        return "cue"

    async def probe(_p):
        return None

    summary = await ea.run_existing_audit(
        ["/m/bad.en.srt", "/m/good.en.srt"],
        generated_paths=set(),
        read_text=read_text,
        probe_duration=probe,
        evaluate=lambda t, d: _ev(flagged=False),
        record=lambda **kw: stored.append(kw),
        now=1.0,
    )
    assert summary.errors == 1
    assert summary.scanned == 1
    assert [s["canonical_path"] for s in stored] == ["/m/good.en.srt"]


@pytest.mark.asyncio
async def test_progress_callback_fires_per_processed_file():
    ticks = []

    async def probe(_p):
        return None

    await ea.run_existing_audit(
        ["/m/a.subgen.en.srt", "/m/b.en.srt"],
        generated_paths=set(),
        read_text=lambda p: "cue",
        probe_duration=probe,
        evaluate=lambda t, d: _ev(flagged=False),
        record=lambda **kw: None,
        now=1.0,
        on_progress=lambda done, total: ticks.append((done, total)),
    )
    # one tick per path (including the skipped one), final reaches total
    assert ticks[-1] == (2, 2)
