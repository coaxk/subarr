"""#638 follow-up: the matched groups that are NOT offered as rules.

The `unmatched` reasons say how many rows sit in a group below the verified bar
(`below_threshold`) or collide with data already at the new path (`collision`),
but not which groups those are. On a report with 6,224 below-threshold rows
nobody could tell which library they belonged to without the database. These
tests pin `Analysis.unoffered` and its `unmatched.groups` rendering.
"""

from __future__ import annotations

import pytest

from subarr.relink import RULE_MIN_VERIFIED, analyse

RELINK = "/api/admin/db/orphans/relink"


def _an(stale, inv, *, sizes=None, verified=None, probed=None):
    stale = list(stale)
    return analyse(
        stale,
        old_sizes=sizes if sizes is not None else dict.fromkeys(stale, 1),
        verified=set(stale) if verified is None else verified,
        probed=probed if probed is not None else set(),
        inventory=inv,
        failed_slugs=set(),
    )


def _heads(groups):
    return [(g.old_head, g.new_head) for g in groups]


def _movies(n, old="Old/Movies", new="@m", start=0):
    stale = [f"{old}/T{i}/f{i}.mkv" if old else f"T{i}/f{i}.mkv" for i in range(start, start + n)]
    inv = {f"{new}/T{i}/f{i}.mkv": 1 for i in range(start, start + n)}
    return stale, inv


def test_no_groups_when_nothing_matched():
    a = _an(["Q/R/S/u.mkv"], {"Z/R/other.mkv": 1})
    assert a.unoffered == []


def test_offered_rule_is_not_an_unoffered_group():
    stale, inv = _movies(3)
    a = _an(stale, inv)
    assert len(a.rules) == 1 and a.unoffered == []


def test_probe_only_group_below_the_bar_is_listed():
    stale, inv = _movies(50, old="TV", new="@tv")
    verified = set(stale[:2])  # 2 < RULE_MIN_VERIFIED; the other 48 are probe-only
    a = _an(stale, inv, verified=verified, probed=set(stale) - verified)
    assert RULE_MIN_VERIFIED == 3
    assert a.rules == []
    assert _heads(a.unoffered) == [("TV", "@tv")]
    g = a.unoffered[0]
    assert g.verified == 2 and len(g.by_size) == 50 and g.collisions == 0
    assert a.reasons["below_threshold"] == 50


def test_all_collision_group_is_listed_with_no_pairs():
    stale, inv = _movies(4)
    new = set(inv)
    a = _an(stale, inv, verified=set(stale) | new)  # every new path already has a verdict
    assert a.rules == []
    assert _heads(a.unoffered) == [("Old/Movies", "@m")]
    g = a.unoffered[0]
    assert g.collisions == 4 and g.included == [] and g.verified == 0


def test_withheld_path_only_group_is_listed():
    stale = ["Old/S1/E/s1.mkv"]
    a = _an(stale, {"S1/E/s1.mkv": 5}, sizes={})  # path-only alone is withheld
    assert a.reasons["path_only_withheld"] == 1
    assert _heads(a.unoffered) == [("Old", "")]
    assert a.unoffered[0].path_only_withheld == 1 and a.unoffered[0].included == []


def test_largest_group_first_then_heads():
    s1, i1 = _movies(2, old="B", new="@b")
    s2, i2 = _movies(5, old="Z", new="@z", start=10)
    s3, i3 = _movies(2, old="A", new="@a", start=20)
    stale = s1 + s2 + s3
    a = _an(stale, {**i1, **i2, **i3}, verified=set())
    assert _heads(a.unoffered) == [("Z", "@z"), ("A", "@a"), ("B", "@b")]


def test_empty_old_head_group():
    stale, inv = _movies(2, old="")
    a = _an(stale, inv)
    assert _heads(a.unoffered) == [("", "@m")]


