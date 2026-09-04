"""#483: a config-store read blip must not silently delete your libraries.

`config_store.load_overrides()` is fail-soft and returns `{}` when the overrides
file is present but unreadable. `rebuild_libraries` then does:

    raw_extras = config_store.load_overrides().get("libraries", [])   # -> []
    libs = build_libraries(default_lib, [])                           # -> (lib0,)

so EVERY non-default library disappears for that load, and every canonical
produced in that window loses its `@slug` head. It self-heals on the next
successful read, which is what made this so hard to pin down.

Reported by AztecGuyGDL. The decisive evidence was that the bad canonical was
TRANSIENT: the same episode recorded `@tv/...` before, `tv/...` in one scan, and
`@tv/...` again afterwards. A duplicate root or a mis-scoped walk would have
been consistently wrong.

The fail-soft itself is CORRECT and does not change here. It exists so a bad
read never becomes a file wipe, and it still does that. The defect is that
`rebuild_libraries` cannot tell "no libraries are configured" from "we could not
read what is configured", and treats the second as the first.

Why it failed SILENTLY rather than loudly, which took two wrong theories to
understand: the reporter's `/media/library/tv` and `/data/tv_shows` are aliases
of the same content, so with the library set degraded the file was still
reachable under library 0's root and produced a plausible-looking `tv/...`.
Without the alias, `fs_to_canonical` would have raised PathOutsideRootError and
the scan would have failed visibly.
"""

from __future__ import annotations

import importlib
import json

import pytest


def _write_store(tmp_path, monkeypatch, fs_root):
    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps(
            {
                "libraries": [
                    {
                        "slug": "tv",
                        "name": "TV",
                        "fs_root": str(fs_root),
                        "subgen_prefix": "/media/tv",
                        "arr_prefix": "/tv/",
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    return store


class TestReadResultDistinguishesAbsentFromUnreadable:
    def test_absent_file_is_a_successful_read_of_nothing(self, tmp_path, monkeypatch):
        # A user with no extra libraries is not a failure. This has to stay
        # distinguishable or the fix below would refuse to ever rebuild.
        from subarr import config_store

        monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "absent.json"))
        data, ok = config_store.load_overrides_result()
        assert data == {}
        assert ok is True

    def test_present_but_unreadable_is_a_failed_read(self, tmp_path, monkeypatch):
        from subarr import config_store

        p = tmp_path / "ov.json"
        p.write_text(json.dumps({"libraries": []}))
        monkeypatch.setenv("SUBARR_CONFIG_STORE", str(p))

        def _boom(self, *a, **k):
            raise OSError("transient")

        monkeypatch.setattr("pathlib.Path.read_text", _boom)
        data, ok = config_store.load_overrides_result()
        assert ok is False, "an unreadable file must not look like an empty one"

    def test_a_good_read_returns_data_and_ok(self, tmp_path, monkeypatch):
        from subarr import config_store

        _write_store(tmp_path, monkeypatch, tmp_path / "tv")
        data, ok = config_store.load_overrides_result()
        assert ok is True
        assert data["libraries"][0]["slug"] == "tv"

    def test_the_old_helper_still_swallows_failures(self, tmp_path, monkeypatch):
        # load_overrides() has other callers and must keep its contract.
        from subarr import config_store

        monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "absent.json"))
        assert config_store.load_overrides() == {}


class TestLibrariesSurviveAReadBlip:
    def _load(self, tmp_path, monkeypatch):
        _write_store(tmp_path, monkeypatch, tmp_path / "tv")
        monkeypatch.setenv("SUBARR_MEDIA_ROOT", str(tmp_path / "media"))
        from subarr import config

        importlib.reload(config)
        return config

    def test_the_tv_library_is_there_to_begin_with(self, tmp_path, monkeypatch):
        config = self._load(tmp_path, monkeypatch)
        assert {lib.slug for lib in config.settings.libraries} == {"", "tv"}

    def test_a_failed_read_does_not_drop_configured_libraries(self, tmp_path, monkeypatch):
        # THE BUG. Before the fix this collapsed to {""} and every canonical
        # built during that window silently lost its @slug.
        config = self._load(tmp_path, monkeypatch)
        from subarr import config_store

        monkeypatch.setattr(config_store, "load_overrides_result", lambda: ({}, False))
        config.rebuild_libraries(config.settings)

        assert {lib.slug for lib in config.settings.libraries} == {"", "tv"}, (
            "a transient config read blip deleted the configured libraries"
        )

    def test_a_successful_read_of_nothing_still_collapses_to_default(self, tmp_path, monkeypatch):
        # The other direction. Genuinely removing a library must still work, or
        # this fix would make library config un-editable.
        config = self._load(tmp_path, monkeypatch)
        from subarr import config_store

        monkeypatch.setattr(config_store, "load_overrides_result", lambda: ({}, True))
        config.rebuild_libraries(config.settings)
        assert {lib.slug for lib in config.settings.libraries} == {""}


class TestCanonicalsStaySlugged:
    """The user-visible property. Everything above is machinery for this one."""

    def test_a_canonical_keeps_its_slug_across_a_failed_rebuild(self, tmp_path, monkeypatch):
        media = tmp_path / "media"
        tv = tmp_path / "tv"
        (tv / "Show" / "Season 01").mkdir(parents=True)
        f = tv / "Show" / "Season 01" / "ep.mkv"
        f.write_bytes(b"")
        media.mkdir()

        _write_store(tmp_path, monkeypatch, tv)
        monkeypatch.setenv("SUBARR_MEDIA_ROOT", str(media))
        from subarr import config, config_store, paths

        importlib.reload(config)
        importlib.reload(paths)
        assert paths.fs_to_canonical(f).startswith("@tv/")

        monkeypatch.setattr(config_store, "load_overrides_result", lambda: ({}, False))
        config.rebuild_libraries(config.settings)

        assert paths.fs_to_canonical(f).startswith("@tv/"), (
            "the canonical lost its library after a config read blip"
        )


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
