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
            lid_fn=lambda clip_path, span: lid_map[span],
            translate_fn=lambda clip_path, span: translate_map[span],
            gate_fn=lambda canonical: (gate[0], gate[1], duration, 42),  # (ok, reason, duration_s, size)
        )
        return g, store

    return make


@pytest.mark.asyncio
async def test_generates_forced_sidecar_for_a_foreign_scene(gen):
    from subarr.paths import canonical_to_fs

    utts = [(0.0, 60.0), (60.0, 63.0), (63.2, 66.0)]
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9), (63.2, 66.0): ("fr", 0.9)},
        translate_map={(60.0, 63.0): "Come with me", (63.2, 66.0): "Now"},
    )
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "scanned" and result["n_spans"] == 1
    sidecar = canonical_to_fs("TV/Show/ep.mkv").with_name("ep.forced.en.srt")
    assert sidecar.exists()
    body = sidecar.read_text(encoding="utf-8")
    assert "00:01:00,000 --> 00:01:06,000" in body  # merged 60s..66s span, absolute time
    assert "Come with me" in body and "Now" in body
    # cache records the verdict keyed on mtime/size
    assert (
        store.get("TV/Show/ep.mkv", mtime=canonical_to_fs("TV/Show/ep.mkv").stat().st_mtime, size=42)
        is not None
    )


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
    g, store = gen(
        utterances=utts,
        lid_map={(0.0, 60.0): ("en", 0.95), (60.0, 63.0): ("fr", 0.9)},
        translate_map={(60.0, 63.0): "hi"},
    )
    first = await g.process("TV/Show/ep.mkv")
    assert first["status"] == "scanned"
    second = await g.process("TV/Show/ep.mkv")  # unchanged file
    assert second["status"] == "cached"


# --- extra coverage for the load-bearing requirements the plan's own tests do
# --- not exercise directly (req #2 disk-level no-clobber + path-containment,
# --- req #4 vad-unavailable). ---


@pytest.mark.asyncio
async def test_disk_level_no_clobber_when_gate_ok(gen):
    """Even when the gate passes, a .forced.en.srt already on disk is never
    overwritten (defence-in-depth over the gate's has_forced_sidecar): skip +
    record, original bytes intact."""
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
    # recorded, not a silent no-op
    assert (
        store.get("TV/Show/ep.mkv", mtime=canonical_to_fs("TV/Show/ep.mkv").stat().st_mtime, size=42)
        is not None
    )


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
    hit = store.get("TV/Show/ep.mkv", mtime=canonical_to_fs("TV/Show/ep.mkv").stat().st_mtime, size=42)
    assert hit is not None and hit.status == "vad-unavailable"  # recorded
