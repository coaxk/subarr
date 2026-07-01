"""#359: _run_retime re-times the finished .srt in place, off by default,
best-effort (never blocks completion)."""

from __future__ import annotations

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
