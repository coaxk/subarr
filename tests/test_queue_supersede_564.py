"""#564: Queue Issues judge each file by its LATEST attempt.

A failed or skipped attempt used to stay in "Issues — silent fails" for the whole
24 h window, even after the file was retried and transcribed. The history view
now drops an older failure when the same file has a newer attempt, or when a
retry is waiting in subarr's pending queue or running in subgen.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from subarr.scan_store import (
    PATH_STATUS_ERROR,
    PATH_STATUS_OK,
    PATH_STATUS_ORPHANED,
    PATH_STATUS_SKIPPED,
)

EP = "TV/Flics/Season 2/Flics - S02E03 - Coup d'arret.mkv"
OTHER = "TV/Flics/Season 2/Flics - S02E04 - Mourir libre.mkv"


def _scan(scan_id, created_at, path, status, error=None):
    r = SimpleNamespace(
        path=path,
        status=status,
        subgen_body={"walked": 1, "queued": 1 if status == PATH_STATUS_OK else 0},
        error=error,
        started_at=created_at,
        finished_at=created_at,
        subgen_status_code=200,
    )
    return SimpleNamespace(id=scan_id, created_at=created_at, status="done", results=[r])


def _issues(history):
    """Mirror of queue.jsx's Issues filter."""
    benign = {"sub_exists", "file_removed", "interrupted"}
    out = []
    for h in history:
        o = h["outcome"]
        if o["category"] == "error" or (o["category"] == "skipped" and o.get("skip_reason") not in benign):
            out.append(h)
    return out


@pytest.fixture
def build(subarr_env):
    from subarr.routers.queue import _build_history_view

    return _build_history_view


@pytest.mark.parametrize("old_status", [PATH_STATUS_SKIPPED, PATH_STATUS_ERROR, PATH_STATUS_ORPHANED])
def test_newer_success_clears_older_failure(build, old_status):
    scans = [
        _scan("new", 200.0, EP, PATH_STATUS_OK),
        _scan("old", 100.0, EP, old_status, error="boom"),
    ]
    history, counts = build(scans, set(), {EP})
    assert [h["scan_id"] for h in history] == ["new"]
    assert history[0]["outcome"]["label"] == "completed"
    assert history[0]["retries"] == 1
    assert counts["ok"] == 1
    assert counts["skipped"] == counts["error"] == counts["orphaned"] == 0


def test_order_of_input_does_not_matter(build):
    scans = [
        _scan("old", 100.0, EP, PATH_STATUS_SKIPPED),
        _scan("new", 200.0, EP, PATH_STATUS_OK),
    ]
    history, _ = build(scans, set(), set())
    assert [h["scan_id"] for h in history] == ["new"]


def test_a_file_that_fails_twice_shows_once(build):
    scans = [
        _scan("second", 200.0, EP, PATH_STATUS_SKIPPED),
        _scan("first", 100.0, EP, PATH_STATUS_SKIPPED),
    ]
    history, counts = build(scans, set(), set())
    issues = _issues(history)
    assert [h["scan_id"] for h in issues] == ["second"]
    assert issues[0]["retries"] == 1
    assert counts["skipped"] == 1


def test_failure_hidden_while_retry_is_pending_in_subarr(build):
    scans = [_scan("old", 100.0, EP, PATH_STATUS_SKIPPED), _scan("x", 90.0, OTHER, PATH_STATUS_SKIPPED)]
    # active_paths maps path -> when the waiting job was queued
    history, counts = build(scans, set(), set(), active_paths={EP: 150.0})
    assert [h["path"] for h in history] == [OTHER]
    assert counts["skipped"] == 1


def test_a_retrys_own_failure_is_not_hidden_by_its_own_job(build):
    """The feeder writes the scan row AFTER the job was queued, and a submitted
    job can linger up to the orphan grace. That job is the attempt that failed,
    not a newer retry, so its failure must still show."""
    scans = [_scan("retry", 200.0, EP, PATH_STATUS_ERROR, error="boom")]
    history, _ = build(scans, set(), set(), active_paths={EP: 190.0})
    assert [h["scan_id"] for h in _issues(history)] == ["retry"]


def test_failure_hidden_while_retry_is_live_in_subgen(build):
    scans = [_scan("old", 100.0, EP, PATH_STATUS_ERROR, error="boom")]
    history, _ = build(scans, {EP.rsplit("/", 1)[-1]}, set())
    assert history == []


def test_other_files_are_untouched(build):
    scans = [
        _scan("new", 200.0, EP, PATH_STATUS_OK),
        _scan("old", 100.0, EP, PATH_STATUS_SKIPPED),
        _scan("sib", 100.0, OTHER, PATH_STATUS_SKIPPED),
    ]
    history, _ = build(scans, set(), set())
    assert [h["path"] for h in _issues(history)] == [OTHER]
    assert "retries" not in next(h for h in history if h["path"] == OTHER)


