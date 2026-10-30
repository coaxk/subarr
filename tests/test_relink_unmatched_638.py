"""#638 follow-up: `analyse` says why each stale path is not in an offered rule.

Every distinct stale path lands under exactly one reason, the first that
applies in pipeline order, so a remote report can tell this bug from any other
gate without anyone reading the database.
"""

from __future__ import annotations

import time

from subarr.relink import RULE_MIN_VERIFIED, Analysis, analyse, build_rules

REASONS = (
    "failed_library",
    "too_short",
    "no_candidate",
    "needs_size",
    "size_changed",
    "ambiguous",
    "contested",
    "collision",
    "path_only_withheld",
    "below_threshold",
    "offered",
)


def _an(stale, inv, *, sizes=None, verified=None, probed=None, failed=frozenset()):
    stale = list(stale)
    return analyse(
        stale,
        old_sizes=sizes if sizes is not None else {},
        verified=set(stale) if verified is None else verified,
        probed=probed if probed is not None else set(),
        inventory=inv,
        failed_slugs=set(failed),
    )


def _only(a: Analysis, reason: str) -> None:
    """Every key is present and only `reason` is non-zero."""
    assert set(a.reasons) == set(REASONS)
    assert {k: v for k, v in a.reasons.items() if v} == {reason: a.reasons[reason]}
    assert a.reasons[reason] > 0


def test_every_key_is_present_for_empty_input():
    a = _an([], {})
    assert a.rules == [] and a.reasons == dict.fromkeys(REASONS, 0) and a.examples == {}


def test_build_rules_is_the_rules_of_analyse():
    stale = [f"Old/S{i}/E/s{i}.mkv" for i in range(3)]
    inv = {f"S{i}/E/s{i}.mkv": 1 for i in range(3)}
    sizes = {s: 1 for s in stale}
    kw = {
        "old_sizes": sizes,
        "verified": set(stale),
        "probed": set(),
        "inventory": inv,
        "failed_slugs": set(),
    }
    assert build_rules(stale, **kw) == analyse(stale, **kw).rules


def test_failed_library():
    a = _an(["@gone/A/B/c.mkv"], {"@gone/A/B/c2.mkv": 1}, failed={"gone"})
    _only(a, "failed_library")


def test_too_short():
    a = _an(["lone.mkv", "@m/x.mkv", "@m", ""], {"@m/Q/x.mkv": 1}, sizes={"@m/x.mkv": 1})
    _only(a, "too_short")
    assert a.reasons["too_short"] == 4


def test_no_candidate():
    _only(_an(["Q/R/S/u.mkv"], {"Z/R/other.mkv": 1}), "no_candidate")


def test_no_candidate_when_only_candidate_is_in_a_failed_library():
    a = _an(["Q/R/u.mkv"], {"@gone/R/u.mkv": 1}, sizes={"Q/R/u.mkv": 1}, failed={"gone"})
    _only(a, "no_candidate")


def test_no_candidate_when_only_candidate_is_itself():
    _only(_an(["Q/R/u.mkv"], {"Q/R/u.mkv": 1}, sizes={"Q/R/u.mkv": 1}), "no_candidate")


def test_needs_size():
    a = _an(["Z/Y/x.mkv"], {"W/Y/x.mkv": 1})
    _only(a, "needs_size")
    a = _an(["Z/Y/x.mkv"], {"W/Y/x.mkv": 1}, sizes={"Z/Y/x.mkv": None})
    _only(a, "needs_size")


def test_size_changed():
    _only(_an(["K/J/i.mkv"], {"L/J/i.mkv": 6}, sizes={"K/J/i.mkv": 5}), "size_changed")
    # a 3-tail candidate of the wrong size is size_changed too
    _only(_an(["K/J/I/i.mkv"], {"L/J/I/i.mkv": 6}, sizes={"K/J/I/i.mkv": 5}), "size_changed")


def test_ambiguous_sized_and_unsized():
    inv = {"A1/O/n.mkv": 5, "A2/O/n.mkv": 5}
    _only(_an(["P/O/n.mkv"], inv, sizes={"P/O/n.mkv": 5}), "ambiguous")
    inv = {"A/S/E/s.mkv": 1, "B/S/E/s.mkv": 1}
    _only(_an(["Old/S/E/s.mkv"], inv), "ambiguous")


