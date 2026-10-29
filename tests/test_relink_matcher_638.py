"""#638: the pure tail+size matcher that proposes path-rewrite rules."""

from __future__ import annotations

from subarr.relink import MIN_TAIL, RULE_MIN_VERIFIED, Pair, Rule, build_rules, split_head


def _files(prefix: str, n: int = 3, tag: str = "A") -> list[str]:
    """n distinct 3-segment tails under `prefix` ('' for none)."""
    base = f"{prefix}/" if prefix else ""
    return [f"{base}Show{tag}{i}/S1/e{i}.mkv" for i in range(n)]


def _build(stale, inventory, *, old_sizes=None, verified=None, probed=None, failed=frozenset()):
    stale = list(stale)
    return build_rules(
        stale,
        old_sizes=old_sizes if old_sizes is not None else {},
        verified=set(stale) if verified is None else verified,
        probed=probed if probed is not None else set(),
        inventory=inventory,
        failed_slugs=set(failed),
    )


def _remap(paths, old, new):
    out = []
    for p in paths:
        assert p.startswith(old)
        out.append(new + p[len(old) :])
    return out


def test_constants():
    assert RULE_MIN_VERIFIED == 3
    assert MIN_TAIL == 3


def test_split_head():
    assert split_head("@disk2/Movies/f.mkv") == ("@disk2", ["Movies", "f.mkv"])
    assert split_head("Movies/f.mkv") == ("", ["Movies", "f.mkv"])
    assert split_head("@disk2") == ("@disk2", [])
    assert split_head("") == ("", [])


def test_dropped_head_segment():
    stale = _files("Old/Anime")
    new = _remap(stale, "Old/Anime", "Anime")
    inv = {n: 10 + i for i, n in enumerate(new)}
    sizes = {s: inv[n] for s, n in zip(stale, new, strict=True)}
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    r = rules[0]
    assert isinstance(r, Rule)
    assert (r.old_head, r.new_head) == ("Old", "")
    assert len(r.by_size) == 3
    assert all(isinstance(p, Pair) and p.how == "size" for p in r.by_size)


def test_dropped_whole_head_gives_empty_new_head():
    stale = _files("Old")
    inv = {n: 1 for n in _remap(stale, "Old/", "")}
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    assert [(r.old_head, r.new_head) for r in rules] == [("Old", "")]


def test_added_head_segment():
    stale = _files("")
    inv = {f"Anime/{s}": 1 for s in stale}
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    assert [(r.old_head, r.new_head) for r in rules] == [("", "Anime")]


def test_slug_change():
    stale = [f"@disk2/Movies/F{i}/f{i}.mkv" for i in range(3)]
    inv = {f"Movies/F{i}/f{i}.mkv": 1 for i in range(3)}
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    assert [(r.old_head, r.new_head) for r in rules] == [("@disk2", "")]


def test_slug_to_slug_with_head_segment():
    stale = [f"@disk2/Old/Movies/F{i}/f{i}.mkv" for i in range(3)]
    inv = {f"@disk3/Movies/F{i}/f{i}.mkv": 1 for i in range(3)}
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    assert [(r.old_head, r.new_head) for r in rules] == [("@disk2/Old", "@disk3")]


def test_size_mismatch_is_no_match_and_counts_against_rule():
    stale = _files("Old", 4)
    inv = {n: 5 for n in _remap(stale, "Old/", "")}
    sizes = {s: 5 for s in stale}
    sizes[stale[3]] = 999  # mismatch
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    r = rules[0]
    assert r.mismatches == 1
    assert stale[3] not in [p.old for p in r.included]
    assert len(r.by_size) == 3


def test_path_only_included_when_rule_proven():
    stale = _files("Old", 5)
    inv = {n: 5 for n in _remap(stale, "Old/", "")}
    sizes = {s: 5 for s in stale[:3]}
    sizes[stale[3]] = None  # probe row with no size
    # stale[4] has no probe row at all
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    r = rules[0]
    assert len(r.by_size) == 3
    assert len(r.path_only) == 2
    assert all(p.how == "path_only" for p in r.path_only)
    assert r.path_only_withheld == 0
    assert r.mismatches == 0
    assert len(r.included) == 5


def test_path_only_withheld_on_mismatch():
    stale = _files("Old", 6)
    inv = {n: 5 for n in _remap(stale, "Old/", "")}
    sizes = {s: 5 for s in stale[:3]}
    sizes[stale[3]] = 999  # mismatch
    sizes[stale[4]] = None
    # stale[5]: no probe row
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    r = rules[0]
    assert r.mismatches == 1
    assert r.path_only == []
    assert r.path_only_withheld == 2


def test_rule_with_no_size_confirmed_rows_is_not_offered():
    stale = _files("Old", 4)
    inv = {n: 5 for n in _remap(stale, "Old/", "")}
    assert _build(stale, inv, old_sizes={}) == []
    assert _build(stale, inv, old_sizes={s: None for s in stale}) == []


