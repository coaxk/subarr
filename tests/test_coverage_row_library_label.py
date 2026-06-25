"""#161 Phase 2 — coverage rows emit a library {slug, name} label."""

from __future__ import annotations


def test_to_dict_includes_library_label(anime_stack):
    from subarr.coverage_engine import CoverageItem

    item = CoverageItem(
        media_type="episode",
        title="Naruto",
        file_canonical_path="@anime/Naruto/S01E01.mkv",
    )
    d = item.to_dict()
    assert d["library"] == {"slug": "anime", "name": "Anime"}


def test_to_dict_library_default_lib_blank_slug(subarr_env):
    from subarr.coverage_engine import CoverageItem

    item = CoverageItem(
        media_type="episode",
        title="Show",
        file_canonical_path="Show/S01E01.mkv",  # no @slug head = library 0
    )
    d = item.to_dict()
    assert d["library"]["slug"] == ""


def test_to_dict_library_falls_back_to_canonical_path(subarr_env):
    from subarr.coverage_engine import CoverageItem

    # no file_canonical_path -> use canonical_path
    item = CoverageItem(media_type="movie", title="X", canonical_path="Movies/X")
    assert item.to_dict()["library"]["slug"] == ""
