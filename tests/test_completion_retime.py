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
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p, since=None: str(srt))
    w._run_retime(_Entry())
    assert srt.read_text(encoding="utf-8") == _HOT  # byte-for-byte unchanged


def test_flag_on_retimes_hot_sidecar(tmp_path, monkeypatch):
    from subarr.subtitle_readability import analyze_srt

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p, since=None: str(srt))
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
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p, since=None: str(srt))
    mtime_before = srt.stat().st_mtime_ns
    w._run_retime(_Entry())
    assert srt.stat().st_mtime_ns == mtime_before  # write-only-if-changed → no touch


def test_no_sidecar_is_noop(monkeypatch):
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p, since=None: None)
    w._run_retime(_Entry())  # must not raise


def test_retime_failure_never_raises(tmp_path, monkeypatch):
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p, since=None: str(srt))
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
    # #545: completion now records the outcome too, so the fake takes it.
    w._provenance = types.SimpleNamespace(mark_completed=lambda i, **kw: calls.append("mark"))
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


# #613 follow-up: never re-time a subtitle that predates the job. When subgen
# writes nothing, the sidecar picker falls back to whatever is beside the video
# (an English subtitle Bazarr downloaded last week, say), and re-timing would
# rewrite a file subgen never produced. Completion still records the job by
# existence alone (#545), so this guard is limited to the in-place rewrite.


class _QueuedEntry(_Entry):
    def __init__(self, queued_at):
        self.queued_at = queued_at


def _age(path, seconds):
    import os
    import time

    t = time.time() - seconds
    os.utime(path, (t, t))


def test_a_sidecar_older_than_the_job_is_not_retimed(tmp_path, monkeypatch):
    import time

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _age(srt, 7 * 86400)
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p, since=None: str(srt))
    w._run_retime(_QueuedEntry(queued_at=time.time() - 3600))
    assert srt.read_text(encoding="utf-8") == _HOT


def test_a_sidecar_written_since_the_job_is_retimed(tmp_path, monkeypatch):
    import time

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p, since=None: str(srt))
    w._run_retime(_QueuedEntry(queued_at=time.time() - 3600))
    assert srt.read_text(encoding="utf-8") != _HOT


def test_within_the_clock_skew_slack_still_retimes(tmp_path, monkeypatch):
    # The share's clock may run behind subarr's; a file a minute "older" than
    # the job is still this job's output.
    import time

    srt = tmp_path / "S01E01.en.srt"
    srt.write_text(_HOT, encoding="utf-8")
    now = time.time()
    _age(srt, 60)
    _enable(True)
    w = _watcher()
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p, since=None: str(srt))
    w._run_retime(_QueuedEntry(queued_at=now))
    assert srt.read_text(encoding="utf-8") != _HOT
