"""#638: protected and cache-only rows are judged independently.

Protected rows (hand-built verifications) keep the ratio test. Cache-only rows
(probe cache) have no ratio: a missing one is prunable when its scope shows
enough readable files, else held. Each class has its own storage-failure test.
"""

from __future__ import annotations

import pytest

from subarr.orphan_prune import (
    HeldScope,
    prune_decision,
    prune_missing_multi,
)


class _MultiFakeStore:
    # Own copy: importing it from tests/test_orphan_prune.py would couple this
    # module to that one's helpers.
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


def _scope(path):
    """Same rule as paths.prune_scope, written inline on purpose."""
    s = path.strip()
    if s.startswith("@"):
        head, _, rest = s[1:].partition("/")
        slug, rel = head, rest.strip("/")
    else:
        slug, rel = "", s.strip("/")
    parts = rel.split("/") if rel else []
    return slug, parts[0] if len(parts) >= 2 else ""


def _run(a_paths, b_paths, states, *, folder_gone=None, dry_run=False):
    a = _MultiFakeStore(a_paths)
    b = _MultiFakeStore(b_paths)
    rep = prune_missing_multi(
        {"a": a, "b": b},
        state=lambda path: states[path],
        dry_run=dry_run,
        regenerable=frozenset({"b"}),
        scope_of=_scope,
        folder_gone=folder_gone or (lambda library, folder: False),
    )
    return a, b, rep


def _paths(prefix, n):
    return [f"{prefix}{i}.mkv" for i in range(n)]


def test_reporter_shape_prunes_cache_only_rows_in_a_live_scope():
    present = _paths("TV/p", 3)
    missing = _paths("TV/m", 10)
    states = {p: "present" for p in present} | {p: "missing" for p in missing}

    _a, b, rep = _run([], present + missing, states)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 10
    assert b.deleted == missing
    assert rep.held == []


def test_protected_present_rows_also_witness_the_scope():
    present = _paths("TV/p", 3)
    missing = _paths("TV/m", 10)
    protected = _paths("TV/a", 2)
    states = {p: "present" for p in present + protected} | {p: "missing" for p in missing}

    a, b, rep = _run(protected, present + missing, states)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 10
    assert a.deleted == []
    assert b.deleted == missing
    assert rep.held == []


def test_share_down_cache_only_holds_every_row():
    missing = _paths("TV/m", 5)
    states = {p: "missing" for p in missing}

    _a, b, rep = _run([], missing, states)

    assert rep.decision.safe is False
    assert rep.held == missing
    assert b.deleted == []
    assert rep.held_scopes == [HeldScope("", "TV", 5, 0)]
    assert "on hold" in rep.decision.reason


def test_share_down_with_protected_rows_holds_both_classes():
    protected = _paths("TV/Show/a", 2)
    cached = _paths("TV/Show/b", 3)
    states = {p: "missing" for p in protected + cached}

    a, b, rep = _run(protected, cached, states)

    assert rep.decision.safe is False
    assert a.deleted == [] and b.deleted == []
    assert rep.held_protected == 2
    assert rep.held == protected + cached
    assert "storage mount" in rep.decision.reason
    assert "on hold" in rep.decision.reason


def test_only_dead_scopes_are_held():
    present = _paths("Movies/p", 3)
    movies_missing = _paths("Movies/m", 2)
    tv_missing = _paths("TV/m", 4)
    states = {p: "present" for p in present}
    states |= {p: "missing" for p in movies_missing + tv_missing}

    _a, b, rep = _run([], present + movies_missing + tv_missing, states)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 2
    assert rep.missing == movies_missing
    assert rep.held == tv_missing
    assert rep.held_scopes == [HeldScope("", "TV", 4, 0)]
    assert "TV" in rep.decision.reason
    assert b.deleted == movies_missing


@pytest.mark.parametrize("present_count,prunable", [(2, False), (3, True)])
def test_witness_boundary_is_three(present_count, prunable):
    present = _paths("TV/p", present_count)
    missing = ["TV/m.mkv"]
    states = {p: "present" for p in present} | {"TV/m.mkv": "missing"}

    _a, b, rep = _run([], present + missing, states)

    if prunable:
        assert rep.missing == missing
        assert rep.held == []
        assert b.deleted == missing
    else:
        assert rep.missing == missing
        assert rep.held == missing
        assert b.deleted == []


def test_protected_rows_count_as_witnesses():
    present = _paths("TV/p", 3)
    missing = ["TV/m.mkv"]
    states = {p: "present" for p in present} | {"TV/m.mkv": "missing"}

    _a, b, rep = _run(present, missing, states)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 1
    assert rep.held == []
    assert b.deleted == missing


