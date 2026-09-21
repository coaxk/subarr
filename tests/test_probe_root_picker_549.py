"""#549: offer the probe roots that exist, instead of asking people to type them.

The field is free text, so a root has to be typed from memory — the folder name
under the default library, or the `@slug/...` form for another one. #524 is what
that costs: the wizard pre-filled `TV, Movies`, the real folders were `Film` and
`Serie Tv`, and every scheduled walk failed with nothing on screen saying so.
#546 made a wrong root visible; this makes the right one selectable.

The picker writes the SAME canonical strings the field already accepts, so the
schedule API, `check_probe_root` and the walker need no change — which is the
property these tests pin down.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def folders(media_root):
    """Folders under the DEFAULT library root."""
    for name in ("Serie Tv", "Film", ".hidden", "Anime"):
        (media_root / name).mkdir(parents=True, exist_ok=True)
    (media_root / "loose.mkv").write_bytes(b"x")
    return media_root


def _by_slug(groups):
    return {g["slug"]: g for g in groups}


def test_the_default_library_offers_its_folders(subarr_env, folders):
    from subarr.probe_walker import suggest_probe_roots_by_library

    (group,) = suggest_probe_roots_by_library()
    assert group["slug"] == ""
    # The fixture root may hold folders from other fixtures; what matters is
    # that the real ones are offered, in a stable case-insensitive order.
    assert {"Anime", "Film", "Serie Tv"} <= set(group["roots"])
    assert group["roots"] == sorted(group["roots"], key=str.lower)


def test_a_file_is_never_offered_as_a_root(subarr_env, folders):
    from subarr.probe_walker import suggest_probe_roots_by_library

    (group,) = suggest_probe_roots_by_library()
    assert "loose.mkv" not in group["roots"]


def test_hidden_folders_are_not_offered(subarr_env, folders):
    from subarr.probe_walker import suggest_probe_roots_by_library

    (group,) = suggest_probe_roots_by_library()
    assert not any(r.startswith(".") for r in group["roots"])


def test_a_second_library_offers_slug_qualified_roots(two_libraries, folders):
    """The `@slug/folder` form is exactly what nobody can be expected to know,
    so it is the form the picker has to write for them."""
    from subarr.probe_walker import suggest_probe_roots_by_library

    (d2 := two_libraries / "Serie Tv").mkdir(parents=True, exist_ok=True)
    assert d2.is_dir()
    groups = _by_slug(suggest_probe_roots_by_library())
    assert "disk2" in groups
    assert "@disk2/Serie Tv" in groups["disk2"]["roots"]
    assert "@disk2/Movies" in groups["disk2"]["roots"]


def test_a_library_itself_is_offered_as_a_root(two_libraries, folders):
    """Probing a whole library is a legitimate choice, and for the default one
    that is the empty-prefix case the canonical form cannot express as a folder."""
    from subarr.probe_walker import suggest_probe_roots_by_library

    groups = _by_slug(suggest_probe_roots_by_library())
    assert groups["disk2"]["library_root"] == "@disk2"
    assert groups[""]["library_root"] == ""


def test_a_library_that_cannot_be_read_yields_no_roots(subarr_env, monkeypatch, tmp_path):
    """An unmounted share must not look like a library with no folders in a way
    that hides it — it is listed, with nothing to pick."""
    from subarr import probe_walker

    def boom(_canonical):
        raise OSError("share went away")

    monkeypatch.setattr(probe_walker, "canonical_to_fs", boom)
    groups = probe_walker.suggest_probe_roots_by_library()
    assert groups and groups[0]["roots"] == []


# ── the property that matters: what it writes is what the field accepts ──


@pytest.mark.parametrize("which", ["default", "second"])
def test_every_offered_root_passes_the_servers_own_check(two_libraries, folders, which):
    from subarr.probe_walker import check_probe_root, suggest_probe_roots_by_library

    (two_libraries / "Serie Tv").mkdir(parents=True, exist_ok=True)
    groups = _by_slug(suggest_probe_roots_by_library())
    group = groups[""] if which == "default" else groups["disk2"]
    for root in group["roots"]:
        assert check_probe_root(root)["ok"], f"the picker would write {root!r}, which the server rejects"


def test_the_library_root_itself_passes_the_check(two_libraries, folders):
    from subarr.probe_walker import check_probe_root, suggest_probe_roots_by_library

    for group in suggest_probe_roots_by_library():
        assert check_probe_root(group["library_root"])["ok"]


# ── the endpoint the picker reads ────────────────────────────────────


def test_the_endpoint_returns_the_groups(app_with_stub):
    r = app_with_stub.get("/api/probe-roots/suggestions")
    assert r.status_code == 200
    groups = r.json()["libraries"]
    assert isinstance(groups, list) and groups
    assert {"slug", "name", "library_root", "roots"} <= set(groups[0])
