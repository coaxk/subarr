"""#641: the relink dry run must not walk the library on every Review load
when the stale rows can never form a rule.

The measured case: 1,959 stale paths that were all filename renames and quality
upgrades, a 29.8 s walk every 300 s of Review use, 0 rules. A "no rules" result
can only change when its inputs do, so it is cached against everything the
result depends on except the inventory, which is bounded by NO_RULES_TTL_S.
"""

from __future__ import annotations

import pytest

RELINK = "/api/admin/db/orphans/relink"


@pytest.fixture(autouse=True)
def _fresh_inventory():
    from subarr import relink

    relink.invalidate_inventory()
    yield
    relink.invalidate_inventory()


@pytest.fixture
def clock(monkeypatch):
    """Drives both relink caches; starts well past zero like time.monotonic."""
    from subarr import relink

    now = [1000.0]
    monkeypatch.setattr(relink, "_clock", lambda: now[0])
    return now


@pytest.fixture
def walks(monkeypatch):
    from subarr import relink

    calls = []
    real = relink.walk_inventory

    def counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(relink, "walk_inventory", counted)
    return calls


def _renames(c, root, n=4, *, start=0, verified=True):
    """n episodes upgraded in place: verdicts point at the old filename, the
    folder and the new file exist. The tail differs, so no rule can form.
    The old file was 5+i bytes (its probe row says so)."""
    olds = []
    for i in range(start, start + n):
        d = root / "TV" / f"Show{i}" / "Season 1"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"e{i} WEBDL-1080p.mkv").write_bytes(b"y" * 50)
        o = f"TV/Show{i}/Season 1/e{i} HDTV-720p.mkv"
        _seed_probe(c.app.state.probe_store, o, 5 + i)
        if verified:
            c.app.state.audio_lang.upsert(canonical_path=o, lang_code="en", source="user", confidence=1.0)
        olds.append(o)
    return olds


def _seed_probe(store, path, size):
    from subarr.media_probe import ProbeResult

    store.upsert(
        canonical_path=path, mtime=0.0, size=size, result=ProbeResult(canonical_path=path), source="ffprobe"
    )


def _past_inventory_ttl(clock):
    clock[0] += 301.0


def test_renames_only_walk_once_then_cached(app_with_stub, media_root, clock, walks):
    c = app_with_stub
    _renames(c, media_root)
    first = c.get(RELINK).json()
    assert first["rules"] == [] and first["stale"] == 4
    assert first["inventory"]["walked"] is True and first["inventory"]["cached"] is False
    _past_inventory_ttl(clock)
    second = c.get(RELINK).json()
    assert second["rules"] == []
    assert second["inventory"]["cached"] is True
    # The cached answer still describes the walk that produced it.
    assert second["inventory"]["files"] == first["inventory"]["files"] > 0
    assert walks == [1]


def test_new_stale_path_walks_again(app_with_stub, media_root, clock, walks):
    c = app_with_stub
    _renames(c, media_root)
    c.get(RELINK)
    _past_inventory_ttl(clock)
    _renames(c, media_root, 1, start=10)
    body = c.get(RELINK).json()
    assert body["stale"] == 5 and body["inventory"]["cached"] is False
    assert walks == [1, 1]


def test_stale_path_gaining_a_verdict_walks_again(app_with_stub, media_root, clock, walks):
    """A probe-only stale row that gains a verdict counts toward a rule's
    RULE_MIN_VERIFIED, so it changes what the analysis can return."""
    c = app_with_stub
    _renames(c, media_root)
    (probe_only,) = _renames(c, media_root, 1, start=10, verified=False)
    c.get(RELINK)
    _past_inventory_ttl(clock)
    c.app.state.audio_lang.upsert(canonical_path=probe_only, lang_code="en", source="user", confidence=1.0)
    assert c.get(RELINK).json()["inventory"]["cached"] is False
    assert walks == [1, 1]


def test_old_size_change_walks_again(app_with_stub, media_root, clock, walks):
    """Sizes pick between candidates and withhold path-only pairs."""
    c = app_with_stub
    (o, *_rest) = _renames(c, media_root)
    c.get(RELINK)
    _past_inventory_ttl(clock)
    _seed_probe(c.app.state.probe_store, o, 999)
    assert c.get(RELINK).json()["inventory"]["cached"] is False
    assert walks == [1, 1]


def test_cache_expires_after_ttl(app_with_stub, media_root, clock, walks):
    from subarr import relink

    c = app_with_stub
    _renames(c, media_root)
    c.get(RELINK)
    clock[0] += relink.NO_RULES_TTL_S - 1
    assert c.get(RELINK).json()["inventory"]["cached"] is True
    clock[0] += 2
    assert c.get(RELINK).json()["inventory"]["cached"] is False
    assert walks == [1, 1]


def test_library_change_misses_the_cache(clock, monkeypatch):
    from subarr import relink

    inv = relink.Inventory({}, frozenset())
    monkeypatch.setattr(relink, "_library_key", lambda: (("", "/a"),))
    relink.remember_no_rules("fp", inv)
    assert relink.cached_no_rules("fp") is inv
    assert relink.cached_no_rules("other") is None
    monkeypatch.setattr(relink, "_library_key", lambda: (("", "/b"),))
    assert relink.cached_no_rules("fp") is None