def test_contested():
    stale = ["C1/Pa/c.mkv", "C2/Pa/c.mkv"]
    a = _an(stale, {"N/Pa/c.mkv": 7}, sizes=dict.fromkeys(stale, 7))
    _only(a, "contested")
    assert a.reasons["contested"] == 2


def test_collision():
    stale = [f"Old/Movies/T{i}/f{i}.mkv" for i in range(4)]
    inv = {f"@m/T{i}/f{i}.mkv": 1 for i in range(4)}
    a = _an(stale, inv, sizes=dict.fromkeys(stale, 1), verified=set(stale) | {"@m/T3/f3.mkv"})
    assert a.reasons["collision"] == 1 and a.reasons["offered"] == 3
    assert sum(a.reasons.values()) == 4


def test_path_only_withheld():
    stale = [f"Old/S{i}/E/s{i}.mkv" for i in range(5)]
    inv = {f"S{i}/E/s{i}.mkv": 5 for i in range(5)}
    sizes = {s: 5 for s in stale[:3]}
    sizes[stale[3]] = 999  # a recorded mismatch withholds the path-only pair
    a = _an(stale, inv, sizes=sizes)
    assert a.reasons == {
        **dict.fromkeys(REASONS, 0),
        "offered": 3,
        "size_changed": 1,
        "path_only_withheld": 1,
    }


def test_path_only_withheld_wins_over_below_threshold():
    stale = ["Old/S0/E/s0.mkv", "Old/S1/E/s1.mkv"]
    inv = {"S0/E/s0.mkv": 5, "S1/E/s1.mkv": 5}
    a = _an(stale, inv, sizes={stale[0]: 5})  # one sized pair, one path-only pair, rule has 1 < 3
    # A path-only pair beside a sized pair is kept; the rule is simply too small.
    assert a.reasons["below_threshold"] == 2
    a = _an(["Old/S1/E/s1.mkv"], inv)  # path-only alone: no size-confirmed pairs
    _only(a, "path_only_withheld")


def test_below_threshold():
    assert RULE_MIN_VERIFIED == 3
    stale = [f"Lo/V{i}/E/v{i}.mkv" for i in range(2)]
    inv = {f"V{i}/E/v{i}.mkv": 1 for i in range(2)}
    _only(_an(stale, inv, sizes=dict.fromkeys(stale, 1)), "below_threshold")


def test_offered():
    stale = [f"Old/Movies/T{i}/f{i}.mkv" for i in range(3)]
    inv = {f"@m/T{i}/f{i}.mkv": 1 for i in range(3)}
    a = _an(stale, inv, sizes=dict.fromkeys(stale, 1))
    _only(a, "offered")
    assert a.reasons["offered"] == 3 and len(a.rules) == 1


def test_contested_never_reaches_collision_or_threshold():
    stale = ["C1/Pa/c.mkv", "C2/Pa/c.mkv"]
    a = _an(stale, {"N/Pa/c.mkv": 7}, sizes=dict.fromkeys(stale, 7), verified=set(stale) | {"N/Pa/c.mkv"})
    _only(a, "contested")


def test_duplicate_stale_entries_count_once():
    a = _an(["lone.mkv", "lone.mkv"], {})
    assert a.reasons["too_short"] == 1