def test_ambiguous_tail_is_unmatched():
    amb = "Old/Show/S1/e.mkv"
    others = _files("Old", 3)
    inv = {"A/Show/S1/e.mkv": 1, "B/Show/S1/e.mkv": 1}
    inv.update({n: 1 for n in _remap(others, "Old/", "")})
    stale = [amb, *others]
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    included = [p.old for r in rules for p in r.included]
    assert amb not in included
    assert sorted(included) == sorted(others)
    # Alone it can never form a rule either.
    assert _build([amb], inv, old_sizes={amb: 1}) == []


def test_longer_tail_disambiguates():
    stale = [*_files("Old/Anime", 2), "Old/Anime/Show/S1/e.mkv"]
    inv = {n: 1 for n in _remap(stale, "Old/Anime", "Anime")}
    inv["TV/Show/S1/e.mkv"] = 1
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    assert len(rules) == 1
    assert (rules[0].old_head, rules[0].new_head) == ("Old", "")
    assert ("Old/Anime/Show/S1/e.mkv", "Anime/Show/S1/e.mkv") in [(p.old, p.new) for p in rules[0].included]


def test_size_filter_runs_before_ambiguity():
    stale = [f"Old/Show{i}/S1/e.mkv" for i in range(3)]
    inv = {}
    sizes = {}
    for i, s in enumerate(stale):
        inv[f"A/Show{i}/S1/e.mkv"] = 7  # right size
        inv[f"B/Show{i}/S1/e.mkv"] = 8  # wrong size, same tail length
        sizes[s] = 7
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    assert (rules[0].old_head, rules[0].new_head) == ("Old", "A")
    assert len(rules[0].by_size) == 3
    assert rules[0].mismatches == 0


def test_threshold_boundary():
    for n, offered in ((2, False), (3, True)):
        stale = _files("Old", n)
        inv = {x: 1 for x in _remap(stale, "Old/", "")}
        rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
        assert bool(rules) is offered, n
    # A probe-only stale row (no verification) does not lift 2 to offered.
    stale = _files("Old", 3)
    inv = {x: 1 for x in _remap(stale, "Old/", "")}
    sizes = {s: 1 for s in stale}
    assert _build(stale, inv, old_sizes=sizes, verified=set(stale[:2])) == []
    # ...but rides along once the rule is offered.
    stale4 = _files("Old", 4)
    inv4 = {x: 1 for x in _remap(stale4, "Old/", "")}
    rules = _build(stale4, inv4, old_sizes={s: 1 for s in stale4}, verified=set(stale4[:3]))
    assert len(rules) == 1
    assert rules[0].verified == 3
    assert len(rules[0].included) == 4


def test_short_paths_never_match():
    stale = [f"Show{i}/e.mkv" for i in range(4)]
    inv = {f"X/Show{i}/e.mkv": 1 for i in range(4)}
    assert _build(stale, inv, old_sizes={s: 1 for s in stale}) == []
    # The @slug head is never a tail segment: 2 segments after removing it.
    stale = [f"@disk2/Show{i}/e.mkv" for i in range(4)]
    inv = {f"Show{i}/e.mkv": 1 for i in range(4)}
    assert _build(stale, inv, old_sizes={s: 1 for s in stale}) == []
    stale = [f"@disk2/Show{i}/e.mkv" for i in range(4)]
    inv = {f"X/Show{i}/e.mkv": 1 for i in range(4)}
    assert _build(stale, inv, old_sizes={s: 1 for s in stale}) == []


def test_case_sensitive():
    stale = [f"old/show{i}/s1/e.mkv" for i in range(3)]
    inv = {f"Show{i}/S1/e.mkv": 1 for i in range(3)}
    assert _build(stale, inv, old_sizes={s: 1 for s in stale}) == []


def test_two_stale_paths_claiming_one_file_are_both_unmatched():
    # Each old layout forms its OWN offered rule (3 other matches apiece), so
    # only the double-claim guard keeps the shared file out of both. Without
    # it, both rules carry it, and the second click lands a skipped collision.
    shared = "Show/S1/e.mkv"
    inv = {"New/" + shared: 1}
    stale = ["Old1/" + shared, "Old2/" + shared]
    for i in range(3):
        inv[f"New/A{i}/S1/a{i}.mkv"] = 1
        inv[f"New/B{i}/S1/b{i}.mkv"] = 1
        stale += [f"Old1/A{i}/S1/a{i}.mkv", f"Old2/B{i}/S1/b{i}.mkv"]
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    assert sorted((r.old_head, r.new_head, r.verified) for r in rules) == [
        ("Old1", "New", 3),
        ("Old2", "New", 3),
    ]
    included = [p.old for r in rules for p in r.included]
    assert not [p for p in included if p.endswith(shared)]


