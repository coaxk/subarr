"""#364 slice 1 — orchestration end-to-end with injected VAD/clip/LID/translate
and a fake gate-input resolver. No audio, no ffmpeg, no real subgen."""

from __future__ import annotations

import importlib
import logging

import pytest


@pytest.fixture
def gen(subarr_env, tmp_path):
    # subarr_env sets SUBARR_MEDIA_ROOT to a tmp media root with TV/Show/ep.mkv.
    from subarr import config, paths
    from subarr import forced_segment_service as svc
    from subarr.forced_segment import ForcedSegmentParams
    from subarr.forced_segment_store import ForcedSegmentScanStore
    from subarr.migrate import run_migrations

    importlib.reload(config)
    importlib.reload(paths)
    importlib.reload(svc)
    db = config.settings.db_path
    run_migrations(db)
    store = ForcedSegmentScanStore(db)

    def make(*, utterances, lid_map, translate_map, gate=(True, "ok"), duration=3600.0):
        g = svc.ForcedSegmentGenerator(
            subgen=object(),  # unused: LID/translate are injected below
            scan_store=store,
            params=ForcedSegmentParams(min_span_s=2.0, merge_gap_s=1.0, mostly_foreign_fraction=0.6),
            vad_fn=lambda fs_path, track=0: utterances,
            clip_fn=lambda fs_path, s, e, out, track=0: None,  # no real ffmpeg
            # LID is keyed on the utterance span; subgen_path threads Branch A/B.
            lid_fn=lambda clip_path, subgen_path, span: lid_map[span],
            # translate is called ONCE per merged span and returns a TIMESTAMPED SRT.
            translate_fn=lambda clip_path, span: translate_map[span],
            gate_fn=lambda canonical: (gate[0], gate[1], duration, 42),  # (ok, reason, duration_s, size)
        )
        return g, store

    return make


def _identity(canonical_path):
    from subarr.paths import canonical_to_fs

    st = canonical_to_fs(canonical_path).stat()
    return st.st_mtime, st.st_size


@pytest.mark.asyncio
async def test_generates_forced_sidecar_for_a_foreign_scene(gen):
    from subarr.paths import canonical_to_fs

    utts = [(0.0, 60.0), (60.0, 63.0), (63.2, 66.0)]
    # subgen /asr returns a TIMESTAMPED SRT for the whole span (relative to the
    # clip start); the orchestrator must offset each cue to absolute file time
    # and emit them as SEPARATE cues, never one fused 6s cue.
    span_srt = "1\n00:00:00,000 --> 00:00:03,000\nCome with me\n\n2\n00:00:03,000 --> 00:00:06,000\nNow\n"
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9), (63.2, 66.0): ("fr", 0.9)},
        translate_map={(60.0, 66.0): span_srt},  # keyed on the MERGED span
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "scanned" and result["n_spans"] == 1
    sidecar = canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt")
    assert sidecar.exists()
    body = sidecar.read_text(encoding="utf-8")
    # TWO separate cues, each offset to the span absolute start (60s).
    assert body.count("-->") == 2
    assert "00:01:00,000 --> 00:01:03,000" in body and "Come with me" in body
    assert "00:01:03,000 --> 00:01:06,000" in body and "Now" in body
    # cache records the verdict keyed on stat mtime+size (same source).
    mtime, size = _identity("TV/Show/ep.mkv")
    assert store.get("TV/Show/ep.mkv", mtime=mtime, size=size) is not None


@pytest.mark.asyncio
async def test_long_span_emits_multiple_offset_cues(gen):
    """A long foreign scene must become MULTIPLE readable cues, each offset by
    the span start — not one unreadable multi-minute subtitle."""
    from subarr.paths import canonical_to_fs

    # English dominates so the file does not bail; one 60s foreign span at 300s.
    utts = [(0.0, 300.0), (300.0, 360.0)]
    span_srt = (
        "1\n00:00:00,000 --> 00:00:04,000\nLine A\n\n"
        "2\n00:00:10,000 --> 00:00:14,000\nLine B\n\n"
        "3\n00:00:50,000 --> 00:00:54,000\nLine C\n"
    )
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 300.0): ("en", 0.95), (300.0, 360.0): ("fr", 0.9)},
        translate_map={(300.0, 360.0): span_srt},
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "scanned" and result["n_spans"] == 1
    body = canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt").read_text(encoding="utf-8")
    assert body.count("-->") == 3  # three separate cues, NOT one fused span cue
    assert "00:05:00,000 --> 00:05:04,000" in body and "Line A" in body  # 0s + 300s
    assert "00:05:10,000 --> 00:05:14,000" in body and "Line B" in body  # 10s + 300s
    assert "00:05:50,000 --> 00:05:54,000" in body and "Line C" in body  # 50s + 300s


@pytest.mark.asyncio
async def test_no_foreign_scene_writes_nothing_and_records_none(gen):
    from subarr.paths import canonical_to_fs

    utts = [(0.0, 60.0)]
    g, store = gen(utterances=utts, lid_map={(0.0, 60.0): ("en", 0.95)}, translate_map={})
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "none" and result["n_spans"] == 0
    assert not canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt").exists()


