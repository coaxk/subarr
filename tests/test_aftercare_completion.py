"""#156: aftercare judging fires (best-effort) on job completion."""

from __future__ import annotations


from subarr.aftercare_store import AfterCareStore
from subarr.completion_watcher import CompletionWatcher


def _store(tmp_path):
    from subarr.migrate import run_migrations

    db = tmp_path / "a.db"
    run_migrations(db)
    return AfterCareStore(db)


class _Entry:
    id = 1
    canonical_path = "TV/Show/S01E01.mkv"
    source = "subgenscan"


def _watcher(store, **kw):
    # Construct with only what _run_aftercare needs; other deps unused here.
    w = CompletionWatcher.__new__(CompletionWatcher)
    w._aftercare = store
    return w


def test_run_aftercare_records_result(tmp_path, monkeypatch):
    store = _store(tmp_path)
    srt = tmp_path / "S01E01.en.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n", encoding="utf-8")
    w = _watcher(store)
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: str(srt))
    w._run_aftercare(_Entry())
    rows = store.list_results(view="all", limit=10, offset=0)
    assert len(rows) == 1
    assert rows[0]["canonical_path"] == "TV/Show/S01E01.mkv"


def test_run_aftercare_no_srt_is_noop(tmp_path):
    store = _store(tmp_path)
    w = _watcher(store)
    w._find_srt_sidecar = lambda p: None
    w._run_aftercare(_Entry())  # must not raise
    assert store.list_results(view="all", limit=10, offset=0) == []


def test_run_aftercare_never_raises_on_bad_input(tmp_path, monkeypatch):
    store = _store(tmp_path)
    w = _watcher(store)
    monkeypatch.setattr(w, "_find_srt_sidecar", lambda p: (_ for _ in ()).throw(OSError("boom")))
    w._run_aftercare(_Entry())  # best-effort: swallows the error
    assert store.list_results(view="all", limit=10, offset=0) == []