def test_failed_library_rows_never_match():
    stale = [f"@disk2/Movies/F{i}/f{i}.mkv" for i in range(3)]
    inv = {f"Movies/F{i}/f{i}.mkv": 1 for i in range(3)}
    sizes = {s: 1 for s in stale}
    assert len(_build(stale, inv, old_sizes=sizes)) == 1
    assert _build(stale, inv, old_sizes=sizes, failed={"disk2"}) == []
    # The candidate lives in a failed library.
    stale = [f"Movies/F{i}/f{i}.mkv" for i in range(3)]
    inv = {f"@disk2/Movies/F{i}/f{i}.mkv": 1 for i in range(3)}
    sizes = {s: 1 for s in stale}
    assert len(_build(stale, inv, old_sizes=sizes)) == 1
    assert _build(stale, inv, old_sizes=sizes, failed={"disk2"}) == []
    # The default library itself failed (slug "").
    assert _build(stale, inv, old_sizes=sizes, failed={""}) == []


def test_collisions_excluded_and_counted():
    collided = _files("Old", 4, tag="C")
    clean = _files("Old", 3, tag="K")
    stale = collided + clean
    inv = {n: 1 for n in _remap(stale, "Old/", "")}
    sizes = {s: 1 for s in stale}
    already = set(_remap(collided, "Old/", ""))
    rules = _build(stale, inv, old_sizes=sizes, verified=set(stale) | already)
    assert len(rules) == 1
    assert rules[0].verified == 3
    assert rules[0].collisions == 4
    assert sorted(p.old for p in rules[0].included) == sorted(clean)
    # Only the collided ones: nothing to offer.
    rules = _build(
        collided,
        {n: 1 for n in already},
        old_sizes={s: 1 for s in collided},
        verified=set(collided) | already,
    )
    assert rules == []


def test_probe_only_collision_when_new_path_is_probed():
    stale = [*_files("Old", 3, tag="K"), *_files("Old", 1, tag="P")]
    new = _remap(stale, "Old/", "")
    inv = {n: 1 for n in new}
    sizes = {s: 1 for s in stale}
    rules = _build(stale, inv, old_sizes=sizes, verified=set(stale[:3]), probed={new[3]})
    assert len(rules) == 1
    assert rules[0].collisions == 1
    assert stale[3] not in [p.old for p in rules[0].included]
    # A probe row at the new path does NOT collide when the old row IS verified.
    rules = _build(stale[:3], inv, old_sizes=sizes, verified=set(stale[:3]), probed=set(new[:3]))
    assert len(rules) == 1 and rules[0].collisions == 0


def test_shorter_tail_size_match_wins_over_longer_tail_mismatch():
    # Each stale path shares a 4-tail with a wrong-size file and a 3-tail with the right-size one.
    stale = [f"Old/Lib/Show{i}/S1/e.mkv" for i in range(3)]
    inv = {}
    sizes = {}
    for i, s in enumerate(stale):
        inv[f"Lib/Show{i}/S1/e.mkv"] = 99
        inv[f"Other/Show{i}/S1/e.mkv"] = 5
        sizes[s] = 5
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    assert (rules[0].old_head, rules[0].new_head) == ("Old/Lib", "Other")
    assert rules[0].mismatches == 0


def test_mismatch_unique_best_is_recorded():
    stale = _files("Old", 4)
    inv = {n: 5 for n in _remap(stale, "Old/", "")}
    sizes = {s: 5 for s in stale[:3]}
    sizes[stale[3]] = 999
    assert _build(stale, inv, old_sizes=sizes)[0].mismatches == 1


def test_mismatch_tie_records_nothing():
    stale = _files("Old", 4)
    inv = {n: 5 for n in _remap(stale, "Old/", "")}
    # a second equal-tail candidate for the last stale path; both wrong size
    inv["Alt/" + stale[3][len("Old/") :]] = 6
    sizes = {s: 5 for s in stale[:3]}
    sizes[stale[3]] = 999
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    assert rules[0].mismatches == 0
    assert stale[3] not in [p.old for p in rules[0].included]


def test_pairs_sorted_by_old_path():
    stale = list(reversed(_files("Old", 5)))
    inv = {x: 1 for x in _remap(stale, "Old/", "")}
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    olds = [p.old for p in rules[0].included]
    assert olds == sorted(olds)
    assert [p.old for p in rules[0].by_size] == sorted(p.old for p in rules[0].by_size)


def test_rules_sorted_by_verified_desc_then_heads():
    a = _files("OldA", 3)
    b = _files("OldB", 4, tag="B")
    inv = {x: 1 for x in _remap(a, "OldA/", "")}
    inv.update({x: 1 for x in _remap(b, "OldB/", "")})
    stale = a + b
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    assert [(r.old_head, r.verified) for r in rules] == [("OldB", 4), ("OldA", 3)]


def test_stale_path_present_in_inventory_is_not_self_matched():
    stale = _files("", 3)
    inv = {s: 1 for s in stale}
    assert _build(stale, inv, old_sizes={s: 1 for s in stale}) == []


def test_empty_inputs():
    assert _build([], {}) == []
    assert _build(_files("Old"), {}) == []
