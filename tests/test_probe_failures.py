"""Persisted probe failures — the foundation for the coverage probe-gate's
"couldn't analyze" state. A file that fails to probe must be remembered
(survives restart) and distinguishable from never-probed, and a later
successful probe must clear the failure.
"""

from __future__ import annotations


def _store(tmp_path):
    # Mirror the app: migrations own the schema; the store no longer
    # self-creates tables (init_schema removed).
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore

    db = tmp_path / "p.db"
    run_migrations(db)
    return ProbeStore(db)


def test_record_failure_and_failed_paths(tmp_path):
    s = _store(tmp_path)
    s.record_failure("TV/X/a.mkv", "ffprobe timeout")
    s.record_failure("TV/Y/b.mkv", "corrupt container")
    assert s.failed_paths() == {"TV/X/a.mkv", "TV/Y/b.mkv"}


def test_record_failure_upserts_and_counts_attempts(tmp_path):
    s = _store(tmp_path)
    s.record_failure("TV/X/a.mkv", "err1")
    s.record_failure("TV/X/a.mkv", "err2")
    f = s.get_failure("TV/X/a.mkv")
    assert f["attempts"] == 2
    assert f["error"] == "err2"
    assert s.failed_paths() == {"TV/X/a.mkv"}  # one row, not two


def test_successful_probe_clears_failure(tmp_path):
    from subarr.media_probe import ProbeResult

    s = _store(tmp_path)
    s.record_failure("TV/X/a.mkv", "transient")
    assert "TV/X/a.mkv" in s.failed_paths()
    s.upsert(
        canonical_path="TV/X/a.mkv", mtime=1.0, size=100, result=ProbeResult(canonical_path="TV/X/a.mkv")
    )
    assert "TV/X/a.mkv" not in s.failed_paths()  # recovered → no longer failed


# --- #416: eager-probe must surface WHY files errored, not just a count ---


def test_summarize_probe_errors_categorizes_and_samples():
    from subarr.probe_walker import _summarize_probe_errors

    errs = [
        {"path": "TV/A/e1.mkv", "error": "outside media root"},
        {"path": "TV/A/e2.mkv", "error": "outside media root"},
        {"path": "Movies/B.mkv", "error": "stat: [Errno 2] No such file or directory: '/x'"},
    ]
    s = _summarize_probe_errors(errs)
    assert "2 outside-media-root" in s
    assert "1 path-not-found" in s
    assert "TV/A/e1.mkv" in s  # carries a concrete sample so it is diagnosable


def test_summarize_probe_errors_empty():
    from subarr.probe_walker import _summarize_probe_errors

    assert _summarize_probe_errors([]) == ""


def test_log_probe_error_summary_escalates_when_systemic(caplog):
    import logging as _l

    from subarr.probe_walker import WalkState, _log_probe_error_summary

    st = WalkState("w1", "eager")
    st.total_files = 4
    st.errors = [{"path": f"p{i}.mkv", "error": "outside media root"} for i in range(4)]
    with caplog.at_level(_l.WARNING, logger="subarr.probe_walker"):
        _log_probe_error_summary("eager probe", st)
    assert any(
        r.levelno == _l.WARNING and "systemic" in r.getMessage() and "outside-media-root" in r.getMessage()
        for r in caplog.records
    )


def test_log_probe_error_summary_info_when_sparse(caplog):
    import logging as _l

    from subarr.probe_walker import WalkState, _log_probe_error_summary

    st = WalkState("w2", "eager")
    st.total_files = 100
    st.errors = [{"path": "p.mkv", "error": "stat: nope"}]
    with caplog.at_level(_l.INFO, logger="subarr.probe_walker"):
        _log_probe_error_summary("eager probe", st)
    assert any(
        "1 files errored" in r.getMessage() and "path-not-found" in r.getMessage() for r in caplog.records
    )
    assert not any(r.levelno >= _l.WARNING for r in caplog.records)