def test_unknown_is_not_a_witness():
    unknown = _paths("TV/u", 3)
    missing = ["TV/m.mkv"]
    states = {p: "unknown" for p in unknown} | {"TV/m.mkv": "missing"}

    _a, _b, rep = _run(unknown, missing, states)

    assert rep.decision.safe is False
    assert rep.held == missing
    assert rep.held_scopes == [HeldScope("", "TV", 1, 0)]


def test_unknown_is_never_missing_or_held_or_deleted():
    present = _paths("TV/p", 3)
    states = {p: "present" for p in present}
    states |= {"TV/unknown.mkv": "unknown", "TV/m.mkv": "missing"}

    a, b, rep = _run(["TV/unknown.mkv"], present + ["TV/m.mkv"], states)

    assert rep.decision.safe is True
    assert "TV/unknown.mkv" not in rep.missing
    assert "TV/unknown.mkv" not in rep.held
    assert a.deleted == []
    assert b.deleted == ["TV/m.mkv"]


def test_folder_renamed_away_is_live_through_its_library():
    present = _paths("Movies/p", 3)
    missing = _paths("Old/m", 3)
    states = {p: "present" for p in present} | {p: "missing" for p in missing}

    _a, b, rep = _run(
        [],
        present + missing,
        states,
        folder_gone=lambda library, folder: (library, folder) == ("", "Old"),
    )

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 3
    assert rep.held == []
    assert b.deleted == missing


def test_folder_gone_without_library_witnesses_stays_dead():
    present = _paths("Movies/p", 2)
    missing = _paths("Old/m", 3)
    states = {p: "present" for p in present} | {p: "missing" for p in missing}

    _a, b, rep = _run(
        [],
        present + missing,
        states,
        folder_gone=lambda library, folder: (library, folder) == ("", "Old"),
    )

    assert rep.decision.safe is False
    assert rep.held == missing
    assert b.deleted == []


def test_folder_present_is_dead_even_with_library_witnesses():
    present = _paths("Movies/p", 10)
    missing = _paths("Dead/m", 3)
    states = {p: "present" for p in present} | {p: "missing" for p in missing}

    _a, b, rep = _run([], present + missing, states, folder_gone=lambda library, folder: False)

    assert rep.decision.safe is False
    assert rep.held == missing
    assert b.deleted == []


def test_folder_gone_is_only_asked_for_a_witnessless_folder_scope():
    calls = []
    paths = _paths("TV/p", 3) + ["TV/m.mkv", "root.mkv"] + _paths("Old/m", 3)
    states = {p: "present" for p in _paths("TV/p", 3)}
    states |= {p: "missing" for p in ["TV/m.mkv", "root.mkv"] + _paths("Old/m", 3)}

    def folder_gone(library, folder):
        calls.append((library, folder))
        return True

    _a, _b, _rep = _run([], paths, states, folder_gone=folder_gone)

    assert calls == [("", "Old")]


def test_protected_denominator_is_protected_paths_only():
    protected_present = _paths("TV/A/a", 3)
    protected_missing = ["TV/A/gone.mkv"]
    present = _paths("TV/B/p", 3)
    missing = _paths("TV/B/m", 10)
    states = {p: "present" for p in protected_present + present}
    states |= {p: "missing" for p in protected_missing + missing}

    a, b, rep = _run(protected_present + protected_missing, present + missing, states)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 11
    assert len(rep.missing) == 11
    assert a.deleted == protected_missing
    assert b.deleted == missing
    # The old union denominator would have refused this entirely.
    assert prune_decision(total=17, missing=11).safe is False


def test_classes_are_judged_independently():
    protected_missing = _paths("TV/Show/a", 2)
    present = _paths("TV/Show/p", 3)
    cached_missing = _paths("TV/Show/m", 5)
    states = {p: "present" for p in present}
    states |= {p: "missing" for p in protected_missing + cached_missing}

    a, b, rep = _run(protected_missing, present + cached_missing, states)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 5
    assert rep.missing == cached_missing
    assert rep.held_protected == 2
    assert rep.held == protected_missing
    assert a.deleted == []
    assert b.deleted == cached_missing
    assert "storage mount" in rep.decision.reason


def test_a_path_in_both_stores_is_protected():
    shared = "TV/Show/same.mkv"
    protected_present = _paths("TV/Show/a", 3)
    states = {p: "present" for p in protected_present} | {shared: "missing"}

    a, b, rep = _run(protected_present + [shared], [shared], states)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 1
    assert rep.held == []
    assert a.deleted == [shared]
    assert b.deleted == [shared]


def test_regenerable_empty_keeps_the_union_ratio():
    a = _MultiFakeStore(["ok1.mkv", "ok2.mkv", "gone1.mkv"])
    b = _MultiFakeStore(["gone2.mkv", "gone3.mkv", "gone4.mkv"])

    rep = prune_missing_multi({"a": a, "b": b}, exists=lambda p: not p.startswith("gone"))

    assert rep.decision.safe is False
    assert rep.held_protected == 4
    assert a.deleted == [] and b.deleted == []