@pytest.mark.asyncio
async def test_mostly_foreign_bails_without_writing(gen):
    from subarr.paths import canonical_to_fs

    utts = [(0.0, 40.0), (40.0, 80.0)]
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 40.0): ("fr", 0.9), (40.0, 80.0): ("fr", 0.9)},
        translate_map={},
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "bailed"
    assert not canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt").exists()


@pytest.mark.asyncio
async def test_never_clobbers_an_existing_forced_sidecar(gen):
    from subarr.paths import canonical_to_fs

    sidecar = canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt")
    sidecar.write_text("PRE-EXISTING", encoding="utf-8")
    g, store = gen(
        utterances=[(0.0, 60.0), (60.0, 63.0)],
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9)},
        translate_map={(60.0, 63.0): "hi"},
        gate=(False, "existing_forced"),
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "skipped" and result["reason"] == "existing_forced"
    assert sidecar.read_text(encoding="utf-8") == "PRE-EXISTING"


@pytest.mark.asyncio
async def test_cache_hit_skips_rescan(gen):
    utts = [(0.0, 60.0), (60.0, 63.0)]
    span_srt = "1\n00:00:00,000 --> 00:00:03,000\nhi\n"
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9)},
        translate_map={(60.0, 63.0): span_srt},
    )
    first = await g.process("TV/Show/ep.mkv")
    assert first["status"] == "scanned"
    second = await g.process("TV/Show/ep.mkv")  # unchanged file
    assert second["status"] == "cached"


# --- extra coverage for the load-bearing requirements the plan's own tests do
# --- not exercise directly (req #2 disk-level no-clobber + path-containment,
# --- req #4 vad-unavailable, plus the review's never-raises guard). ---


@pytest.mark.asyncio
async def test_disk_level_no_clobber_when_gate_ok(gen):
    """Even when the gate passes, a .forced.en.srt already on disk is never
    overwritten (defence-in-depth over the gate's has_forced_sidecar): skip +
    record a distinct 'exists' status, original bytes intact."""
    from subarr.paths import canonical_to_fs

    sidecar = canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt")
    sidecar.write_text("HUMAN-EDITED", encoding="utf-8")
    g, store = gen(
        utterances=[(0.0, 60.0), (60.0, 63.0)],
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9)},
        translate_map={(60.0, 63.0): "hi"},
        gate=(True, "ok"),  # gate passes; the DISK check must still block
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "skipped" and result["reason"] == "existing_forced"
    assert sidecar.read_text(encoding="utf-8") == "HUMAN-EDITED"
    mtime, size = _identity("TV/Show/ep.mkv")
    hit = store.get("TV/Show/ep.mkv", mtime=mtime, size=size)
    assert hit is not None and hit.status == "exists"  # distinct, not misleading 'none'


@pytest.mark.asyncio
async def test_path_traversal_is_contained_and_writes_nothing(gen):
    g, store = gen(utterances=[(0.0, 60.0)], lid_map={}, translate_map={})
    result = await g.process("../../etc/evil.mkv")
    assert result["status"] == "error" and result["reason"] == "unresolvable"


@pytest.mark.asyncio
async def test_vad_unavailable_is_recorded_and_logged_not_silent(gen, monkeypatch, caplog):
    """#416: VAD unavailable must be a recorded, LOGGED skip — never a silent
    no-op. Empty utterances + vad_available() False => a distinct verdict."""
    from subarr.paths import canonical_to_fs

    monkeypatch.setattr("subarr.vad.vad_available", lambda: False)
    g, store = gen(utterances=[], lid_map={}, translate_map={})
    with caplog.at_level(logging.WARNING, logger="subarr.forced_segment_service"):
        result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "vad-unavailable"
    assert not canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt").exists()
    assert any("VAD unavailable" in r.getMessage() for r in caplog.records)
    # Recorded (visible in summary), but NOT a cache hit — a transient verdict
    # stays re-scannable so a later walk retries once the VAD model is pulled.
    mtime, size = _identity("TV/Show/ep.mkv")
    assert store.get("TV/Show/ep.mkv", mtime=mtime, size=size) is None
    assert store.summary()["total_scanned"] == 1


@pytest.mark.asyncio
async def test_process_never_raises_on_internal_error(gen, caplog):
    """The docstring promises 'never raises': an unexpected internal failure
    (here VAD) is caught, LOGGED, recorded as 'error', and returned — the walker
    / import hook must never crash."""

    g, store = gen(utterances=[(0.0, 60.0)], lid_map={}, translate_map={})

    def boom(_fs_path, track=0):
        raise RuntimeError("VAD subsystem exploded")

    g._vad = boom
    with caplog.at_level(logging.WARNING, logger="subarr.forced_segment_service"):
        result = await g.process("TV/Show/ep.mkv")  # must NOT raise
    assert result["status"] == "error"
    assert any("process failed" in r.getMessage() for r in caplog.records)
    # Recorded (visible in summary), but NOT a cache hit — a transient error
    # stays re-scannable so a subgen outage mid-walk does not permanently skip.
    mtime, size = _identity("TV/Show/ep.mkv")
    assert store.get("TV/Show/ep.mkv", mtime=mtime, size=size) is None
    assert store.summary()["total_scanned"] == 1
