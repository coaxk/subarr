"""Path translation regression tests.

The canonical_to_subgen_batch prefix was wrong since Phase 1 ('/media/library/'
instead of '/media/') and survived because every scan test mocked subgen with
a transport that ignored the directory= query value. First live end-to-end
scan from the GUI caught it. These tests pin the contract.
"""

from __future__ import annotations

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


def test_canonical_to_subgen_batch_per_library(two_libraries):
    from subarr.paths import canonical_to_subgen_batch

    # library 0 keeps /media (existing regression tests still assert this)
    assert canonical_to_subgen_batch("TV/Foo") == "/media/TV/Foo"
    # library disk2 uses its own subgen prefix
    assert canonical_to_subgen_batch("@disk2/Movies/film.mkv") == "/media2/Movies/film.mkv"
    assert canonical_to_subgen_batch("@disk2/") == "/media2/"


def test_subgen_to_canonical_per_library(two_libraries):
    from subarr.paths import subgen_to_canonical

    assert subgen_to_canonical("/media/TV/Foo/ep.mkv") == "TV/Foo/ep.mkv"
    assert subgen_to_canonical("/media2/Movies/film.mkv") == "@disk2/Movies/film.mkv"
    assert subgen_to_canonical("/media2") == "@disk2/"
    # unknown prefix -> best-effort stripped (unchanged behavior)
    assert subgen_to_canonical("/elsewhere/x.mkv") == "elsewhere/x.mkv"


def test_strip_arr_prefix_default_library_byte_identical(subarr_env):
    from subarr.paths import strip_arr_prefix

    # Single library: behaves exactly as today (no @head, default prefix).
    assert strip_arr_prefix("/data/Media/TV/Foo") == "TV/Foo"
    assert strip_arr_prefix(None) is None
    assert strip_arr_prefix("") == ""
    # Non-matching path passes through slash-stripped (today's behavior).
    assert strip_arr_prefix("/somewhere/else") == "somewhere/else"


def test_strip_arr_prefix_qualifies_non_default_library(two_libraries):
    from subarr.paths import strip_arr_prefix

    # /data/Media/ -> library 0 (no head); /data/d2/ -> @disk2
    assert strip_arr_prefix("/data/Media/TV/Foo") == "TV/Foo"
    assert strip_arr_prefix("/data/d2/Movies/film.mkv") == "@disk2/Movies/film.mkv"


def test_strip_arr_prefix_longest_match_wins(two_libraries):
    # If two prefixes both match, the longer one owns the path. Here only one
    # matches, but the test documents the longest-match rule.
    from subarr.paths import strip_arr_prefix

    assert strip_arr_prefix("/data/d2/x") == "@disk2/x"


def test_strip_arr_prefix_explicit_override_unchanged(subarr_env):
    # The explicit `prefix=` seam (Phase 0) keeps single-prefix, no-head
    # behavior — used by callers/tests that already know the prefix.
    from subarr.paths import strip_arr_prefix

    assert strip_arr_prefix("/custom/TV/Foo", prefix="/custom/") == "TV/Foo"
