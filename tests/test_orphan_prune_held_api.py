"""#638: the orphan-prune endpoints report and keep held cache-only rows."""

from __future__ import annotations

from pathlib import Path

DRY = "/api/admin/db/orphans"
APPLY = "/api/admin/db/orphans/prune"


def _file(media_root, rel):
    f = media_root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    return f


def _probe(app, *rels):
    from subarr.media_probe import ProbeResult

    for rel in rels:
        app.state.probe_store.upsert(
            canonical_path=rel, mtime=1.0, size=1, result=ProbeResult(canonical_path=rel)
        )


def _probe_paths(app):
    return set(app.state.probe_store.all_paths())


def test_reporter_shape_end_to_end(app_with_stub, media_root):
    c = app_with_stub
    present = [f"TV/Show/ep{i}.mkv" for i in range(3)]
    missing = [f"TV/Show/gone{i}.mkv" for i in range(5)]
    for rel in present:
        _file(media_root, rel)
    _probe(c.app, *(present + missing))

    body = c.get(DRY).json()
    assert body["safe"] is True
    assert body["would_delete"] == 5
    assert body["held"] == 0
    assert body["held_scopes"] == []

    applied = c.post(APPLY).json()
    assert applied["deleted_total"] == 5
    assert _probe_paths(c.app) == set(present)


def test_existing_top_folder_without_readable_files_holds(app_with_stub, media_root):
    c = app_with_stub
    missing = [f"TV/Show/gone{i}.mkv" for i in range(4)]
    _probe(c.app, *missing)

    body = c.get(DRY).json()
    assert body["safe"] is False
    assert body["held"] == 4
    assert body["held_scopes"][0]["present"] == 0

    applied = c.post(APPLY).json()
    assert applied["deleted_total"] == 0
    assert _probe_paths(c.app) == set(missing)


def test_gone_top_folder_with_library_witnesses_is_pruned(app_with_stub, media_root):
    c = app_with_stub
    present = [f"Movies/Film/f{i}.mkv" for i in range(3)]
    missing = [f"Old/Show/gone{i}.mkv" for i in range(4)]
    for rel in present:
        _file(media_root, rel)
    _probe(c.app, *(present + missing))

    applied = c.post(APPLY).json()
    assert applied["deleted_total"] == 4
    assert _probe_paths(c.app) == set(present)


def test_get_after_a_partial_hold_reports_the_held_rows(app_with_stub, media_root):
    c = app_with_stub
    present = [f"TV/Show/ep{i}.mkv" for i in range(3)]
    live_missing = ["TV/Show/gone.mkv"]
    held = [f"Dead/Show/h{i}.mkv" for i in range(2)]
    for rel in present:
        _file(media_root, rel)
    (media_root / "Dead").mkdir()
    _probe(c.app, *(present + live_missing + held))

    applied = c.post(APPLY).json()
    assert applied["deleted_total"] == 1

    body = c.get(DRY).json()
    assert body["safe"] is False
    assert body["would_delete"] == len(held)
    assert body["held"] == len(held)


def test_two_scopes_delete_only_the_live_one(app_with_stub, media_root):
    c = app_with_stub
    present = [f"TV/Show/ep{i}.mkv" for i in range(3)]
    live_missing = [f"TV/Show/gone{i}.mkv" for i in range(2)]
    dead_missing = [f"Dead/Show/d{i}.mkv" for i in range(3)]
    for rel in present:
        _file(media_root, rel)
    (media_root / "Dead").mkdir()
    _probe(c.app, *(present + live_missing + dead_missing))

    body = c.post(APPLY).json()
    assert body["deleted_total"] == 2
    assert _probe_paths(c.app) == set(present + dead_missing)


def test_every_response_carries_the_held_fields(app_with_stub, media_root):
    c = app_with_stub

    empty = c.get(DRY).json()
    assert empty["held"] == 0
    assert empty["held_verified"] == 0
    assert empty["held_scopes"] == []

    _file(media_root, "a.mkv")
    c.app.state.audio_lang.upsert(canonical_path="a.mkv", lang_code="en", source="user", confidence=1.0)
    for i in range(4):
        c.app.state.audio_lang.upsert(
            canonical_path=f"g{i}.mkv", lang_code="en", source="user", confidence=1.0
        )

    refused = c.post(APPLY).json()
    assert refused["safe"] is False
    assert refused["held_verified"] == 4
    assert refused["held"] == 4
    assert refused["held_scopes"] == []


