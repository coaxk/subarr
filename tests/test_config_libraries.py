"""settings.libraries is built from env (single) + overrides file (multi)."""

from __future__ import annotations

import importlib
import json
from pathlib import Path


def test_single_library_from_env(subarr_env):
    from subarr import config

    libs = config.settings.libraries
    assert len(libs) == 1
    assert libs[0].slug == ""
    assert libs[0].fs_root == config.settings.media_root
    assert libs[0].subgen_prefix == config.settings.subgen_media_prefix
    assert libs[0].arr_prefix == config.settings.arr_path_prefix


def test_extra_libraries_from_overrides_file(subarr_env, monkeypatch, tmp_path):
    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps(
            {"libraries": [{"name": "Disk 2", "fs_root": "/mnt/d2/Movies", "arr_prefix": "/data/d2/"}]}
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
    libs = config.settings.libraries
    assert [lib.slug for lib in libs] == ["", "disk-2"]
    assert libs[1].fs_root == Path("/mnt/d2/Movies")


def test_bad_libraries_falls_back_to_single(subarr_env, monkeypatch, tmp_path):
    # Duplicate slug -> LibraryConfigError -> fail-soft to single default.
    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps(
            {
                "libraries": [
                    {"slug": "x", "name": "A", "fs_root": "/a", "arr_prefix": "/data/a/"},
                    {"slug": "x", "name": "B", "fs_root": "/b", "arr_prefix": "/data/b/"},
                ]
            }
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config

    importlib.reload(config)
    assert len(config.settings.libraries) == 1  # boot survived; only default


def test_rebuild_libraries_picks_up_scalar_change(subarr_env):
    # #285: rebuild_libraries() re-derives libraries[0] from the CURRENT
    # scalars, so a runtime edit of arr_path_prefix/media_root isn't stale.
    from pathlib import Path

    from subarr import config

    s = config.settings
    object.__setattr__(s, "arr_path_prefix", "/data/Changed/")
    object.__setattr__(s, "media_root", Path("/mnt/newroot"))
    config.rebuild_libraries(s)
    assert s.libraries[0].arr_prefix == "/data/Changed/"
    assert s.libraries[0].fs_root == Path("/mnt/newroot")


def test_rebuild_libraries_preserves_extras(subarr_env, monkeypatch, tmp_path):
    # The rebuild must keep persisted extra libraries, not just reset to default.
    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps(
            {"libraries": [{"name": "Disk 2", "fs_root": "/mnt/d2/Movies", "arr_prefix": "/data/d2/"}]}
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    importlib.reload(__import__("subarr.config", fromlist=["x"]))
    from subarr import config

    object.__setattr__(config.settings, "arr_path_prefix", "/data/Changed/")
    config.rebuild_libraries(config.settings)
    libs = config.settings.libraries
    assert libs[0].arr_prefix == "/data/Changed/"
    assert [lib.slug for lib in libs] == ["", "disk-2"]


def test_library_binding_fields_default_empty(tmp_path):
    from subarr.libraries import Library

    lib = Library(slug="", name="d", fs_root=tmp_path, subgen_prefix="/media", arr_prefix="/data/")
    assert lib.sonarr_id == ""
    assert lib.radarr_id == ""
    assert lib.bazarr_id == ""


def test_build_libraries_passes_through_bindings(tmp_path):
    from subarr.libraries import Library, build_libraries

    default = Library(
        slug="", name="default", fs_root=tmp_path / "m", subgen_prefix="/media", arr_prefix="/data/tv/"
    )
    extras = [
        {
            "name": "Anime",
            "fs_root": str(tmp_path / "anime"),
            "arr_prefix": "/data/anime/",
            "sonarr_id": "anime",
            "bazarr_id": "anime",
        }
    ]
    libs = build_libraries(default, extras)
    anime = next(l for l in libs if l.slug == "anime")
    assert anime.sonarr_id == "anime"
    assert anime.radarr_id == ""
    assert anime.bazarr_id == "anime"
