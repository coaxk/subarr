"""#483: a filesystem-root tie must not silently re-home a file to library 0.

`fs_to_canonical` picks the library with the LONGEST matching `fs_root` and
broke ties with a strictly-greater comparison. Library 0 is always first in the
list, so two libraries sharing a root handed every path to library 0 and dropped
the `@<slug>/` head.

That head is not decoration: it selects the library, and therefore the
subgen_prefix. Losing it produces a wrong-but-plausible subgen path, a 404, and
subarr then reporting "file removed, no longer on disk" for a file that is
sitting right there. Reported by AztecGuyGDL after a Requeue.

⚠️ There is a uniqueness guard on `arr_prefix` for exactly this hazard, whose
comment calls it "a real #161 multi-instance footgun". There was none on
`fs_root`, and the consequence is the same.

⚠️ Rejecting a duplicate root at config time was the first instinct and is
WRONG here: invalid library config is handled fail-soft by falling back to the
single default library, so raising would silently delete the user's second
library rather than fix it. Preserving the setup and resolving the ambiguity is
the better trade.
"""

from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path

import pytest


def _configure(tmp_path: Path, monkeypatch, extras: list[dict], media_root: Path):
    """Reload config+paths with a chosen library set. Mirrors conftest's
    two_libraries fixture, but lets each test choose the roots."""
    store = tmp_path / "ov.json"
    store.write_text(json.dumps({"libraries": extras}))
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    monkeypatch.setenv("SUBARR_MEDIA_ROOT", str(media_root))
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
    return config, paths


@pytest.fixture(autouse=True)
def _restore_modules():
    # Reloading config/paths leaks into other tests unless restored.
    yield
    import importlib

    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)


class TestDuplicateRootKeepsTheSlug:
    def test_the_explicit_library_wins_over_the_catch_all_default(self, tmp_path, monkeypatch):
        media = tmp_path / "media"
        (media / "tv_shows").mkdir(parents=True)
        f = media / "tv_shows" / "ep.mkv"
        f.write_bytes(b"")

        _cfg, paths = _configure(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "tv",
                    "name": "TV",
                    "fs_root": str(media),
                    "subgen_prefix": "/media/tv_shows",
                    "arr_prefix": "/tv/",
                }
            ],
            media_root=media,
        )
        # Before the fix this returned 'tv_shows/ep.mkv': library 0 won the tie
        # and the file was re-homed, taking library 0's subgen_prefix with it.
        assert paths.fs_to_canonical(f) == "@tv/tv_shows/ep.mkv"

    def test_the_canonical_round_trips_back_to_the_same_file(self, tmp_path, monkeypatch):
        # The real damage was the round trip: canonical -> fs -> canonical came
        # back different, so a requeue targeted a path that does not exist.
        media = tmp_path / "media"
        (media / "tv_shows").mkdir(parents=True)
        f = media / "tv_shows" / "ep.mkv"
        f.write_bytes(b"")

        _cfg, paths = _configure(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "tv",
                    "name": "TV",
                    "fs_root": str(media),
                    "subgen_prefix": "/media/tv_shows",
                    "arr_prefix": "/tv/",
                }
            ],
            media_root=media,
        )
        canon = paths.fs_to_canonical(f)
        assert paths.canonical_to_fs(canon).resolve() == f.resolve()
        assert paths.fs_to_canonical(paths.canonical_to_fs(canon)) == canon

    def test_the_subgen_path_is_the_configured_one(self, tmp_path, monkeypatch):
        # What the user actually saw: the wrong prefix reached subgen.
        media = tmp_path / "media"
        (media / "tv_shows").mkdir(parents=True)
        f = media / "tv_shows" / "ep.mkv"
        f.write_bytes(b"")

        _cfg, paths = _configure(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "tv",
                    "name": "TV",
                    "fs_root": str(media),
                    "subgen_prefix": "/media/tv_shows",
                    "arr_prefix": "/tv/",
                }
            ],
            media_root=media,
        )
        sub = paths.canonical_to_subgen_batch(paths.fs_to_canonical(f))
        assert sub == "/media/tv_shows/tv_shows/ep.mkv", sub


