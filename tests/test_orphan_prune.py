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


# --- #453 wiring: sweep BOTH stores under ONE safety decision ---------------
# Review is driven by /api/audio-lang/*, so audio_lang_store holds the rows the
# user actually sees stuck. probe_store holds the probe cache, which is
# regenerable but shows the same stale paths in Library Probe.
#
# ONE decision over the UNION, not one per store. Judging separately would let
# a dropped mount refuse one store and allow the other, which is exactly the
# partial prune the guard exists to prevent: it would destroy the expensive
# verifications while looking like it worked.


class _MultiFakeStore:
    # Deliberately NOT named _FakeStore: this module already has one with a
    # different attribute shape (.paths), and redefining the name silently
    # shadowed it and broke the mount-failure test above.
    def __init__(self, paths):
        self._paths = list(paths)
        self.deleted = []

    def all_paths(self):
        return list(self._paths)

    def delete(self, p):
        if p in self._paths:
            self._paths.remove(p)
            self.deleted.append(p)
            return True
        return False


def test_audio_lang_store_exposes_all_paths():
    """The core's docstring claimed audio_lang_store already had this shape.

    It did not -- it has delete() but never had all_paths(), so the documented
    usage would have raised AttributeError the moment it was wired up. Pinning
    it so the claim and the code cannot drift apart again.
    """
    from subarr.audio_lang_store import AudioLangStore

    assert hasattr(AudioLangStore, "all_paths")
    assert hasattr(AudioLangStore, "delete")


def test_union_is_judged_once_not_per_store():
    from subarr.orphan_prune import prune_missing_multi

    # 4 of 6 distinct paths missing = 67%, over the 50% bar. Per-store this
    # would be 1/3 (safe) and 3/3 (unsafe); as a union it is one refusal.
    a = _MultiFakeStore(["ok1.mkv", "ok2.mkv", "gone1.mkv"])
    b = _MultiFakeStore(["gone2.mkv", "gone3.mkv", "gone4.mkv"])
    rep = prune_missing_multi({"a": a, "b": b}, exists=lambda p: not p.startswith("gone"))

    assert rep.decision.safe is False
    assert a.deleted == [] and b.deleted == []


def test_a_shared_path_counts_once():
    from subarr.orphan_prune import prune_missing_multi

    # Both stores key on canonical_path, so the same file appears in both.
    # Counting it twice would skew the ratio and could tip a safe prune unsafe.
    a = _MultiFakeStore(["same.mkv", "ok.mkv"])
    b = _MultiFakeStore(["same.mkv"])
    rep = prune_missing_multi({"a": a, "b": b}, exists=lambda p: True, dry_run=True)
    assert rep.decision.would_delete == 0
    assert rep.total_paths == 2  # not 3


def test_safe_sweep_deletes_from_every_store():
    from subarr.orphan_prune import prune_missing_multi

    a = _MultiFakeStore(["ok1.mkv", "ok2.mkv", "ok3.mkv", "gone.mkv"])
    b = _MultiFakeStore(["ok1.mkv", "gone.mkv"])
    rep = prune_missing_multi({"a": a, "b": b}, exists=lambda p: p != "gone.mkv")

    assert rep.decision.safe is True
    assert a.deleted == ["gone.mkv"]
    assert b.deleted == ["gone.mkv"]
    assert rep.deleted_by_store == {"a": 1, "b": 1}


def test_dry_run_deletes_nothing_but_reports_what_would_go():
    from subarr.orphan_prune import prune_missing_multi

    a = _MultiFakeStore(["ok1.mkv", "ok2.mkv", "ok3.mkv", "gone.mkv"])
    b = _MultiFakeStore(["gone.mkv"])
    rep = prune_missing_multi({"a": a, "b": b}, exists=lambda p: p != "gone.mkv", dry_run=True)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 1
    assert rep.missing == ["gone.mkv"]
    assert a.deleted == [] and b.deleted == []
    assert rep.deleted_by_store == {}


def test_a_store_that_is_empty_does_not_break_the_sweep():
    from subarr.orphan_prune import prune_missing_multi

    a = _MultiFakeStore(["ok1.mkv", "ok2.mkv", "ok3.mkv", "gone.mkv"])
    b = _MultiFakeStore([])
    rep = prune_missing_multi({"a": a, "b": b}, exists=lambda p: p != "gone.mkv")
    assert rep.decision.safe is True
    assert a.deleted == ["gone.mkv"]


def test_everything_present_is_a_no_op_not_a_refusal():
    from subarr.orphan_prune import prune_missing_multi

    a = _MultiFakeStore(["ok1.mkv", "ok2.mkv"])
    rep = prune_missing_multi({"a": a}, exists=lambda p: True)
    assert rep.decision.safe is True
    assert rep.decision.would_delete == 0
    assert a.deleted == []
