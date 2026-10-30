"""#638 follow-up: a library rooted AT its movies folder gives every movie a
2-segment canonical (`@slug/Title/file.mkv`). A 2-segment tail may match, but
only when the stale row's recorded size equals the current file's size."""

from __future__ import annotations

from subarr.relink import build_rules


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


def _movies(n=3):
    """Stale rows under Anime/Movies, current files at @animemovies/<Title>/<file>."""
    stale = [f"Anime/Movies/Appleseed {1988 + i}/Appleseed {1988 + i} Bluray-1080p.mkv" for i in range(n)]
    inv = {"@animemovies/" + s.split("/", 2)[2]: 10 + i for i, s in enumerate(stale)}
    sizes = {s: 10 + i for i, s in enumerate(stale)}
    return stale, inv, sizes


# 1
def test_movie_library_matches_by_size():
    stale, inv, sizes = _movies()
    rules = _build(stale, inv, old_sizes=sizes)
    assert [(r.old_head, r.new_head) for r in rules] == [("Anime/Movies", "@animemovies")]
    assert len(rules[0].by_size) == 3 and rules[0].path_only == []
    assert all(p.how == "size" for p in rules[0].included)


# 2
def test_movie_library_unsized_is_not_offered():
    stale, inv, _ = _movies()
    assert _build(stale, inv, old_sizes={}) == []
    assert _build(stale, inv, old_sizes={s: None for s in stale}) == []


# 3
def test_movie_library_size_changed_is_not_offered():
    stale, inv, _ = _movies()
    assert _build(stale, inv, old_sizes={s: 999 for s in stale}) == []


# 3 (mixed): a wrong-size 2-tail file is not evidence against a rule
def test_two_tail_wrong_size_is_not_a_mismatch():
    stale = [f"Old/S{i}/E/s{i}.mkv" for i in range(4)]
    inv = {f"S{i}/E/s{i}.mkv": 5 for i in range(4)}
    sizes = {s: 5 for s in stale[:3]}
    sizes[stale[3]] = None  # a path-only pair under the same rule
    # Sized, and its only candidate is a 2-tail file of another size whose
    # heads are the rule's own ("Old", ""): if it counted as a mismatch it
    # would land on this rule and withhold the path-only pair.
    odd = "Old/Z/q.mkv"
    inv["Z/q.mkv"] = 6
    sizes[odd] = 5
    rules = _build([*stale, odd], inv, old_sizes=sizes)
    assert len(rules) == 1
    r = rules[0]
    assert (r.old_head, r.new_head) == ("Old", "")
    assert r.mismatches == 0
    assert [p.old for p in r.path_only] == [stale[3]] and r.path_only_withheld == 0


def test_three_tail_wrong_size_still_counts_as_a_mismatch():
    stale = [f"Old/S{i}/E/s{i}.mkv" for i in range(5)]
    inv = {f"S{i}/E/s{i}.mkv": 5 for i in range(5)}
    sizes = {s: 5 for s in stale[:3]}
    sizes[stale[3]] = 999  # unique 3-tail candidate, wrong size
    sizes[stale[4]] = None
    rules = _build(stale, inv, old_sizes=sizes)
    assert rules[0].mismatches == 1
    assert rules[0].path_only == [] and rules[0].path_only_withheld == 1


def test_three_tail_mismatch_ignores_a_two_tail_wrong_size_neighbour():
    stale = [f"Old/S{i}/E/s{i}.mkv" for i in range(4)]
    inv = {f"S{i}/E/s{i}.mkv": 5 for i in range(4)}
    inv["Other/Z/E/s3.mkv"] = 6  # shares only the 2-tail E/s3.mkv
    sizes = {s: 5 for s in stale[:3]}
    sizes[stale[3]] = 999
    rules = _build(stale, inv, old_sizes=sizes)
    assert rules[0].mismatches == 1  # recorded against the unique 3-tail candidate only


# 4
def test_two_tail_right_size_beats_three_tail_wrong_size():
    stale = [f"Old/Lib/Show{i}/e{i}.mkv" for i in range(3)]
    inv = {}
    sizes = {}
    for i, s in enumerate(stale):
        inv[f"Lib/Show{i}/e{i}.mkv"] = 99  # longer tail, wrong size
        inv[f"@m/Show{i}/e{i}.mkv"] = 5  # shorter tail, right size
        sizes[s] = 5
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    assert (rules[0].old_head, rules[0].new_head) == ("Old/Lib", "@m")
    assert rules[0].mismatches == 0
    assert all(p.how == "size" for p in rules[0].included)


