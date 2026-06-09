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
