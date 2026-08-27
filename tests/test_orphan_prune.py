"""#453: renamed/deleted files leave orphan rows wedged in Review forever.

The obvious fix -- delete every DB row whose path is missing -- is a data-loss
landmine here. When a network share drops, the mountpoint often still LISTS
directories while serving nothing, so every path looks missing at once and a
naive prune wipes every verification the user ever confirmed. These tests pin
the guard that stops that, not just the happy path.
"""

from subarr.orphan_prune import PruneDecision, partition_missing, prune_decision


def test_partition_splits_present_from_missing():
    present, missing = partition_missing(
        ["/m/a.mkv", "/m/gone.mkv", "/m/b.mkv"],
        exists=lambda p: "gone" not in p,
    )
    assert present == ["/m/a.mkv", "/m/b.mkv"]
    assert missing == ["/m/gone.mkv"]


def test_a_few_renames_are_safe_to_prune():
    d = prune_decision(total=100, missing=3)
    assert isinstance(d, PruneDecision)
    assert d.safe is True
    assert "3" in d.reason


def test_everything_missing_is_refused_as_a_mount_failure():
    # The whole point. 100 of 100 gone is not 100 renames.
    d = prune_decision(total=100, missing=100)
    assert d.safe is False
    assert "mount" in d.reason.lower() or "unavailable" in d.reason.lower()


def test_majority_missing_is_refused():
    assert prune_decision(total=100, missing=60).safe is False


def test_boundary_is_explicit_not_accidental():
    # 50% exactly is allowed; above it is not. Stated, so a future edit that
    # flips the comparison fails here rather than silently widening the blast
    # radius.
    assert prune_decision(total=100, missing=50).safe is True
    assert prune_decision(total=100, missing=51).safe is False


def test_nothing_missing_is_a_no_op_not_an_error():
    d = prune_decision(total=100, missing=0)
    assert d.safe is True
    assert d.would_delete == 0


def test_empty_store_never_prunes():
    # No rows means nothing to judge; refuse rather than divide by zero.
    d = prune_decision(total=0, missing=0)
    assert d.safe is False


def test_decision_reports_what_it_would_delete():
    d = prune_decision(total=200, missing=7)
    assert d.would_delete == 7


# ── the service layer, exercised against fake stores ───────────────────────


class _FakeStore:
    def __init__(self, paths):
        self.paths = list(paths)
        self.deleted = []

    def all_paths(self):
        return list(self.paths)

    def delete(self, p):
        self.deleted.append(p)
        if p in self.paths:
            self.paths.remove(p)
            return True
        return False


def test_prune_deletes_only_the_missing_rows():
    from subarr.orphan_prune import prune_missing

    store = _FakeStore(["/m/a.mkv", "/m/gone.mkv", "/m/b.mkv"])
    rep = prune_missing(store, exists=lambda p: "gone" not in p)
    assert rep.decision.safe is True
    assert store.deleted == ["/m/gone.mkv"]
    assert rep.deleted == 1


def test_prune_deletes_NOTHING_when_the_mount_looks_down():
    # The landmine test. Everything missing must delete zero rows, not all.
    from subarr.orphan_prune import prune_missing

    store = _FakeStore([f"/m/{i}.mkv" for i in range(50)])
    rep = prune_missing(store, exists=lambda p: False)
    assert rep.decision.safe is False
    assert store.deleted == []
    assert rep.deleted == 0
    assert len(store.paths) == 50, "not one row may be removed"


def test_prune_is_a_no_op_when_everything_is_present():
    from subarr.orphan_prune import prune_missing

    store = _FakeStore(["/m/a.mkv", "/m/b.mkv"])
    rep = prune_missing(store, exists=lambda p: True)
    assert rep.deleted == 0
    assert store.deleted == []


def test_prune_dry_run_reports_without_deleting():
    from subarr.orphan_prune import prune_missing

    store = _FakeStore(["/m/a.mkv", "/m/gone.mkv"])
    rep = prune_missing(store, exists=lambda p: "gone" not in p, dry_run=True)
    assert rep.decision.would_delete == 1
    assert rep.deleted == 0
    assert store.deleted == []


def test_probe_store_delete_actually_removes_the_row(tmp_path):
    """#453 needs ProbeStore.delete to exist and hit the right table.

    Worth a real store rather than a fake: the first draft of delete targeted a
    table named `probe_results`, which does not exist -- the real one is
    `media_probe`. A fake store would have happily passed.
    """
    from subarr.media_probe import ProbeResult
    from subarr.migrate import run_migrations
    from subarr.probe_store import ProbeStore

    db = tmp_path / "t.db"
    run_migrations(db)
    st = ProbeStore(db)
    for p in ("/m/a.mkv", "/m/b.mkv"):
        st.upsert(canonical_path=p, mtime=1.0, size=10, result=ProbeResult(canonical_path=p))

    assert sorted(st.all_paths()) == ["/m/a.mkv", "/m/b.mkv"]
    assert st.delete("/m/a.mkv") is True
    assert st.delete("/m/nope.mkv") is False, "deleting an absent row reports False"
    assert st.all_paths() == ["/m/b.mkv"]


def test_prune_missing_against_a_real_probe_store(tmp_path):
    from subarr.media_probe import ProbeResult
    from subarr.migrate import run_migrations
    from subarr.orphan_prune import prune_missing
    from subarr.probe_store import ProbeStore

    db = tmp_path / "t.db"
    run_migrations(db)
    st = ProbeStore(db)
    for p in ("/m/keep.mkv", "/m/renamed.mkv"):
        st.upsert(canonical_path=p, mtime=1.0, size=10, result=ProbeResult(canonical_path=p))

    rep = prune_missing(st, exists=lambda p: "renamed" not in p)
    assert rep.decision.safe is True
    assert rep.deleted == 1
    assert st.all_paths() == ["/m/keep.mkv"]
