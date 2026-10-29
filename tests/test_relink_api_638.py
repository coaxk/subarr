"""#638: relink dry run and apply endpoints.

The reporter's rows were recorded while mounts were misconfigured, so most
known paths point at a layout that no longer exists while the same files sit at
new paths. The endpoints offer a path-rewrite rule per layout change and move
the rows on one click; the #453 guard then judges only what is genuinely gone.
"""

from __future__ import annotations

import shutil
import sys

import pytest

DRY = "/api/admin/db/orphans"
RELINK = "/api/admin/db/orphans/relink"


@pytest.fixture(autouse=True)
def _fresh_inventory():
    from subarr import relink

    relink.invalidate_inventory()
    yield
    relink.invalidate_inventory()


def _seed_probe(store, path, size, source="ffprobe"):
    from subarr.media_probe import ProbeResult

    store.upsert(
        canonical_path=path, mtime=0.0, size=size, result=ProbeResult(canonical_path=path), source=source
    )


def _snapshot(app):
    al = app.state.audio_lang._conn.execute("SELECT * FROM audio_lang_verifications ORDER BY 1").fetchall()
    pr = app.state.probe_store._conn.execute("SELECT * FROM media_probe ORDER BY 1").fetchall()
    return al, pr


def _scene(c, root, n=4, *, probe=True, old="Old/Anime", new="Anime"):
    """n files at `new/...`, with verdicts (and probe rows) at `old/...`."""
    olds, news = [], []
    for i in range(n):
        tail = f"Show{i}/S1/e{i}.mkv"
        f = root / new / tail
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * (5 + i))
        o, nw = f"{old}/{tail}", f"{new}/{tail}"
        c.app.state.audio_lang.upsert(canonical_path=o, lang_code="en", source="user", confidence=1.0)
        if probe:
            _seed_probe(c.app.state.probe_store, o, 5 + i)
        olds.append(o)
        news.append(nw)
    return olds, news


def _rule(c):
    body = c.get(RELINK).json()
    assert len(body["rules"]) == 1, body
    return body["rules"][0]


def test_dry_run_offers_rule_and_writes_nothing(app_with_stub, media_root):
    c = app_with_stub
    olds, news = _scene(c, media_root, 4)
    before = _snapshot(c.app)
    r = c.get(RELINK)
    assert r.status_code == 200
    body = r.json()
    rule = body["rules"][0]
    assert (rule["old_head"], rule["new_head"]) == ("Old", "")
    assert rule["verified"] == 4 and rule["by_size"] == 4 and rule["path_only"] == 0
    assert rule["mismatches"] == 0 and rule["collisions"] == 0 and rule["truncated"] == 0
    assert len(rule["sample"]) == 4
    assert [p["old"] for p in rule["pairs_by_size"]] == sorted(olds)
    assert body["stale"] == 4
    assert body["inventory"]["files"] == 5  # 4 relocated files + the fixture ep.mkv
    assert body["inventory"]["unreadable_libraries"] == []
    assert isinstance(body["inventory"]["walk_s"], float)
    assert _snapshot(c.app) == before


def test_sample_is_first_five_of_many(app_with_stub, media_root):
    c = app_with_stub
    _scene(c, media_root, 8)
    rule = _rule(c)
    assert len(rule["sample"]) == 5
    assert [p["old"] for p in rule["sample"]] == [p["old"] for p in rule["pairs_by_size"][:5]]


def test_nothing_stale_offers_nothing(app_with_stub, media_root):
    c = app_with_stub
    (media_root / "a.mkv").write_bytes(b"x")
    c.app.state.audio_lang.upsert(canonical_path="a.mkv", lang_code="en", source="user", confidence=1.0)
    body = c.get(RELINK).json()
    assert body["rules"] == [] and body["stale"] == 0


def test_apply_relinks_then_guard_judges_remainder(app_with_stub, media_root):
    c = app_with_stub
    olds, news = _scene(c, media_root, 4)
    c.app.state.audio_lang.upsert(
        canonical_path="Old/Gone/S1/g.mkv", lang_code="en", source="user", confidence=1.0
    )
    # Before: 5 of 5 known paths are missing, so the prune refuses.
    assert c.get(DRY).json()["safe"] is False

    r = c.post(RELINK, json={"old_head": "Old", "new_head": ""})
    assert r.status_code == 200
    assert r.json() == {"applied": True, "reason": "ok", "relinked": 4, "skipped_collision": 0, "failed": 0}

    after = c.get(DRY).json()
    assert after["safe"] is True
    assert after["would_delete"] == 1
    assert after["missing"] == ["Old/Gone/S1/g.mkv"]
    for o, n in zip(olds, news, strict=True):
        moved = c.app.state.audio_lang.get(n)
        assert moved is not None and moved.lang_code == "en"
        assert moved.evidence["relinked_from"] == o
        assert "carried_from" not in moved.evidence
        assert c.app.state.audio_lang.get(o) is None
    assert set(c.app.state.probe_store.sizes_for(news)) == set(news)
    assert c.app.state.probe_store.sizes_for(olds) == {}
    assert c.get(RELINK).json()["rules"] == []


