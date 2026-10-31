"""The relink dry run takes "present" from an inventory already in memory.

Measured on the maintainer's install: 6,374 known paths on a CIFS share, each
checked with a realpath plus a stat, took 5.5 s of an 8.0 s dry run even when
the walk itself came from the #641 cache. A path the in-memory inventory lists
is present without a stat; every other path is still stat'd, so the inventory
can only make a row look present, never missing. Apply never takes the shortcut.
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
    from subarr import relink

    now = [1000.0]
    monkeypatch.setattr(relink, "_clock", lambda: now[0])
    return now


@pytest.fixture
def stats(monkeypatch):
    """Every path the endpoints stat, in call order."""
    from subarr.routers import admin

    calls: list[str] = []
    real = admin._path_exists

    def spy(p):
        calls.append(p)
        return real(p)

    monkeypatch.setattr(admin, "_path_exists", spy)
    return calls


def _seed_probe(store, path, size):
    from subarr.media_probe import ProbeResult

    store.upsert(
        canonical_path=path, mtime=0.0, size=size, result=ProbeResult(canonical_path=path), source="ffprobe"
    )


def _present(c, root, n, *, start=0):
    """n episodes that exist where the DB says they are."""
    paths = []
    for i in range(start, start + n):
        d = root / "TV" / f"Live{i}" / "Season 1"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"e{i}.mkv").write_bytes(b"z" * 9)
        p = f"TV/Live{i}/Season 1/e{i}.mkv"
        _seed_probe(c.app.state.probe_store, p, 9)
        paths.append(p)
    return paths


def _renames(c, root, n=4):
    """n verdicts whose files were upgraded in place: stale, and no rule forms."""
    olds = []
    for i in range(n):
        d = root / "TV" / f"Show{i}" / "Season 1"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"e{i} WEBDL-1080p.mkv").write_bytes(b"y" * 50)
        o = f"TV/Show{i}/Season 1/e{i} HDTV-720p.mkv"
        _seed_probe(c.app.state.probe_store, o, 5 + i)
        c.app.state.audio_lang.upsert(canonical_path=o, lang_code="en", source="user", confidence=1.0)
        olds.append(o)
    return olds


def test_no_inventory_in_memory_stats_every_known_path(app_with_stub, media_root, clock, stats):
    c = app_with_stub
    live = _present(c, media_root, 5)
    olds = _renames(c, media_root)
    body = c.get(RELINK).json()
    assert body["stale"] == len(olds)
    assert sorted(stats) == sorted(live + olds)


def test_second_dry_run_stats_only_paths_the_inventory_lacks(app_with_stub, media_root, clock, stats):
    c = app_with_stub
    _present(c, media_root, 5)
    olds = _renames(c, media_root)
    first = c.get(RELINK).json()
    stats.clear()
    second = c.get(RELINK).json()
    assert sorted(stats) == sorted(olds)
    assert second["stale"] == first["stale"] == len(olds)
    assert second["unmatched"] == first["unmatched"]


def test_file_appearing_after_the_walk_is_stat_and_not_stale(app_with_stub, media_root, clock, stats):
    c = app_with_stub
    _renames(c, media_root)
    c.get(RELINK)  # walk: the next file does not exist yet
    late = _present(c, media_root, 1, start=50)
    stats.clear()
    body = c.get(RELINK).json()
    assert late[0] in stats  # not in the inventory, so it must be checked
    assert body["stale"] == 4


def test_file_deleted_after_the_walk_reads_present_until_the_inventory_ages(
    app_with_stub, media_root, clock, stats
):
    from subarr import relink

    c = app_with_stub
    live = _present(c, media_root, 1)
    _renames(c, media_root)
    c.get(RELINK)
    (media_root / live[0]).unlink()
    assert c.get(RELINK).json()["stale"] == 4  # the accepted blind spot
    clock[0] += relink.NO_RULES_TTL_S + 1
    assert c.get(RELINK).json()["stale"] == 5


def test_inventory_from_the_no_rules_cache_is_used_after_the_walk_cache_expires(
    app_with_stub, media_root, clock, stats
):
    from subarr import relink

    c = app_with_stub
    _present(c, media_root, 5)
    olds = _renames(c, media_root)
    c.get(RELINK)
    clock[0] += relink.INVENTORY_TTL_S + 1
    stats.clear()
    body = c.get(RELINK).json()
    assert body["inventory"]["cached"] is True
    assert sorted(stats) == sorted(olds)


def test_apply_stats_every_known_path(app_with_stub, media_root, clock, stats):
    c = app_with_stub
    live = _present(c, media_root, 5)
    olds = _renames(c, media_root)
    c.get(RELINK)
    stats.clear()
    c.post(RELINK, json={"old_head": "TV", "new_head": "X"})
    assert sorted(set(stats)) == sorted(live + olds)


def test_unresolvable_path_is_stat_and_counts_present(app_with_stub, media_root, clock, stats):
    c = app_with_stub
    _renames(c, media_root)
    _seed_probe(c.app.state.probe_store, "@nolib/A/B/c.mkv", 1)
    c.get(RELINK)
    stats.clear()
    body = c.get(RELINK).json()
    assert "@nolib/A/B/c.mkv" in stats
    assert body["stale"] == 4


# ─── peek_inventory ────────────────────────────────────────────────────────


def _inv(walked_at, files=None):
    from subarr.relink import Inventory

    return Inventory(files=files or {}, failed_slugs=frozenset(), walked_at=walked_at)


def test_peek_is_none_with_nothing_in_memory(clock):
    from subarr import relink

    assert relink.peek_inventory() is None


def test_peek_never_walks(clock, monkeypatch):
    from subarr import relink

    monkeypatch.setattr(relink, "walk_inventory", lambda: pytest.fail("peek walked"))
    assert relink.peek_inventory() is None


def test_peek_prefers_the_newer_walk(clock, monkeypatch):
    from subarr import relink

    monkeypatch.setattr(relink, "_library_key", lambda: (("", "/a"),))
    old = _inv(900.0, {"old": 1})
    relink.remember_no_rules("fp", old)
    new = _inv(990.0, {"new": 1})
    monkeypatch.setattr(relink, "walk_inventory", lambda: new)
    relink.get_inventory()
    assert relink.peek_inventory() is new


def test_peek_ignores_another_library_config(clock, monkeypatch):
    from subarr import relink

    monkeypatch.setattr(relink, "_library_key", lambda: (("", "/a"),))
    relink.remember_no_rules("fp", _inv(1000.0))
    monkeypatch.setattr(relink, "_library_key", lambda: (("", "/b"),))
    assert relink.peek_inventory() is None


def test_peek_ignores_an_expired_no_rules_entry(clock, monkeypatch):
    from subarr import relink

    monkeypatch.setattr(relink, "_library_key", lambda: (("", "/a"),))
    relink.remember_no_rules("fp", _inv(1000.0))
    clock[0] += relink.NO_RULES_TTL_S
    assert relink.peek_inventory() is None
