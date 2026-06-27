"""#378: library_label(canonical) -> {slug, name} provenance label, shared by the
coverage rows and the Queue/Review/Aftercare/Activity surfaces."""

from __future__ import annotations

from pathlib import Path


def test_library_label_default_library(subarr_env):
    from subarr.paths import library_label

    label = library_label("Show/S01E01.mkv")  # no @slug head = library 0
    assert label["slug"] == ""
    assert isinstance(label["name"], str) and label["name"]


def test_library_label_failsoft_on_unknown_slug(subarr_env):
    from subarr.paths import library_label

    # unknown @slug must not raise — fail-soft to library 0 (no chip in the UI).
    assert library_label("@nope/x")["slug"] == ""


def test_library_label_resolves_non_default(subarr_env, monkeypatch):
    import subarr.config as config
    from subarr.libraries import Library
    from subarr.paths import library_label

    libs = (
        config.settings.libraries[0],
        Library(
            slug="anime",
            name="Anime",
            fs_root=Path("/anime"),
            subgen_prefix="/media",
            arr_prefix="/data/anime/",
        ),
    )
    old = config.settings.libraries
    object.__setattr__(config.settings, "libraries", libs)
    try:
        assert library_label("@anime/Frieren/S01E01.mkv") == {"slug": "anime", "name": "Anime"}
        # a non-anime path still resolves to the default library
        assert library_label("TV/Show/ep.mkv")["slug"] == ""
    finally:
        object.__setattr__(config.settings, "libraries", old)
