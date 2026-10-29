"""#638: the inventory walk records which libraries could not be read."""

from __future__ import annotations

import shutil

import pytest

from subarr import relink


@pytest.fixture(autouse=True)
def _fresh_cache():
    relink.invalidate_inventory()
    yield
    relink.invalidate_inventory()


def test_walk_lists_video_files_with_sizes(subarr_env, media_root):
    (media_root / "TV" / "Show" / "S1").mkdir()
    (media_root / "TV" / "Show" / "S1" / "e.mkv").write_bytes(b"12345")
    (media_root / "TV" / "Show" / "S1" / "e.en.srt").write_text("1\n")
    inv = relink.walk_inventory()
    assert inv.files["TV/Show/S1/e.mkv"] == 5
    assert "TV/Show/S1/e.en.srt" not in inv.files
    assert inv.files["TV/Show/ep.mkv"] == 0
    assert inv.failed_slugs == frozenset()
    assert inv.walk_s >= 0.0


def test_uppercase_extension_is_listed(subarr_env, media_root):
    (media_root / "TV" / "Show" / "UP.MKV").write_bytes(b"abc")
    assert relink.walk_inventory().files["TV/Show/UP.MKV"] == 3


def test_second_library_keys_carry_the_slug(two_libraries):
    (two_libraries / "Movies" / "film.mkv").write_bytes(b"1234")
    inv = relink.walk_inventory()
    assert inv.files["@disk2/Movies/film.mkv"] == 4
    assert inv.failed_slugs == frozenset()


def test_unreadable_root_marks_library_failed(two_libraries):
    shutil.rmtree(two_libraries)
    inv = relink.walk_inventory()
    assert "disk2" in inv.failed_slugs
    assert not [k for k in inv.files if k.startswith("@disk2/")]
    assert "TV/Show/ep.mkv" in inv.files  # the healthy library is still listed


def test_empty_readable_root_is_not_failed(subarr_env, media_root):
    shutil.rmtree(media_root / "TV")
    inv = relink.walk_inventory()
    assert inv.files == {}
    assert inv.failed_slugs == frozenset()


def test_walk_error_marks_library_failed(two_libraries, monkeypatch):
    """A mount that drops AFTER the root listed: os.walk reports it through
    onerror. The partial listing must not look complete."""
    real_walk = relink.os.walk

    def flaky(top, onerror=None, **kw):
        if str(top) == str(two_libraries):
            yield str(top), [], ["film.mkv"]
            onerror(OSError("mount dropped"))
            return
        yield from real_walk(top, onerror=onerror, **kw)

    monkeypatch.setattr(relink.os, "walk", flaky)
    inv = relink.walk_inventory()
    assert "disk2" in inv.failed_slugs
    assert not [k for k in inv.files if k.startswith("@disk2/")]
    assert "TV/Show/ep.mkv" in inv.files


def test_inventory_is_cached_and_invalidated(subarr_env, monkeypatch):
    calls = []
    real = relink.walk_inventory

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(relink, "walk_inventory", counting)
    a = relink.get_inventory()
    b = relink.get_inventory()
    assert a is b
    assert len(calls) == 1
    relink.invalidate_inventory()
    relink.get_inventory()
    assert len(calls) == 2


def test_cache_expires_by_age(subarr_env, monkeypatch):
    calls = []
    real = relink.walk_inventory
    monkeypatch.setattr(relink, "walk_inventory", lambda: (calls.append(1), real())[1])
    relink.get_inventory()
    relink.get_inventory(max_age_s=0.0)
    assert len(calls) == 2


def test_cache_keyed_by_library_config(subarr_env, monkeypatch, tmp_path):
    import dataclasses

    from subarr import config

    calls = []
    real = relink.walk_inventory
    monkeypatch.setattr(relink, "walk_inventory", lambda: (calls.append(1), real())[1])
    relink.get_inventory()
    other = tmp_path / "other"
    other.mkdir()
    lib = dataclasses.replace(config.settings.libraries[0], fs_root=other)
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, libraries=[lib]))
    relink.get_inventory()
    assert len(calls) == 2