def test_older_success_is_kept(build):
    """Only failures are superseded; an earlier completed run stays in history."""
    scans = [
        _scan("new", 200.0, EP, PATH_STATUS_SKIPPED),
        _scan("old", 100.0, EP, PATH_STATUS_OK),
    ]
    history, _ = build(scans, set(), {EP})
    assert [h["scan_id"] for h in history] == ["new", "old"]
    assert "retries" not in history[0]


def test_a_lone_failure_still_shows(build):
    history, counts = build([_scan("only", 100.0, EP, PATH_STATUS_SKIPPED)], set(), set())
    assert [h["scan_id"] for h in _issues(history)] == ["only"]
    assert "retries" not in history[0]
    assert counts["skipped"] == 1


def test_requeue_invalidates_the_history_cache(app_with_stub, monkeypatch):
    """The page refetches right after a requeue; a 5 s stale view would still
    show the old failure, so requeue forces the next GET to rebuild."""
    import subarr.routers.queue as q

    calls = {"n": 0}
    orig = q._build_history_view

    def counting(*args, **kwargs):
        calls["n"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(q, "_build_history_view", counting)
    assert app_with_stub.get("/api/queue").status_code == 200
    # media_root (conftest) ships TV/Show/ep.mkv
    r = app_with_stub.post("/api/queue/requeue", json={"path": "TV/Show/ep.mkv"})
    assert r.status_code == 202, r.text
    assert app_with_stub.get("/api/queue").status_code == 200
    assert calls["n"] == 2


def test_store_active_queued_at_reports_newest_active_job(tmp_path):
    from subarr.migrate import run_migrations
    from subarr.pending_queue import STATUS_DONE, PendingQueueStore

    db = tmp_path / "pq.db"
    run_migrations(db)
    store = PendingQueueStore(db)
    try:
        a = store.enqueue(EP, source="manual")
        b = store.enqueue(OTHER, source="manual")
        store.set_status(b.id, STATUS_DONE)
        got = store.active_queued_at()
        assert set(got) == {EP}
        assert got[EP] == pytest.approx(a.created_at)
    finally:
        store.close()


def test_bulk_requeue_clears_the_old_failure_end_to_end(app_with_stub, media_root):
    """The reported case: a skipped row, requeued WITHOUT deleting its scan (the
    bulk button), must leave Issues on the very next GET."""
    import time

    from subarr.app import app

    (media_root / "TV" / "Show" / "nosub.mkv").write_bytes(b"")
    path = "TV/Show/nosub.mkv"
    store = app.state.scans
    scan = store.create([path], reverse=False)
    scan.created_at = time.time() - 60
    scan.status = "done"
    scan.results[0].status = PATH_STATUS_SKIPPED
    scan.results[0].error = "subgen skipped 1"
    store.save(scan)

    before = app_with_stub.get("/api/queue").json()["history"]
    assert [h["path"] for h in _issues(before)] == [path]

    r = app_with_stub.post("/api/queue/requeue", json={"path": path})
    assert r.status_code == 202, r.text
    after = app_with_stub.get("/api/queue").json()["history"]
    assert _issues(after) == []


def test_delete_invalidates_the_history_cache(app_with_stub, monkeypatch):
    import subarr.routers.queue as q
    from subarr.app import app

    scan = app.state.scans.create(["TV/Show/ep.mkv"], reverse=False)
    calls = {"n": 0}
    orig = q._build_history_view

    def counting(*args, **kwargs):
        calls["n"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(q, "_build_history_view", counting)
    assert app_with_stub.get("/api/queue").status_code == 200
    assert app_with_stub.delete(f"/api/queue/scan/{scan.id}").status_code == 200
    assert app_with_stub.get("/api/queue").status_code == 200
    assert calls["n"] == 2


def test_failure_leaves_issues_while_the_retry_waits_in_pending(app_with_stub, media_root, monkeypatch):
    """Same as above with the feeder held, so the retry sits in subarr's pending
    queue and no newer scan row exists: only the pending-queue signal can
    clear the old row."""
    from subarr.app import app

    monkeypatch.setattr(app.state.queue_feeder, "kick", lambda *a, **k: None)
    (media_root / "TV" / "Show" / "waiting.mkv").write_bytes(b"")
    path = "TV/Show/waiting.mkv"
    store = app.state.scans
    scan = store.create([path], reverse=False)
    scan.status = "done"
    scan.results[0].status = PATH_STATUS_SKIPPED
    store.save(scan)
    assert [h["path"] for h in _issues(app_with_stub.get("/api/queue").json()["history"])] == [path]

    assert app_with_stub.post("/api/queue/requeue", json={"path": path}).status_code == 202
    assert path in app.state.pending_queue.active_queued_at()
    assert app.state.scans.list_recent()[0].id == scan.id  # no newer attempt was written
    assert _issues(app_with_stub.get("/api/queue").json()["history"]) == []
