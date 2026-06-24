"""Unit tests for the multi-library config model (#134 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_slugify_basic():
    from subarr.libraries import slugify

    assert slugify("4K Movies") == "4k-movies"
    assert slugify("  Disk 2  ") == "disk-2"
    assert slugify("Anime/JP") == "anime-jp"
    assert slugify("TV___Shows") == "tv-shows"


def test_slugify_empty_when_no_alnum():
    from subarr.libraries import slugify

    assert slugify("///") == ""
    assert slugify("") == ""


def _default(tmp_path: Path):
    from subarr.libraries import Library

    return Library(
        slug="",
        name="default",
        fs_root=tmp_path / "media",
        subgen_prefix="/media",
        arr_prefix="/data/Media/",
    )


def test_build_libraries_single_is_just_default(tmp_path):
    from subarr.libraries import build_libraries

    libs = build_libraries(_default(tmp_path), [])
    assert len(libs) == 1
    assert libs[0].slug == ""
    assert libs[0].fs_root == tmp_path / "media"


def test_build_libraries_forces_default_slug_empty(tmp_path):
    from subarr.libraries import Library, build_libraries

    # Even if a caller hands a default with a non-empty slug, library 0 is "".
    d = Library(slug="ignored", name="x", fs_root=tmp_path, subgen_prefix="/media", arr_prefix="/data/")
    libs = build_libraries(d, [])
    assert libs[0].slug == ""


def test_build_libraries_adds_extra_with_generated_slug(tmp_path):
    from subarr.libraries import build_libraries

    libs = build_libraries(
        _default(tmp_path),
        [{"name": "4K Movies", "fs_root": "/mnt/disk2/Movies4K", "arr_prefix": "/data/Movies4K/"}],
    )
    assert [lib.slug for lib in libs] == ["", "4k-movies"]
    assert libs[1].fs_root == Path("/mnt/disk2/Movies4K")
    # subgen_prefix defaults to the default library's when omitted.
    assert libs[1].subgen_prefix == "/media"


def test_build_libraries_respects_explicit_immutable_slug(tmp_path):
    from subarr.libraries import build_libraries

    # Persisted record carries its own slug; renaming `name` must NOT re-slug.
    libs = build_libraries(
        _default(tmp_path),
        [{"slug": "disk2", "name": "Renamed Later", "fs_root": "/m/d2", "arr_prefix": "/data/d2/"}],
    )
    assert libs[1].slug == "disk2"


def test_build_libraries_rejects_duplicate_slug(tmp_path):
    from subarr.libraries import LibraryConfigError, build_libraries

    with pytest.raises(LibraryConfigError, match="duplicate"):
        build_libraries(
            _default(tmp_path),
            [
                {"name": "Disk 2", "fs_root": "/a", "arr_prefix": "/data/a/"},
                {"slug": "disk-2", "name": "Other", "fs_root": "/b", "arr_prefix": "/data/b/"},
            ],
        )


def test_build_libraries_rejects_empty_slug_extra(tmp_path):
    from subarr.libraries import LibraryConfigError, build_libraries

    with pytest.raises(LibraryConfigError):
        build_libraries(_default(tmp_path), [{"name": "///", "fs_root": "/a", "arr_prefix": "/data/a/"}])


def test_build_libraries_requires_fs_root_and_arr_prefix(tmp_path):
    from subarr.libraries import LibraryConfigError, build_libraries

    with pytest.raises(LibraryConfigError, match="fs_root"):
        build_libraries(_default(tmp_path), [{"name": "X", "arr_prefix": "/data/x/"}])
    with pytest.raises(LibraryConfigError, match="arr_prefix"):
        build_libraries(_default(tmp_path), [{"name": "X", "fs_root": "/x"}])


def test_build_libraries_rejects_duplicate_arr_prefix():
    # #285 NIT: two libraries with the same arr_prefix make strip_arr_prefix's
    # longest-match assignment arbitrary — reject it (trailing-slash/backslash
    # normalized, matching the resolver).
    from subarr.libraries import Library, LibraryConfigError, build_libraries

    default = Library(
        slug="", name="default", fs_root=Path("/media"),
        subgen_prefix="/media", arr_prefix="/data/Media/",
    )
    # extra arr_prefix == default's, modulo trailing slash -> duplicate
    with pytest.raises(LibraryConfigError, match="duplicate"):
        build_libraries(default, [{"name": "Dup", "fs_root": "/mnt/d2", "arr_prefix": "/data/Media"}])
    # two extras colliding with each other
    with pytest.raises(LibraryConfigError, match="duplicate"):
        build_libraries(
            default,
            [
                {"name": "A", "fs_root": "/mnt/a", "arr_prefix": "/data/a/"},
                {"name": "B", "fs_root": "/mnt/b", "arr_prefix": "/data/a"},
            ],
        )


def test_build_libraries_distinct_arr_prefixes_ok():
    from subarr.libraries import Library, build_libraries

    default = Library(
        slug="", name="default", fs_root=Path("/media"),
        subgen_prefix="/media", arr_prefix="/data/Media/",
    )
    libs = build_libraries(default, [{"name": "Movies2", "fs_root": "/mnt/d2", "arr_prefix": "/data/Movies2/"}])
    assert [lib.slug for lib in libs] == ["", "movies2"]