def test_group_rows_account_for_every_matched_reason():
    """offered + below_threshold + collision + path_only_withheld rows are exactly
    the rows inside some group, offered or not."""
    s1, i1 = _movies(4)  # offered (3 verified) + 1 collision
    s2, i2 = _movies(6, old="TV", new="@tv", start=10)  # below the bar
    stale = s1 + s2
    verified = set(s1) | {"@m/T3/f3.mkv"}
    a = _an(stale, {**i1, **i2}, verified=verified, probed=set(s2))
    groups = [*a.rules, *a.unoffered]
    in_groups = sum(len(g.included) + g.collisions + g.path_only_withheld for g in groups)
    r = a.reasons
    assert in_groups == r["offered"] + r["below_threshold"] + r["collision"] + r["path_only_withheld"] == 10


# ─── API rendering ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_inventory():
    from subarr import relink

    relink.invalidate_inventory()
    yield
    relink.invalidate_inventory()


@pytest.fixture
def app_two(two_libraries, app_with_stub):
    return app_with_stub, two_libraries


def _seed_probe(store, path, size):
    from subarr.media_probe import ProbeResult

    store.upsert(
        canonical_path=path, mtime=0.0, size=size, result=ProbeResult(canonical_path=path), source="ffprobe"
    )


def _seed(c, d2, n, *, old, verified_n, start=0):
    """n files at disk2/<Title>/<file>; every stale row has a probe row with the
    right size, the first `verified_n` also a verdict."""
    for i in range(start, start + n):
        tail = f"{old.replace('/', '_')} {i}/f{i}.mkv"
        f = d2 / tail
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * 7)
        o = f"{old}/{tail}"
        _seed_probe(c.app.state.probe_store, o, 7)
        if i - start < verified_n:
            c.app.state.audio_lang.upsert(canonical_path=o, lang_code="en", source="user", confidence=1.0)


def test_api_lists_unoffered_group_with_counts_and_sample(app_two):
    c, d2 = app_two
    _seed(c, d2, 3, old="Old/Movies", verified_n=3)  # offered: lets the walk run
    _seed(c, d2, 8, old="Old/TV", verified_n=1, start=100)  # 1 verified, 7 probe-only
    body = c.get(RELINK).json()
    assert [(r["old_head"], r["new_head"]) for r in body["rules"]] == [("Old/Movies", "@disk2")]
    groups = body["unmatched"]["groups"]
    assert [(g["old_head"], g["new_head"]) for g in groups] == [("Old/TV", "@disk2")]
    g = groups[0]
    assert g["verified"] == 1 and g["probe_only"] == 7 and g["by_size"] == 8
    assert g["path_only"] == 0 and g["collisions"] == 0 and g["mismatches"] == 0
    assert g["path_only_withheld"] == 0
    assert len(g["sample"]) == 3 and all(p["how"] == "size" for p in g["sample"])
    assert body["unmatched"]["groups_truncated"] == 0
    assert body["unmatched"]["min_verified"] == RULE_MIN_VERIFIED


def test_api_caps_groups_and_reports_the_rest(app_two, monkeypatch):
    from subarr.routers import admin

    monkeypatch.setattr(admin, "RELINK_GROUPS_CAP", 2)
    c, d2 = app_two
    _seed(c, d2, 3, old="Old/Movies", verified_n=3)
    for k, name in enumerate(["Ga", "Gb", "Gc"]):
        _seed(c, d2, 2, old=f"Old/{name}", verified_n=0, start=200 + 10 * k)
    body = c.get(RELINK).json()
    assert len(body["unmatched"]["groups"]) == 2
    assert body["unmatched"]["groups_truncated"] == 1


def test_api_cached_answer_keeps_groups(app_two):
    c, d2 = app_two
    _seed(c, d2, 6, old="Old/TV", verified_n=2)  # 2 < 3: no rule
    # The walk needs RULE_MIN_VERIFIED stale verdicts in total; a third, unmatched one.
    c.app.state.audio_lang.upsert(
        canonical_path="Gone/X/y.mkv", lang_code="en", source="user", confidence=1.0
    )
    first = c.get(RELINK).json()
    second = c.get(RELINK).json()
    assert first["rules"] == [] and second["inventory"]["cached"] is True
    assert [(g["old_head"], g["new_head"]) for g in first["unmatched"]["groups"]] == [("Old/TV", "@disk2")]
    assert second["unmatched"] == first["unmatched"]