def test_path_only_and_probe_only_rows_ride_along(app_with_stub, media_root):
    c = app_with_stub
    olds, news = _scene(c, media_root, 4)
    # one pair with an arr_mediainfo size, replacing the ffprobe row
    c.app.state.probe_store.delete(olds[2])
    _seed_probe(c.app.state.probe_store, olds[2], 7, source="arr_mediainfo")
    # one verdict with no probe row at all (path only)
    c.app.state.probe_store.delete(olds[3])
    # one probe-only stale row (no verdict) under the same rule
    extra_old, extra_new = "Old/Anime/ShowP/S1/p.mkv", "Anime/ShowP/S1/p.mkv"
    (media_root / extra_new).parent.mkdir(parents=True)
    (media_root / extra_new).write_bytes(b"x" * 3)
    _seed_probe(c.app.state.probe_store, extra_old, 3)

    rule = _rule(c)
    assert rule["verified"] == 4  # the probe-only row does not count
    assert rule["by_size"] == 4 and rule["path_only"] == 1

    r = c.post(RELINK, json={"old_head": "Old", "new_head": ""}).json()
    assert r["relinked"] == 5 and r["failed"] == 0 and r["skipped_collision"] == 0
    assert c.app.state.audio_lang.get(news[3]) is not None
    assert c.app.state.probe_store.sizes_for([extra_new]) == {extra_new: 3}
    assert c.app.state.probe_store.sizes_for([extra_old]) == {}


@pytest.mark.parametrize("preexisting", [0, 3])
def test_apply_is_idempotent(app_with_stub, media_root, preexisting):
    c = app_with_stub
    olds, news = _scene(c, media_root, 3 + preexisting)
    for n in news[3:]:
        c.app.state.audio_lang.upsert(canonical_path=n, lang_code="fr", source="user", confidence=1.0)
    first = c.post(RELINK, json={"old_head": "Old", "new_head": ""}).json()
    assert first["applied"] is True and first["relinked"] == 3
    # The collided pairs never make a rule again, so nothing is re-offered.
    assert c.get(RELINK).json()["rules"] == []
    second = c.post(RELINK, json={"old_head": "Old", "new_head": ""}).json()
    assert second["applied"] is False
    assert (second["relinked"], second["skipped_collision"], second["failed"]) == (0, 0, 0)
    for n in news[3:]:
        assert c.app.state.audio_lang.get(n).lang_code == "fr"


def test_collision_existing_verdict_wins(app_with_stub, media_root):
    c = app_with_stub
    olds, news = _scene(c, media_root, 4)
    c.app.state.audio_lang.upsert(canonical_path=news[0], lang_code="fr", source="user", confidence=1.0)
    rule = _rule(c)
    assert rule["collisions"] == 1 and rule["verified"] == 3

    r = c.post(RELINK, json={"old_head": "Old", "new_head": ""}).json()
    assert r["relinked"] == 3
    assert c.app.state.audio_lang.get(news[0]).lang_code == "fr"
    assert "relinked_from" not in (c.app.state.audio_lang.get(news[0]).evidence or {})
    assert c.app.state.audio_lang.get(olds[0]).lang_code == "en"
    assert olds[0] in c.get(DRY).json()["missing"]
    # The collided pair's probe row did not move either.
    assert c.app.state.probe_store.sizes_for([olds[0]]) == {olds[0]: 5}


def test_verdict_appearing_after_the_analysis_counts_as_skipped_collision(
    app_with_stub, media_root, monkeypatch
):
    """A verdict lands at a new path between the analysis and the move."""
    c = app_with_stub
    olds, news = _scene(c, media_root, 4)
    store = c.app.state.audio_lang
    real_relink = store.relink

    def racing(old, new):
        if old == olds[1]:
            store.upsert(canonical_path=new, lang_code="fr", source="user", confidence=1.0)
        return real_relink(old, new)

    monkeypatch.setattr(store, "relink", racing)
    r = c.post(RELINK, json={"old_head": "Old", "new_head": ""}).json()
    assert r["relinked"] == 3 and r["skipped_collision"] == 1
    assert store.get(news[1]).lang_code == "fr"
    assert c.app.state.probe_store.sizes_for([olds[1]]) == {olds[1]: 6}