def test_walk_with_an_unreadable_library_is_never_cached(app_with_stub, media_root, clock, monkeypatch):
    """Rows in library A may map onto library B once B is readable again, and
    B coming back does not change A's stale set, so the key cannot see it."""
    from subarr import relink

    c = app_with_stub
    _renames(c, media_root)
    calls = []
    monkeypatch.setattr(
        relink, "walk_inventory", lambda: calls.append(1) or relink.Inventory({}, frozenset({"anime"}))
    )
    c.get(RELINK)
    _past_inventory_ttl(clock)
    body = c.get(RELINK).json()
    assert body["inventory"]["cached"] is False
    assert calls == [1, 1]


def test_result_with_rules_is_not_cached(app_with_stub, media_root, clock, walks):
    c = app_with_stub
    for i in range(4):
        f = media_root / "Anime" / f"Show{i}" / "S1" / f"e{i}.mkv"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        o = f"Old/Anime/Show{i}/S1/e{i}.mkv"
        _seed_probe(c.app.state.probe_store, o, 1)
        c.app.state.audio_lang.upsert(canonical_path=o, lang_code="en", source="user", confidence=1.0)
    assert len(c.get(RELINK).json()["rules"]) == 1
    _past_inventory_ttl(clock)
    body = c.get(RELINK).json()
    assert len(body["rules"]) == 1 and body["inventory"]["cached"] is False
    assert walks == [1, 1]


def test_invalidate_inventory_clears_the_no_rules_cache(app_with_stub, media_root, clock, walks):
    from subarr import relink

    c = app_with_stub
    _renames(c, media_root)
    c.get(RELINK)
    relink.invalidate_inventory()
    assert c.get(RELINK).json()["inventory"]["cached"] is False
    assert walks == [1, 1]


def test_apply_ignores_a_cached_no_rules_answer(app_with_stub, media_root, clock, walks):
    """The accepted trade-off: files appearing at a new location, with the stale
    set unchanged, stay invisible to the dry run until NO_RULES_TTL_S. Apply
    must never inherit that blindness."""
    c = app_with_stub
    olds = _renames(c, media_root)
    assert c.get(RELINK).json()["rules"] == []
    for i, o in enumerate(olds):
        f = media_root / "Moved" / o
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * (5 + i))
    _past_inventory_ttl(clock)
    assert c.get(RELINK).json()["inventory"]["cached"] is True
    r = c.post(RELINK, json={"old_head": "", "new_head": "Moved"}).json()
    assert r["relinked"] == 4


def _collided(c, root):
    """4 stale verdicts whose files exist at a new path that ALREADY holds a
    verdict: every pair collides, so no rule. Returns the new paths."""
    news = []
    for i in range(4):
        f = root / "Anime" / f"Show{i}" / "S1" / f"e{i}.mkv"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        o, n = f"Old/Anime/Show{i}/S1/e{i}.mkv", f"Anime/Show{i}/S1/e{i}.mkv"
        _seed_probe(c.app.state.probe_store, o, 1)
        c.app.state.audio_lang.upsert(canonical_path=o, lang_code="en", source="user", confidence=1.0)
        c.app.state.audio_lang.upsert(canonical_path=n, lang_code="fr", source="user", confidence=1.0)
        news.append(n)
    return news


def test_verdict_removed_at_the_new_path_walks_again(app_with_stub, media_root, clock, walks):
    """Collisions read verdicts at the NEW paths, so the key must cover them,
    not only the stale paths (review finding on the first cut)."""
    c = app_with_stub
    news = _collided(c, media_root)
    assert c.get(RELINK).json()["rules"] == []
    _past_inventory_ttl(clock)
    for n in news:
        c.app.state.audio_lang._conn.execute(
            "DELETE FROM audio_lang_verifications WHERE canonical_path = ?", (n,)
        )
    c.app.state.audio_lang._conn.commit()
    body = c.get(RELINK).json()
    assert body["inventory"]["cached"] is False
    assert len(body["rules"]) == 1


def test_probe_row_change_anywhere_misses_the_cache(app_with_stub, media_root, clock, walks):
    """Probe-only stale rows collide on `new in probed`."""
    c = app_with_stub
    _renames(c, media_root)
    c.get(RELINK)
    _past_inventory_ttl(clock)
    _seed_probe(c.app.state.probe_store, "TV/Show0/Season 1/e0 WEBDL-1080p.mkv", 50)
    assert c.get(RELINK).json()["inventory"]["cached"] is False


def test_ttl_runs_from_the_walk_not_from_the_answer(app_with_stub, media_root, clock, monkeypatch):
    """A no-rules answer built on a 299 s old cached inventory must expire
    NO_RULES_TTL_S after that walk, not 299 s later."""
    from subarr import relink

    c = app_with_stub
    _renames(c, media_root)
    relink.get_inventory()  # walk at t=1000, cached for 300 s
    clock[0] += 299
    c.get(RELINK)  # answer at t=1299 on the t=1000 inventory
    clock[0] = 1000 + relink.NO_RULES_TTL_S + 1
    assert c.get(RELINK).json()["inventory"]["cached"] is False
