"""Path translation regression tests.

The canonical_to_subgen_batch prefix was wrong since Phase 1 ('/media/library/'
instead of '/media/') and survived because every scan test mocked subgen with
a transport that ignored the directory= query value. First live end-to-end
scan from the GUI caught it. These tests pin the contract.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def test_split_canonical_default_and_qualified(subarr_env):
    from subarr.paths import _split_canonical

    assert _split_canonical("TV/Show/ep.mkv") == ("", "TV/Show/ep.mkv")
    assert _split_canonical("/TV/Show/") == ("", "TV/Show")
    assert _split_canonical("@disk2/Movies/x.mkv") == ("disk2", "Movies/x.mkv")
    assert _split_canonical("@disk2/") == ("disk2", "")
    assert _split_canonical("@disk2") == ("disk2", "")
    assert _split_canonical("") == ("", "")


def test_canonical_to_subgen_batch_strips_and_prefixes(subarr_env):
    from subarr.paths import canonical_to_subgen_batch

    assert canonical_to_subgen_batch("TV/Foo/Season 1") == "/media/TV/Foo/Season 1"
    assert canonical_to_subgen_batch("/TV/Foo/") == "/media/TV/Foo"
    # File leaf (Phase 2 batch 3): a .mkv path round-trips intact.
    assert canonical_to_subgen_batch("TV/Foo/Season 1/ep.mkv") == "/media/TV/Foo/Season 1/ep.mkv"


def test_canonical_to_subgen_batch_root(subarr_env):
    from subarr.paths import canonical_to_subgen_batch

    assert canonical_to_subgen_batch("") == "/media/"


def test_canonical_to_subgen_batch_respects_env_override(monkeypatch, subarr_env):
    """If a future deployment changes subgen's mount, SUBGEN_MEDIA_PREFIX
    is the single knob that flips the prefix."""
    monkeypatch.setenv("SUBGEN_MEDIA_PREFIX", "/srv/media-root")
    import importlib
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
    assert paths.canonical_to_subgen_batch("TV/X") == "/srv/media-root/TV/X"


def test_canonical_to_subgen_batch_handles_unicode(subarr_env):
    """Real-world: non-ASCII path components (French, Japanese, etc) must
    survive the formatting step intact — httpx URL-encodes at request time,
    not here."""
    from subarr.paths import canonical_to_subgen_batch

    assert (
        canonical_to_subgen_batch("TV/Cette nuit-là/Season 1/ep.mkv")
        == "/media/TV/Cette nuit-là/Season 1/ep.mkv"
    )


@pytest.fixture
def two_libraries(subarr_env, monkeypatch, tmp_path):
    """Library 0 = the fixture media_root (empty slug); library 'disk2'
    rooted at a second tmp dir. Reloads config+paths so settings.libraries
    reflects both."""
    d2 = tmp_path / "disk2"
    (d2 / "Movies").mkdir(parents=True)
    (d2 / "Movies" / "film.mkv").write_bytes(b"")
    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps(
            {
                "libraries": [
                    {
                        "slug": "disk2",
                        "name": "Disk 2",
                        "fs_root": str(d2),
                        "subgen_prefix": "/media2",
                        "arr_prefix": "/data/d2/",
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
    return d2


def test_canonical_to_fs_default_library(subarr_env):
    from subarr.config import settings
    from subarr.paths import canonical_to_fs

    # Byte-identical to today: library 0 resolves under media_root.
    assert canonical_to_fs("TV/Show") == (settings.media_root / "TV" / "Show").resolve()


def test_canonical_to_fs_qualified_library(two_libraries):
    from subarr.paths import canonical_to_fs

    assert canonical_to_fs("@disk2/Movies/film.mkv") == (two_libraries / "Movies" / "film.mkv").resolve()


def test_canonical_to_fs_traversal_guard_per_root(two_libraries):
    from subarr.paths import PathOutsideRootError, canonical_to_fs

    with pytest.raises(PathOutsideRootError):
        canonical_to_fs("@disk2/../escape")


def test_canonical_to_fs_unknown_library_raises(subarr_env):
    from subarr.paths import PathOutsideRootError, canonical_to_fs

    with pytest.raises(PathOutsideRootError):
        canonical_to_fs("@nope/x")


def test_fs_to_canonical_roundtrip_both_libraries(two_libraries):
    from subarr.config import settings
    from subarr.paths import canonical_to_fs, fs_to_canonical

    p0 = settings.media_root / "TV" / "Show" / "ep.mkv"
    assert fs_to_canonical(p0) == "TV/Show/ep.mkv"  # library 0: no @head
    p2 = two_libraries / "Movies" / "film.mkv"
    assert fs_to_canonical(p2) == "@disk2/Movies/film.mkv"
    # round-trips
    assert canonical_to_fs(fs_to_canonical(p2)) == p2.resolve()


def test_fs_to_canonical_outside_all_roots_raises(subarr_env, tmp_path):
    from subarr.paths import PathOutsideRootError, fs_to_canonical

    with pytest.raises(PathOutsideRootError):
        fs_to_canonical(tmp_path / "nowhere" / "x.mkv")