# 5
def test_two_tail_size_tie_is_unmatched():
    stale = [f"Old/Show{i}/e.mkv" for i in range(3)]
    inv = {}
    for i in range(3):
        inv[f"@a/Show{i}/e.mkv"] = 5
        inv[f"@b/Show{i}/e.mkv"] = 5
    assert _build(stale, inv, old_sizes={s: 5 for s in stale}) == []


# 6a: the shared tail is only 'Season 1/E0n.mkv'; an equal size makes it a match
def test_generic_tv_tail_matches_on_equal_size_only():
    stale = [f"Old/Show/Season 1/E0{i}.mkv" for i in range(3)]
    inv = {f"New/Other/Season 1/E0{i}.mkv": 7 + i for i in range(3)}
    rules = _build(stale, inv, old_sizes={s: 7 + i for i, s in enumerate(stale)})
    assert [(r.old_head, r.new_head) for r in rules] == [("Old/Show", "New/Other")]
    assert all(p.how == "size" for p in rules[0].included)
    # Same names, no size known: path alone never pairs different shows.
    assert _build(stale, inv, old_sizes={}) == []
    # Sizes differ: no pairing either.
    assert _build(stale, inv, old_sizes={s: 99 for s in stale}) == []


# 6b: the 3-tail wins when the same show also exists at the same size
def test_generic_tv_tail_prefers_the_three_tail():
    stale = [f"Old/Show{i}/Season 1/E01.mkv" for i in range(3)]
    inv = {}
    for i in range(3):
        inv[f"New/Show{i}/Season 1/E01.mkv"] = 7
        inv[f"New/Other{i}/Season 1/E01.mkv"] = 7
    rules = _build(stale, inv, old_sizes={s: 7 for s in stale})
    assert len(rules) == 1
    assert (rules[0].old_head, rules[0].new_head) == ("Old", "New")
    assert [p.new for p in rules[0].included] == [f"New/Show{i}/Season 1/E01.mkv" for i in range(3)]


# unsized paths ignore 2-tail candidates but still use 3-tail ones
def test_unsized_uses_three_tail_candidates_only():
    stale = [f"Old/S{i}/E/s{i}.mkv" for i in range(4)]
    inv = {f"S{i}/E/s{i}.mkv": 5 for i in range(4)}
    for i in range(4):
        inv[f"Elsewhere/Q{i}/E/s{i}.mkv"] = 5  # shares only the 2-tail E/s{i}.mkv
    sizes = {s: 5 for s in stale[:3]}
    rules = _build(stale, inv, old_sizes=sizes)
    assert len(rules) == 1
    assert [p.new for p in rules[0].path_only] == ["S3/E/s3.mkv"]


def test_unsized_three_tail_tie_is_unmatched():
    stale = ["Old/S/E/s.mkv"]
    inv = {"A/S/E/s.mkv": 1, "B/S/E/s.mkv": 1}
    assert _build(stale, inv, old_sizes={}) == []


def test_two_tail_candidate_in_a_failed_library_is_ignored():
    stale, inv, sizes = _movies()
    assert len(_build(stale, inv, old_sizes=sizes)) == 1
    assert _build(stale, inv, old_sizes=sizes, failed={"animemovies"}) == []


def test_a_stale_path_is_never_its_own_two_tail_candidate():
    stale = [f"Show{i}/e{i}.mkv" for i in range(3)]
    assert _build(stale, {s: 1 for s in stale}, old_sizes={s: 1 for s in stale}) == []


def test_two_stale_paths_claiming_one_two_tail_file_are_both_dropped():
    stale, inv, sizes = _movies(4)
    extra = "Other/Movies/Appleseed 1988/Appleseed 1988 Bluray-1080p.mkv"
    sizes[extra] = sizes[stale[0]]
    rules = _build([*stale, extra], inv, old_sizes=sizes)
    olds = [p.old for r in rules for p in r.included]
    assert stale[0] not in olds and extra not in olds
    assert len(olds) == 3


def test_empty_old_head_two_tail_rule():
    stale = [f"Show{i}/e{i}.mkv" for i in range(3)]
    inv = {f"@m/{s}": 1 for s in stale}
    rules = _build(stale, inv, old_sizes={s: 1 for s in stale})
    assert [(r.old_head, r.new_head) for r in rules] == [("", "@m")]