class TestSpecificityStillOutranksTheSlugPreference:
    def test_a_deeper_default_root_still_wins(self, tmp_path, monkeypatch):
        # The tie-break must apply ONLY on an equal-length match. A library
        # whose root is genuinely deeper is the more specific owner and must
        # keep winning, slug or not, or this fix would break normal nesting.
        outer = tmp_path / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        f = inner / "ep.mkv"
        f.write_bytes(b"")

        # library 0 rooted DEEPER than the slugged one.
        _cfg, paths = _configure(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "outer",
                    "name": "Outer",
                    "fs_root": str(outer),
                    "subgen_prefix": "/m-outer",
                    "arr_prefix": "/outer/",
                }
            ],
            media_root=inner,
        )
        assert paths.fs_to_canonical(f) == "ep.mkv"


class TestUnchangedBehaviour:
    def test_single_library_is_untouched(self, tmp_path, monkeypatch):
        media = tmp_path / "media"
        media.mkdir()
        f = media / "ep.mkv"
        f.write_bytes(b"")

        _cfg, paths = _configure(tmp_path, monkeypatch, [], media_root=media)
        assert paths.fs_to_canonical(f) == "ep.mkv"

    def test_distinct_roots_are_untouched(self, tmp_path, monkeypatch):
        media = tmp_path / "media"
        media.mkdir()
        d2 = tmp_path / "disk2"
        d2.mkdir()
        f = d2 / "ep.mkv"
        f.write_bytes(b"")

        _cfg, paths = _configure(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "disk2",
                    "name": "Disk 2",
                    "fs_root": str(d2),
                    "subgen_prefix": "/media2",
                    "arr_prefix": "/data/d2/",
                }
            ],
            media_root=media,
        )
        assert paths.fs_to_canonical(f) == "@disk2/ep.mkv"


class TestTheAmbiguityIsSurfaced:
    def test_a_duplicate_root_is_logged_once_not_silently_resolved(self, tmp_path, monkeypatch, caplog):
        # The tie-break is a best guess, not a truth. Two libraries rooted at
        # the same place are genuinely ambiguous from the path alone, so the
        # config deserves to be visible rather than quietly papered over.
        media = tmp_path / "media"
        media.mkdir()
        f = media / "ep.mkv"
        f.write_bytes(b"")

        _cfg, paths = _configure(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "tv",
                    "name": "TV",
                    "fs_root": str(media),
                    "subgen_prefix": "/m",
                    "arr_prefix": "/tv/",
                }
            ],
            media_root=media,
        )
        with caplog.at_level(logging.WARNING):
            paths.fs_to_canonical(f)
        assert any("fs_root" in r.message or "fs_root" in str(r.args) for r in caplog.records), (
            "an ambiguous library root should be surfaced, not silently guessed"
        )


class TestTheOtherWayToGetAWrongCanonical:
    """#483 has a SECOND candidate mechanism, deliberately left unchanged for
    now, and pinned here so a future change to it is a decision rather than an
    accident.

    strip_arr_prefix documents that "a path matching no library passes through
    slash-stripped". So an *arr path whose prefix matches nothing configured
    becomes a library-0 canonical verbatim, which then resolves against library
    0's subgen_prefix. That produces exactly the reporter's string, and nothing
    complains.

    ⚠️ This is NOT asserted to be correct behaviour. It is asserted to be the
    CURRENT behaviour. Turning the silent pass-through into an error could break
    installs working today, and it is not yet known whether it is what bit the
    reporter, so it waits on their configuration.
    """

    def test_an_unmatched_arr_prefix_passes_through_as_a_library_0_canonical(self, tmp_path, monkeypatch):
        media = tmp_path / "media"
        media.mkdir()
        _cfg, paths = _configure(
            tmp_path,
            monkeypatch,
            [
                {
                    "slug": "tv",
                    "name": "TV",
                    "fs_root": str(tmp_path / "tv"),
                    "subgen_prefix": "/media/tv_shows",
                    "arr_prefix": "/tv/",
                }
            ],
            media_root=media,
        )
        # Matches the 'tv' library: stripped and qualified, as intended.
        assert paths.strip_arr_prefix("/tv/Show/ep.mkv") == "@tv/Show/ep.mkv"

        # Matches NOTHING: passes through slash-stripped, and is now
        # indistinguishable from a genuine library-0 relative path.
        orphan = paths.strip_arr_prefix("/somewhere-else/Show/ep.mkv")
        assert orphan == "somewhere-else/Show/ep.mkv"
        assert not orphan.startswith("@"), (
            "an unmatched arr path silently becomes a library-0 canonical; "
            "if this ever changes, #483's second mechanism has been addressed"
        )