def test_the_no_stores_response_carries_the_held_fields(app_with_stub, media_root, monkeypatch):
    from subarr.routers import admin

    c = app_with_stub
    monkeypatch.setattr(admin, "_orphan_stores", lambda request: {})

    body = c.get(DRY).json()
    assert body["held"] == 0
    assert body["held_verified"] == 0
    assert body["held_scopes"] == []
    assert "no prunable stores" in body["reason"]


def test_path_state_is_three_valued(subarr_env, media_root, monkeypatch):
    from subarr.routers.admin import _path_state

    _file(media_root, "a.mkv")
    assert _path_state("a.mkv") == "present"
    assert _path_state("nope.mkv") == "missing"
    assert _path_state("@nosuchlibrary/x.mkv") == "unknown"

    def boom(self):
        raise OSError("stat failed")

    monkeypatch.setattr(Path, "exists", boom)
    assert _path_state("a.mkv") == "unknown"


def test_folder_gone_is_false_unless_the_folder_is_really_gone(subarr_env, media_root):
    from subarr.routers.admin import _folder_gone

    assert _folder_gone("", "Old") is True
    assert _folder_gone("", "TV") is False
    assert _folder_gone("nosuchlibrary", "TV") is False


def test_protected_rows_still_prune_as_before(app_with_stub, media_root):
    c = app_with_stub
    present = ["a.mkv", "b.mkv", "c.mkv"]
    for rel in present:
        _file(media_root, rel)
    for rel in present + ["gone.mkv"]:
        c.app.state.audio_lang.upsert(canonical_path=rel, lang_code="en", source="user", confidence=1.0)

    body = c.post(APPLY).json()
    assert body["safe"] is True
    assert body["deleted_total"] >= 1
    assert c.app.state.audio_lang.get("gone.mkv") is None
    assert c.app.state.audio_lang.get("a.mkv") is not None


def test_path_state_treats_traversal_as_unknown_and_folder_gone_swallows_errors(
    subarr_env, media_root, monkeypatch
):
    from subarr.routers.admin import _folder_gone, _path_state

    assert _path_state("../x.mkv") == "unknown"

    def boom(self):
        raise OSError("stat failed")

    monkeypatch.setattr(Path, "exists", boom)
    assert _folder_gone("", "TV") is False


def test_a_hold_is_idempotent(app_with_stub, media_root):
    c = app_with_stub
    missing = [f"Dead/Show/d{i}.mkv" for i in range(3)]
    (media_root / "Dead").mkdir()
    _probe(c.app, *missing)

    first = c.post(APPLY).json()
    assert first["safe"] is False
    assert first["deleted_total"] == 0

    second = c.get(DRY).json()
    assert second["safe"] is False
    assert second["held"] == first["held"]
    assert second["held_scopes"] == first["held_scopes"]
    assert second["would_delete"] == first["would_delete"]
    assert _probe_paths(c.app) == set(missing)


def test_held_rows_are_kept_when_the_share_returns(app_with_stub, media_root):
    c = app_with_stub
    present = [f"TV/Show/ep{i}.mkv" for i in range(3)]
    live_missing = ["TV/Show/gone.mkv"]
    held = [f"Dead/Show/d{i}.mkv" for i in range(3)]
    for rel in present:
        _file(media_root, rel)
    (media_root / "Dead").mkdir()
    _probe(c.app, *(present + live_missing + held))

    first = c.post(APPLY).json()
    assert first["deleted_total"] == 1
    assert _probe_paths(c.app) == set(present + held)

    for rel in held:
        _file(media_root, rel)

    second = c.post(APPLY).json()
    assert second["deleted_total"] == 0
    assert _probe_paths(c.app) == set(present + held)