def test_mount_drop_offers_nothing_and_guard_refuses(app_with_stub, media_root, monkeypatch):
    from subarr import relink

    c = app_with_stub
    _scene(c, media_root, 4)
    # The new paths are unreadable too: a walk that found nothing and a failed root.
    monkeypatch.setattr(relink, "walk_inventory", lambda: relink.Inventory({}, frozenset({""})))
    body = c.get(RELINK).json()
    assert body["rules"] == []
    assert body["inventory"]["unreadable_libraries"] == [""]
    assert c.get(DRY).json()["safe"] is False


def test_empty_inventory_offers_nothing_and_guard_refuses(app_with_stub, media_root, monkeypatch):
    from subarr import relink

    c = app_with_stub
    _scene(c, media_root, 4)
    monkeypatch.setattr(relink, "walk_inventory", lambda: relink.Inventory({}, frozenset()))
    assert c.get(RELINK).json()["rules"] == []
    assert c.get(DRY).json()["safe"] is False


def test_really_unreadable_library_root_offers_nothing(app_with_stub, media_root):
    c = app_with_stub
    _scene(c, media_root, 4)
    shutil.rmtree(media_root)
    body = c.get(RELINK).json()
    assert body["rules"] == []
    assert body["inventory"]["unreadable_libraries"] == [""]
    assert c.get(DRY).json()["safe"] is False


def test_mount_drop_mid_walk_offers_nothing(app_with_stub, media_root, monkeypatch):
    from subarr import relink

    c = app_with_stub
    _scene(c, media_root, 4)
    real_walk = relink.os.walk

    def flaky(top, onerror=None, **kw):
        for i, item in enumerate(real_walk(top, onerror=onerror, **kw)):
            yield item
            if i == 1:
                onerror(OSError("mount dropped"))
                return

    monkeypatch.setattr(relink.os, "walk", flaky)
    body = c.get(RELINK).json()
    assert body["rules"] == []
    assert body["inventory"]["unreadable_libraries"] == [""]


def test_partial_inventory_unreadable_library_never_maps_elsewhere(app_with_stub_two, media_root):
    c, d2 = app_with_stub_two
    from subarr import relink

    for i in range(3):
        f = media_root / "Movies" / f"F{i}" / f"f{i}.mkv"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x" * (3 + i))
        c.app.state.audio_lang.upsert(
            canonical_path=f"@disk2/Movies/F{i}/f{i}.mkv", lang_code="en", source="user", confidence=1.0
        )
        _seed_probe(c.app.state.probe_store, f"@disk2/Movies/F{i}/f{i}.mkv", 3 + i)
    # Control: with disk2 readable the slug change is offered.
    body = c.get(RELINK).json()
    assert [(r["old_head"], r["new_head"]) for r in body["rules"]] == [("@disk2", "")]
    # disk2 vanishes: its rows must not be mapped onto the default library.
    shutil.rmtree(d2)
    relink.invalidate_inventory()
    body = c.get(RELINK).json()
    assert body["rules"] == []
    assert body["inventory"]["unreadable_libraries"] == ["disk2"]


def test_apply_restats_targets_and_skips_changed_files(app_with_stub, media_root):
    c = app_with_stub
    olds, news = _scene(c, media_root, 5)
    assert _rule(c)["by_size"] == 5
    (media_root / news[0]).unlink()  # deleted since the dry run
    (media_root / news[1]).write_bytes(b"different length!!")  # replaced
    r = c.post(RELINK, json={"old_head": "Old", "new_head": ""}).json()
    assert r["failed"] == 2 and r["relinked"] == 3 and r["skipped_collision"] == 0
    for o in olds[:2]:
        assert c.app.state.audio_lang.get(o) is not None
        assert c.app.state.probe_store.sizes_for([o]) != {}
    for n in news[:2]:
        assert c.app.state.audio_lang.get(n) is None


def test_apply_unknown_rule_writes_nothing(app_with_stub, media_root):
    c = app_with_stub
    _scene(c, media_root, 4)
    before = _snapshot(c.app)
    r = c.post(RELINK, json={"old_head": "Nope", "new_head": "Elsewhere"})
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] is False
    assert (body["relinked"], body["skipped_collision"], body["failed"]) == (0, 0, 0)
    assert _snapshot(c.app) == before


def test_apply_rejects_a_malformed_body(app_with_stub, media_root):
    c = app_with_stub
    assert c.post(RELINK, json={"old_head": "Old"}).status_code == 422


def test_dry_run_caps_listed_pairs(app_with_stub, media_root, monkeypatch):
    c = app_with_stub
    _scene(c, media_root, 5)
    monkeypatch.setattr(sys.modules["subarr.routers.admin"], "RELINK_PAIRS_CAP", 2)
    rule = _rule(c)
    assert len(rule["pairs_by_size"]) == 2
    assert rule["by_size"] == 5  # the count is not capped
    assert rule["truncated"] == 3
    assert len(rule["sample"]) == 5


@pytest.fixture
def app_with_stub_two(two_libraries, app_with_stub):
    return app_with_stub, two_libraries
