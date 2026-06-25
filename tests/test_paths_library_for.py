"""library_for_canonical resolves a canonical's @slug to its Library (#161)."""

from __future__ import annotations


def test_library_for_canonical_default_library(subarr_env):
    from subarr.paths import library_for_canonical

    lib = library_for_canonical("Show/S01E01.mkv")  # no @slug head = library 0
    assert lib.slug == ""


def test_library_for_canonical_unknown_slug_failsoft_to_library0(subarr_env):
    from subarr.paths import library_for_canonical

    # unknown @slug must not raise — Phase 1 resolver is fail-soft to library 0
    lib = library_for_canonical("@nope/x")
    assert lib.slug == ""