def test_state_and_exists_are_mutually_required():
    store = _MultiFakeStore([])

    with pytest.raises(TypeError):
        prune_missing_multi({"a": store})
    with pytest.raises(ValueError):
        prune_missing_multi({"a": store}, exists=lambda p: True, state=lambda p: "present")


def test_state_is_called_once_per_distinct_path():
    a = _MultiFakeStore(["same.mkv", "a.mkv"])
    b = _MultiFakeStore(["same.mkv", "b.mkv"])
    calls = []

    def state(path):
        calls.append(path)
        return "present"

    prune_missing_multi({"a": a, "b": b}, state=state, regenerable=frozenset({"b"}))

    assert sorted(calls) == ["a.mkv", "b.mkv", "same.mkv"]
    assert len(calls) == 3


def test_dry_run_reports_both_classes_without_deleting():
    present = _paths("TV/p", 3)
    missing = ["TV/m.mkv"]
    held = _paths("Old/m", 3)
    states = {p: "present" for p in present}
    states |= {p: "missing" for p in missing + held}

    _a, b, rep = _run([], present + missing + held, states, dry_run=True)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 1
    assert rep.missing == missing
    assert rep.held == held
    assert rep.deleted_by_store == {}
    assert b.deleted == []


def test_held_text_uses_each_scope_name_form():
    paths = ["@disk2/TV/m.mkv", "@disk2/m.mkv", "TV/m.mkv", "m.mkv"]
    states = {p: "missing" for p in paths}

    _a, _b, rep = _run([], paths, states)

    assert "4 cached probe rows are on hold" in rep.decision.reason
    assert "the default library" in rep.decision.reason
    assert "TV" in rep.decision.reason
    assert "@disk2" in rep.decision.reason
    assert "@disk2/TV" in rep.decision.reason


def test_held_text_uses_the_singular_for_one_row():
    states = {"TV/m.mkv": "missing"}

    _a, _b, rep = _run([], ["TV/m.mkv"], states)

    assert "1 cached probe row is on hold" in rep.decision.reason


@pytest.mark.parametrize("scope_count,extra", [(5, None), (6, "and 1 more")])
def test_held_text_names_at_most_five_scopes(scope_count, extra):
    paths = [f"F{i}/m.mkv" for i in range(scope_count)]
    states = {p: "missing" for p in paths}

    _a, _b, rep = _run([], paths, states)

    assert rep.decision.safe is False
    if extra is None:
        assert "more" not in rep.decision.reason
    else:
        assert extra in rep.decision.reason


def test_held_scopes_order_by_missing_then_library_then_folder():
    paths = _paths("A/m", 2) + _paths("B/m", 2) + _paths("C/m", 1) + _paths("@a/A/m", 3) + _paths("@a/Z/m", 3)
    states = {p: "missing" for p in paths}

    _a, _b, rep = _run([], paths, states)

    assert rep.held_scopes == [
        HeldScope("a", "A", 3, 0),
        HeldScope("a", "Z", 3, 0),
        HeldScope("", "A", 2, 0),
        HeldScope("", "B", 2, 0),
        HeldScope("", "C", 1, 0),
    ]


def test_protected_ratio_boundary_allows_exactly_half():
    allowed = ["TV/A/p0.mkv", "TV/A/p1.mkv", "TV/A/m0.mkv", "TV/A/m1.mkv"]
    states = {p: "present" for p in allowed[:2]} | {p: "missing" for p in allowed[2:]}

    a, _b, rep = _run(allowed, [], states)

    assert rep.decision.safe is True
    assert rep.decision.would_delete == 2
    assert a.deleted == allowed[2:]


def test_protected_ratio_above_half_holds():
    paths = ["TV/A/p0.mkv", "TV/A/m0.mkv", "TV/A/m1.mkv", "TV/A/m2.mkv"]
    states = {"TV/A/p0.mkv": "present"}
    states |= {p: "missing" for p in paths[1:]}

    a, _b, rep = _run(paths, [], states)

    assert rep.decision.safe is False
    assert rep.held_protected == 3
    assert rep.held == paths[1:]
    assert a.deleted == []


def test_a_raising_state_function_deletes_nothing():
    present = _paths("TV/p", 3)
    paths = present + ["TV/m.mkv", "TV/boom.mkv"]
    states = {p: "present" for p in present} | {"TV/m.mkv": "missing"}

    def state(path):
        if path == "TV/boom.mkv":
            raise RuntimeError("state failed")
        return states[path]

    a = _MultiFakeStore([])
    b = _MultiFakeStore(paths)

    with pytest.raises(RuntimeError):
        prune_missing_multi({"a": a, "b": b}, state=state, regenerable=frozenset({"b"}), scope_of=_scope)

    assert a.deleted == [] and b.deleted == []
