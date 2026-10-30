"""#638 follow-up: the relink endpoints on a movie library rooted at its movies
folder, and the `unmatched` breakdown, fresh and from the #641 cache."""

from __future__ import annotations

import shutil

import pytest

RELINK = "/api/admin/db/orphans/relink"


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


def _movies(c, d2, n=3, *, sized=True, old="Old/Movies"):
    """n movies at disk2/<Title>/<file> (2 segments after the slug); verdicts
    (and, when `sized`, probe rows) at <old>/<Title>/<file>."""
    olds, news = [], []
    for i in range(n):
        tail = f"Appleseed {1988 + i}/Appleseed {1988 + i} Bluray-1080p.mkv"
        f = d2 / tail
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * (5 + i))
        o = f"{old}/{tail}" if old else tail
        c.app.state.audio_lang.upsert(canonical_path=o, lang_code="en", source="user", confidence=1.0)
        if sized:
            _seed_probe(c.app.state.probe_store, o, 5 + i)
        olds.append(o)
        news.append(f"@disk2/{tail}")
    return olds, news


def test_movie_library_dry_run_offers_rule_and_reports_breakdown(app_two):
    c, d2 = app_two
    olds, _news = _movies(c, d2)
    body = c.get(RELINK).json()
    assert [(r["old_head"], r["new_head"]) for r in body["rules"]] == [("Old/Movies", "@disk2")]
    assert body["rules"][0]["by_size"] == 3 and body["rules"][0]["path_only"] == 0
    reasons = body["unmatched"]["reasons"]
    assert reasons["offered"] == 3 and sum(reasons.values()) == body["stale"] == 3
    assert body["unmatched"]["examples"] == {"offered": sorted(olds)}


def test_movie_library_apply_end_to_end(app_two):
    c, d2 = app_two
    olds, news = _movies(c, d2)
    r = c.post(RELINK, json={"old_head": "Old/Movies", "new_head": "@disk2"}).json()
    assert r == {"applied": True, "reason": "ok", "relinked": 3, "skipped_collision": 0, "failed": 0}
    for o, n in zip(olds, news, strict=True):
        assert c.app.state.audio_lang.get(n) is not None
        assert c.app.state.audio_lang.get(o) is None


def test_empty_old_head_rule_applies(app_two):
    c, d2 = app_two
    olds, news = _movies(c, d2, old="")  # stale rows carry no head at all
    body = c.get(RELINK).json()
    assert [(r["old_head"], r["new_head"]) for r in body["rules"]] == [("", "@disk2")]
    r = c.post(RELINK, json={"old_head": "", "new_head": "@disk2"}).json()
    assert r["applied"] is True and r["relinked"] == 3
    for o, n in zip(olds, news, strict=True):
        assert c.app.state.audio_lang.get(n) is not None
        assert c.app.state.audio_lang.get(o) is None


def test_movie_library_without_recorded_sizes_says_needs_size(app_two):
    c, d2 = app_two
    _movies(c, d2, sized=False)
    body = c.get(RELINK).json()
    assert body["rules"] == [] and body["stale"] == 3
    assert body["unmatched"]["reasons"]["needs_size"] == 3
    assert sum(body["unmatched"]["reasons"].values()) == 3


def test_movie_library_with_changed_sizes_says_size_changed(app_two):
    c, d2 = app_two
    olds, _ = _movies(c, d2)
    for o in olds:
        _seed_probe(c.app.state.probe_store, o, 9999)
    body = c.get(RELINK).json()
    assert body["rules"] == []
    assert body["unmatched"]["reasons"]["size_changed"] == 3


def test_walk_skipped_has_null_unmatched(app_two):
    c, d2 = app_two
    _movies(c, d2, n=2)  # below RULE_MIN_VERIFIED stale verdicts: no walk
    body = c.get(RELINK).json()
    assert body["stale"] == 2 and body["inventory"]["walked"] is False
    assert body["unmatched"] is None


def test_cached_answer_returns_the_same_unmatched(app_two):
    c, d2 = app_two
    _movies(c, d2, sized=False)
    first = c.get(RELINK).json()
    second = c.get(RELINK).json()
    assert first["inventory"]["cached"] is False and second["inventory"]["cached"] is True
    assert first["unmatched"] is not None
    assert second["unmatched"] == first["unmatched"]


def test_unreadable_library_walk_still_reports_unmatched_and_is_not_cached(app_two):
    c, d2 = app_two
    _movies(c, d2, sized=False)
    shutil.rmtree(d2)
    first = c.get(RELINK).json()
    second = c.get(RELINK).json()
    assert first["inventory"]["unreadable_libraries"] == ["disk2"]
    assert first["inventory"]["cached"] is False and second["inventory"]["cached"] is False
    assert first["unmatched"] is not None and sum(first["unmatched"]["reasons"].values()) == 3
    assert second["unmatched"] == first["unmatched"]