def _mixed():
    inv: dict[str, int] = {}
    sizes: dict[str, int | None] = {}
    stale: list[str] = []
    extra_verified: set[str] = set()
    # offered via 2-tail movie rule (3) + a collision (1)
    for i in range(4):
        s = f"Old/Movies/T{i}/f{i}.mkv"
        stale.append(s)
        sizes[s] = 1
        inv[f"@m/T{i}/f{i}.mkv"] = 1
    extra_verified.add("@m/T3/f3.mkv")
    # failed library, too short, no candidate
    stale += ["@gone/A/B/c.mkv", "lone.mkv", "Q/R/S/u.mkv"]
    # needs_size
    stale.append("Z/Y/x.mkv")
    inv["W/Y/x.mkv"] = 1
    # size_changed (2-tail)
    stale.append("K/J/i.mkv")
    sizes["K/J/i.mkv"] = 5
    inv["L/J/i.mkv"] = 6
    # ambiguous
    stale.append("P/O/n.mkv")
    sizes["P/O/n.mkv"] = 5
    inv["A1/O/n.mkv"] = 5
    inv["A2/O/n.mkv"] = 5
    # contested
    for c in ("C1/Pa/c.mkv", "C2/Pa/c.mkv"):
        stale.append(c)
        sizes[c] = 7
    inv["N/Pa/c.mkv"] = 7
    # rule R2 with a 3-tail mismatch (size_changed) and a withheld path-only pair, 3 offered
    for i in range(3):
        s = f"Old/S{i}/E/s{i}.mkv"
        stale.append(s)
        sizes[s] = 5
        inv[f"S{i}/E/s{i}.mkv"] = 5
    stale.append("Old/S3/E/s3.mkv")
    sizes["Old/S3/E/s3.mkv"] = 9
    inv["S3/E/s3.mkv"] = 5
    stale.append("Old/S4/E/s4.mkv")
    inv["S4/E/s4.mkv"] = 5
    # below threshold (2 pairs)
    for i in range(2):
        s = f"Lo/V{i}/E/v{i}.mkv"
        stale.append(s)
        sizes[s] = 1
        inv[f"V{i}/E/v{i}.mkv"] = 1
    return stale, inv, sizes, extra_verified


def test_mixed_fixture_hits_every_reason_and_sums_to_distinct_stale_paths():
    stale, inv, sizes, extra = _mixed()
    a = _an(stale, inv, sizes=sizes, verified=set(stale) | extra, failed={"gone"})
    assert a.reasons == {
        "failed_library": 1,
        "too_short": 1,
        "no_candidate": 1,
        "needs_size": 1,
        "size_changed": 2,
        "ambiguous": 1,
        "contested": 2,
        "collision": 1,
        "path_only_withheld": 1,
        "below_threshold": 2,
        "offered": 6,
    }
    assert all(v > 0 for v in a.reasons.values())
    assert sum(a.reasons.values()) == len(set(stale))
    # duplicates in the input do not change the invariant
    b = _an([*stale, *stale], inv, sizes=sizes, verified=set(stale) | extra, failed={"gone"})
    assert b.reasons == a.reasons


def test_examples_are_sorted_capped_and_only_for_nonzero_reasons():
    stale = [f"zz{i}/only.mkv" for i in (5, 3, 9, 1, 7)]
    a = _an(stale, {})
    assert a.reasons["too_short"] == 0 and a.reasons["no_candidate"] == 5
    assert a.examples == {"no_candidate": sorted(stale)[:3]}
    assert "too_short" not in a.examples
    stale, inv, sizes, extra = _mixed()
    a = _an(stale, inv, sizes=sizes, verified=set(stale) | extra, failed={"gone"})
    assert set(a.examples) == set(REASONS)
    assert all(len(v) <= 3 and v == sorted(v) for v in a.examples.values())
    assert len(a.examples["offered"]) == 3
    assert a.examples["contested"] == ["C1/Pa/c.mkv", "C2/Pa/c.mkv"]


def test_two_tail_bucket_scales():
    """30k inventory files, 2k sharing one 2-segment tail; 11k stale, 2k of them in that bucket."""
    inv: dict[str, int] = {}
    stale: list[str] = []
    sizes: dict[str, int | None] = {}
    for i in range(28_000):
        inv[f"Lib/Show{i}/Season 1/e{i}.mkv"] = 100 + i
    for i in range(2_000):
        inv[f"Lib/Gen{i}/Season 1/E01.mkv"] = 1_000_000 + i
    for i in range(9_000):
        s = f"Old/Show{i}/Season 1/e{i}.mkv"
        stale.append(s)
        sizes[s] = 100 + i
    for i in range(2_000):
        s = f"Old/Gen{i}/Season 1/E01.mkv"
        stale.append(s)
        sizes[s] = 5  # nothing has this size: the worst case walks the whole bucket
    assert len(inv) == 30_000 and len(stale) == 11_000
    t = time.perf_counter()
    a = _an(stale, inv, sizes=sizes)
    took = time.perf_counter() - t
    print(f"analyse timing guard: {took:.2f}s")
    assert took < 5.0
    assert a.reasons["offered"] == 9_000
    assert a.reasons["size_changed"] == 2_000
