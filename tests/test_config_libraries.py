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
