"""#365: warn (don't fail) when a library binds to an unconfigured arr instance."""

from __future__ import annotations

from pathlib import Path


def _lib(slug, **ids):
    from subarr.libraries import Library

    return Library(
        slug=slug,
        name=slug or "default",
        fs_root=Path(f"/{slug or 'd'}"),
        subgen_prefix="/media",
        arr_prefix=f"/data/{slug or ''}",
        **ids,
    )


def _inst(iid, service):
    from subarr.instances import Instance

    return Instance(id=iid, service=service, name=iid or "default", url="http://x", api_key="k")


def test_dangling_binding_warns():
    from subarr.config import validate_library_bindings

    libs = (_lib(""), _lib("anime", sonarr_id="ghost", bazarr_id="anime"))
    insts = (_inst("", "sonarr"), _inst("anime", "bazarr"))
    warnings = validate_library_bindings(libs, insts)
    assert len(warnings) == 1
    assert "ghost" in warnings[0] and "sonarr" in warnings[0]


def test_valid_bindings_no_warning():
    from subarr.config import validate_library_bindings

    libs = (_lib(""), _lib("anime", sonarr_id="anime", bazarr_id="anime"))
    insts = (_inst("", "sonarr"), _inst("anime", "sonarr"), _inst("anime", "bazarr"))
    assert validate_library_bindings(libs, insts) == []
