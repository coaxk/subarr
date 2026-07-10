"""#364 slice 1 — orchestration end-to-end with injected VAD/clip/LID/translate
and a fake gate-input resolver. No audio, no ffmpeg, no real subgen."""

from __future__ import annotations

import asyncio
import importlib
import logging

import pytest


@pytest.fixture
def gen(subarr_env, tmp_path):
    # subarr_env sets SUBARR_MEDIA_ROOT to a tmp media root with TV/Show/ep.mkv,
    # and already reloads config+paths (coordinated with the rest of the app,
    # incl. routers) under that env. Reloading paths AGAIN here would re-mint
    # PathOutsideRootError and desync modules subarr_env re-bound but we don't
    # (e.g. routers.queue), leaking into later tests
    # (reference_subarr-test-module-reload). So only reload forced_segment_service
    # — the one module subarr_env doesn't know about — to re-bind it to the
    # already-reloaded paths.
    from subarr import config
    from subarr import forced_segment_service as svc
    from subarr.forced_segment import ForcedSegmentParams
    from subarr.forced_segment_store import ForcedSegmentScanStore
    from subarr.migrate import run_migrations

    importlib.reload(svc)
    db = config.settings.db_path
    run_migrations(db)
    store = ForcedSegmentScanStore(db)

    def make(*, utterances, lid_map, translate_map, gate=(True, "ok"), duration=3600.0, local_lid=None):
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
            local_lid=local_lid,
        )
        return g, store

    return make


@pytest.fixture
def gen_factory(tmp_path):
    """A leaner factory than `gen`: builds a ForcedSegmentGenerator directly with
    sensible no-op fakes (vad/clip/lid/translate/gate), letting a test override
    just the piece it cares about (e.g. translate_fn) for calling an internal
    method like _build_cues directly, with no real ffmpeg/subgen/media root."""
    from subarr import forced_segment_service as svc
    from subarr.forced_segment import ForcedSegmentParams
    from subarr.forced_segment_store import ForcedSegmentScanStore
    from subarr.migrate import run_migrations

    db = tmp_path / "gen_factory.db"
    run_migrations(db)
    store = ForcedSegmentScanStore(db)

    def make(
        *,
        translate_fn=None,
        lid_fn=None,
        vad_fn=None,
        clip_fn=None,
        gate_fn=None,
        params=None,
    ):
        return svc.ForcedSegmentGenerator(
            subgen=object(),  # unused: LID/translate are injected below
            scan_store=store,
            params=params or ForcedSegmentParams(),
            vad_fn=vad_fn or (lambda fs_path, track=0: []),
            clip_fn=clip_fn or (lambda fs_path, s, e, out, track=0: None),  # no real ffmpeg
            lid_fn=lid_fn or (lambda clip_path, subgen_path, span: (None, 0.0)),
            translate_fn=translate_fn or (lambda clip_path, span: ("", None)),
            gate_fn=gate_fn or (lambda canonical: (True, "ok", 3600.0, 42)),
        )

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
        translate_map={(60.0, 66.0): (span_srt, None)},  # keyed on the MERGED span; source lang unknown
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
        translate_map={(300.0, 360.0): (span_srt, None)},
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
        translate_map={(60.0, 63.0): ("hi", None)},
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
        translate_map={(60.0, 63.0): (span_srt, None)},
    )
    first = await g.process("TV/Show/ep.mkv")
    assert first["status"] == "scanned"
    second = await g.process("TV/Show/ep.mkv")  # unchanged file
    assert second["status"] == "cached"


@pytest.mark.asyncio
async def test_cache_hit_skips_gate(gen):
    """A resume walk over an already-scanned file must NOT recompute the gate
    (probe_store read + canonical_to_fs + stat + sidecar .exists + audio_lang):
    the (canonical, mtime, size) cache check runs BEFORE the gate. The cache key
    takes size from stat, not the gate return, so the check never depends on it."""
    g, store = gen(utterances=[(0.0, 60.0)], lid_map={}, translate_map={})
    # Pre-seed a terminal 'scanned' row keyed on the file's real stat identity.
    mtime, size = _identity("TV/Show/ep.mkv")
    store.upsert(
        canonical_path="TV/Show/ep.mkv", mtime=mtime, size=size, status="scanned", n_spans=1, total_ms=0
    )

    gate_calls = []

    def recording_gate(canonical):
        gate_calls.append(canonical)
        return (True, "ok", 3600.0, size)

    g._gate = recording_gate
    result = await g.process("TV/Show/ep.mkv")
    assert result["status"] == "cached"
    assert gate_calls == []  # the gate was NOT run on a cache hit


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
        translate_map={(60.0, 63.0): ("hi", None)},
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


# --- #364 slice 2 Task 4: translate-arbiter (drop false-positive spans) ---


def test_build_cues_drops_span_whose_source_is_primary(gen_factory):
    from subarr.forced_segment import Span

    # subgen returns a TIMESTAMPED SRT (parse_srt requires real timestamp lines
    # to unpack a cue, same as every other _build_cues test in this file) — a
    # bare sentence would parse to zero cues regardless of the arbiter, so both
    # branches return a minimal one-cue SRT to actually exercise the drop.
    async def fake_translate(clip, span):
        s0 = span[0]
        if s0 < 10:
            return ("1\n00:00:00,000 --> 00:00:01,000\nHola.\n", "es")
        return ("1\n00:00:00,000 --> 00:00:01,000\nhello there\n", "en")

    g = gen_factory(translate_fn=fake_translate)
    spans = [Span(0, 4000), Span(20000, 24000)]
    cues = asyncio.run(g._build_cues("/x.mkv", spans, "/tmp"))
    texts = [t for _s, _e, t in cues]
    assert any("Hola" in t for t in texts)
    assert not any("hello there" in t for t in texts)


def test_subgen_translate_returns_text_and_lang():
    from subarr import forced_segment_service as svc

    class FakeSubgen:
        async def asr(self, *, local_file, task, return_language=False):
            assert task == "translate" and return_language is True
            return ("translated text", "es")

    text, lang = asyncio.run(svc.subgen_translate(FakeSubgen(), "/clip.wav"))
    assert text == "translated text" and lang == "es"


# --- #364 slice 2 Task 5: wire local_lid (windowed backend) into the generator ---


@pytest.mark.asyncio
async def test_generator_uses_local_lid_when_present(gen):
    """When a local_lid backend is injected, _run must call its .classify()
    instead of the per-utterance subgen _classify (which uses lid_fn). All
    utterances classify as English (outcome 'none') so the test only needs to
    assert WHICH path ran, not the merge/translate machinery downstream."""
    utts = [(0.0, 60.0), (60.0, 63.0)]

    calls = {"local": 0}

    class FakeLocal:
        async def classify(self, fs_path, utterances, tmp):
            calls["local"] += 1
            return [(u, False) for u in utterances]  # all English -> 'none' outcome

    lid_calls = {"n": 0}

    def counting_lid(clip_path, subgen_path, span):
        lid_calls["n"] += 1
        return ("fr", 0.9)  # would force a foreign span if this path ran

    g, store = gen(
        utterances=utts,
        lid_map={u: ("fr", 0.9) for u in utts},
        translate_map={},
        local_lid=FakeLocal(),
    )
    g._lid = counting_lid  # overwrite after construction to detect any fallback use

    result = await g.process("TV/Show/ep.mkv")

    assert calls["local"] == 1  # local backend was used
    assert lid_calls["n"] == 0  # per-utterance subgen lid_fn was NOT called
    assert result["status"] == "none"  # all-English classification -> no forced spans
