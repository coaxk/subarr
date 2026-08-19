"""#359: _run_retime re-times the finished .srt in place, off by default,
best-effort (never blocks completion)."""

from __future__ import annotations

import types

import pytest

from subarr.completion_watcher import CompletionWatcher

_HOT = (
    "1\n00:00:00,000 --> 00:00:02,000\n"
    "This is a very long translated line that crams far too many characters\n\n"
    "2\n00:00:20,000 --> 00:00:20,300\nhi\n"
)
_CALM = (
    "1\n00:00:00,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:04,000 --> 00:00:07,000\nGeneral Kenobi.\n"
)


class _Entry:
    id = 1
    canonical_path = "TV/Show/S01E01.mkv"
    source = "subgenscan"


def _watcher():
    return CompletionWatcher.__new__(CompletionWatcher)


def _enable(on: bool):
    from subarr.config import settings

    object.__setattr__(settings, "retime_enabled", on)


def test_flag_off_leaves_sidecar_untouched(tmp_path, monkeypatch):
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(False)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    w._run_retime(_Entry())
    assert srt.read_text(encoding="utf-8") == _HOT  # byte-for-byte unchanged


def test_flag_on_retimes_hot_sidecar(tmp_path, monkeypatch):
    from subarr.subtitle_readability import analyze_srt

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    before = analyze_srt(_HOT)
    w._run_retime(_Entry())
    after = analyze_srt(srt.read_text(encoding="utf-8"))
    before_crit = sum(1 for i in before.issues if i.kind == "cps" and i.severity == "critical")
    after_crit = sum(1 for i in after.issues if i.kind == "cps" and i.severity == "critical")
    assert after_crit < before_crit
    assert after.counts.get("overlap", 0) == 0  # no new overlaps


def test_flag_on_comfortable_sidecar_not_rewritten(tmp_path, monkeypatch):
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_CALM, encoding="utf-8")
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    mtime_before = srt.stat().st_mtime_ns
    w._run_retime(_Entry())
    assert srt.stat().st_mtime_ns == mtime_before  # write-only-if-changed → no touch


def test_no_sidecar_is_noop(monkeypatch):
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: None)
    w._run_retime(_Entry())  # must not raise


def test_retime_failure_never_raises(tmp_path, monkeypatch):
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    monkeypatch.setattr(
        "subarr.completion_watcher.retime_srt",
        lambda t: (_ for _ in ()).throw(ValueError("boom")),
    )
    w._run_retime(_Entry())  # best-effort: swallows the error
    assert srt.read_text(encoding="utf-8") == _HOT  # original preserved on failure


@pytest.mark.asyncio
async def test_complete_entry_retimes_before_aftercare_and_upload(monkeypatch):
    w = CompletionWatcher.__new__(CompletionWatcher)
    calls: list[str] = []
    w._provenance = types.SimpleNamespace(mark_completed=lambda i: calls.append("mark"))
    monkeypatch.setattr(w, "_run_retime", lambda e: calls.append("retime"))
    monkeypatch.setattr(w, "_run_aftercare", lambda e: calls.append("aftercare"))

    async def _up(e):
        calls.append("upload")
        return True

    async def _plex(p):
        calls.append("plex")

    monkeypatch.setattr(w, "_try_upload_to_bazarr", _up)
    monkeypatch.setattr(w, "_maybe_plex_partial_scan", _plex)
    entry = types.SimpleNamespace(id=1, canonical_path="TV/x.mkv", series_id=None, source="s")
    await w.complete_entry(entry)
    # re-time must run before aftercare reads the sidecar and before the upload.
    assert calls.index("retime") < calls.index("aftercare")
    assert calls.index("retime") < calls.index("upload")


# ─── P4-S3: explicit parameter forwarding / defaults / env pin / transform ───


def _set_params(**kw):
    from subarr.config import settings

    for k, v in kw.items():
        object.__setattr__(settings, k, v)


def _capture_retime_srt(monkeypatch, box):
    """Patch retime_srt to record the RetimeParams it receives and echo text."""

    def fake(srt_text, params=None):
        box["params"] = params
        return srt_text

    monkeypatch.setattr("subarr.completion_watcher.retime_srt", fake)


def test_exact_defaults_when_settings_unmodified(tmp_path, monkeypatch):
    from subarr.subtitle_retime import RetimeParams

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_CALM, encoding="utf-8")
    _set_params(
        retime_enabled=True,
        target_cps=17.0,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
        max_borrow_ms=500,
    )
    box: dict = {}
    _capture_retime_srt(monkeypatch, box)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    w._run_retime(_Entry())
    assert box["params"] == RetimeParams()  # untouched settings -> stock params


def test_params_forwarded_from_live_settings(tmp_path, monkeypatch):
    from subarr.subtitle_retime import RetimeParams

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_CALM, encoding="utf-8")
    _set_params(
        retime_enabled=True,
        target_cps=25.0,
        min_cue_ms=1200,
        min_gap_ms=150,
        max_cue_ms=9000,
        max_borrow_ms=900,
    )
    box: dict = {}
    _capture_retime_srt(monkeypatch, box)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    w._run_retime(_Entry())
    got = box["params"]
    assert got.target_cps == 25.0
    assert got.min_cue_ms == 1200
    assert got.min_gap_ms == 150
    assert got.max_cue_ms == 9000
    assert got.max_borrow_ms == 900
    assert got == RetimeParams(25.0, 1200, 150, 9000, 900)


def test_disabled_retime_never_invokes_retime_srt(tmp_path, monkeypatch):
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _set_params(retime_enabled=False)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    # if the early-out regressed, this would raise 'boom' instead of returning
    monkeypatch.setattr(
        "subarr.completion_watcher.retime_srt",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retime_srt called while disabled")),
    )
    w._run_retime(_Entry())
    assert srt.read_text(encoding="utf-8") == _HOT  # untouched


def test_env_pinned_retime_off_skips(tmp_path, monkeypatch):
    # SUBARR_RETIME_ENABLED=0 pins retime_enabled off at load; the retimer must
    # treat it identically to a UI-off state (no write, no retime_srt call).
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _set_params(retime_enabled=False)  # reflects an env-pinned config load
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    monkeypatch.setattr(
        "subarr.completion_watcher.retime_srt",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retime_srt called under env pin")),
    )
    w._run_retime(_Entry())
    assert srt.read_text(encoding="utf-8") == _HOT


def test_high_cps_transforms_extend_over_cps_cue(tmp_path, monkeypatch):
    # With a strict target, the over-CPS cue's end time is extended (duration
    # grows) to bring it under the limit.
    from subarr.subtitle_retime import parse_srt

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _set_params(
        retime_enabled=True,
        target_cps=20.0,
        min_cue_ms=1000,
        min_gap_ms=100,
        max_cue_ms=7000,
        max_borrow_ms=500,
    )
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    w._run_retime(_Entry())
    out = parse_srt(srt.read_text(encoding="utf-8"))
    src1, src2 = parse_srt(_HOT)
    assert len(out) == len(parse_srt(_HOT))  # cue count preserved
    assert out[0].end_ms > src1.end_ms  # over-CPS cue extended past 2000ms
    # the 300ms micro-cue is floated up to the min_cue_ms floor
    assert out[1].end_ms - out[1].start_ms >= 1000
    assert out[1].lines == src2.lines  # content untouched
