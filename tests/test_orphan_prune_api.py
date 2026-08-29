"""#453: the endpoints that let a user actually clear orphaned rows.

The reporter confirmed the diagnosis on the issue: Sonarr holds the NEW
filename while subarr still shows the old one, so the arrs are correct and the
stale state is entirely in our tables. Their Tdarr setup triggers an arr
rescan within 30s of a rename, which is why they reach and stay in that state
so reliably, and why re-walking never cleared it.

The dangerous part is that the fix deletes user data. audio_lang_store holds
hand-confirmed audio-language verifications that cost hours to rebuild. So the
dry run is the product, not a debugging aid: nothing is deleted without the
user seeing the exact list first.
"""

from __future__ import annotations

DRY = "/api/admin/db/orphans"
APPLY = "/api/admin/db/orphans/prune"


def _seed(app, present: list[str], missing: list[str], media_root):
    """Put rows in BOTH stores; create real files for `present` only."""
    for rel in present:
        f = media_root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    for rel in present + missing:
        app.state.audio_lang.upsert(canonical_path=rel, lang_code="en", source="user", confidence=1.0)
    return present, missing


def test_dry_run_lists_orphans_and_deletes_nothing(app_with_stub, media_root):
    c = app_with_stub
    _seed(c.app, ["a.mkv", "b.mkv", "c.mkv"], ["gone.mkv"], media_root)

    r = c.get(DRY)
    assert r.status_code == 200
    body = r.json()
    assert body["safe"] is True
    assert body["would_delete"] == 1
    assert "gone.mkv" in body["missing"]
    # nothing removed
    assert c.app.state.audio_lang.get("gone.mkv") is not None


def test_apply_deletes_only_the_missing_rows(app_with_stub, media_root):
    c = app_with_stub
    _seed(c.app, ["a.mkv", "b.mkv", "c.mkv"], ["gone.mkv"], media_root)

    r = c.post(APPLY)
    assert r.status_code == 200
    body = r.json()
    assert body["safe"] is True
    assert body["deleted_total"] >= 1
    assert c.app.state.audio_lang.get("gone.mkv") is None
    assert c.app.state.audio_lang.get("a.mkv") is not None


def test_a_mass_disappearance_is_REFUSED_and_deletes_nothing(app_with_stub, media_root):
    """The whole reason this feature is guarded.

    A dropped CIFS mount often still lists directories while serving nothing,
    so every path goes missing in the same instant. A naive prune would wipe
    every verification the user ever confirmed.
    """
    c = app_with_stub
    _seed(c.app, ["a.mkv"], ["g1.mkv", "g2.mkv", "g3.mkv", "g4.mkv"], media_root)

    r = c.post(APPLY)
    assert r.status_code == 200
    body = r.json()
    assert body["safe"] is False
    assert body["deleted_total"] == 0
    assert "mount" in body["reason"].lower() or "storage" in body["reason"].lower()
    for p in ("g1.mkv", "g2.mkv", "g3.mkv", "g4.mkv"):
        assert c.app.state.audio_lang.get(p) is not None, "not one row may be removed"


def test_nothing_missing_is_a_clean_no_op(app_with_stub, media_root):
    c = app_with_stub
    _seed(c.app, ["a.mkv", "b.mkv"], [], media_root)

    body = c.get(DRY).json()
    assert body["safe"] is True
    assert body["would_delete"] == 0
    assert body["missing"] == []


def test_an_unresolvable_path_is_never_pruned(app_with_stub, media_root):
    """Fails SAFE. A path we cannot resolve to a real location -- traversal,
    or a library that has been removed from config -- is treated as PRESENT.

    Treating it as missing would mean a temporary library misconfiguration
    makes every one of its rows a deletion candidate at once. Leaving a stale
    row behind is recoverable; deleting hours of verifications is not.
    """
    c = app_with_stub
    _seed(c.app, ["a.mkv", "b.mkv", "c.mkv"], [], media_root)
    c.app.state.audio_lang.upsert(
        canonical_path="@nosuchlibrary/x.mkv", lang_code="en", source="user", confidence=1.0
    )

    body = c.get(DRY).json()
    assert "@nosuchlibrary/x.mkv" not in body["missing"]

    c.post(APPLY)
    assert c.app.state.audio_lang.get("@nosuchlibrary/x.mkv") is not None


def test_empty_store_does_not_crash_or_claim_success(app_with_stub, media_root):
    c = app_with_stub
    body = c.get(DRY).json()
    assert body["would_delete"] == 0
    assert isinstance(body["reason"], str) and body["reason"]
