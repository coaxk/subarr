"""#134 slice 5: manage the multi-library list from the UI.

The default library (slug "") mirrors the legacy media_root scalars and is
managed through the existing settings/env surface — read-only here. PUT
replaces the EXTRAS list wholesale: validated via build_libraries (the same
gate config.load() uses), persisted to the config_store "libraries" key, and
applied to the running frozen Settings via object.__setattr__ (the
established live-reload pattern). No env var backs libraries, so the
onboarding clobber guard (#33) does not apply.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config_store
from ..config import settings
from ..libraries import Library, LibraryConfigError, build_libraries, slugify

router = APIRouter(prefix="/api/settings", tags=["libraries"])


class LibraryIn(BaseModel):
    slug: str | None = None  # present = existing entry (immutable); absent = new
    name: str
    fs_root: str
    subgen_prefix: str | None = None
    arr_prefix: str


class LibrariesPut(BaseModel):
    libraries: list[LibraryIn]


class ValidateRequest(BaseModel):
    fs_root: str


def _serialize(lib: Library, *, is_default: bool) -> dict:
    return {
        "slug": lib.slug,
        "name": lib.name,
        "fs_root": str(lib.fs_root),
        "subgen_prefix": lib.subgen_prefix,
        "arr_prefix": lib.arr_prefix,
        "is_default": is_default,
        "reachable": lib.fs_root.is_dir(),
    }


def _serialize_all(libs: tuple[Library, ...]) -> dict:
    return {"libraries": [_serialize(lib, is_default=(i == 0)) for i, lib in enumerate(libs)]}


@router.get("/libraries")
async def get_libraries() -> dict:
    return _serialize_all(settings.libraries)


@router.put("/libraries")
async def put_libraries(body: LibrariesPut) -> dict:
    extras: list[dict] = []
    for item in body.libraries:
        d = {
            # Slug immutability: an incoming slug is preserved verbatim; a
            # new entry (no slug) gets one derived from its name, fixed
            # forever after (renames never re-slug — keys are durable).
            "slug": (item.slug or "").strip() or slugify(item.name),
            "name": item.name,
            "fs_root": item.fs_root,
            "arr_prefix": item.arr_prefix,
        }
        if item.subgen_prefix:
            d["subgen_prefix"] = item.subgen_prefix
        extras.append(d)

    default = settings.libraries[0]
    try:
        libs = build_libraries(default, extras)
    except LibraryConfigError as e:
        raise HTTPException(422, detail=str(e))

    config_store.save_override("libraries", extras)
    object.__setattr__(settings, "libraries", libs)
    return _serialize_all(libs)


@router.post("/libraries/validate")
async def validate_library(body: ValidateRequest) -> dict:
    p = Path(body.fs_root)
    if not p.is_dir():
        return {"ok": False, "error": "not a directory or not reachable", "samples": [], "total": 0}
    try:
        entries = sorted(p.iterdir(), key=lambda e: e.name.lower())
    except OSError as e:
        return {"ok": False, "error": f"unreadable: {e}", "samples": [], "total": 0}
    samples = [("📁 " + e.name + "/") if e.is_dir() else ("📄 " + e.name) for e in entries[:5]]
    return {"ok": True, "error": None, "samples": samples, "total": len(entries)}
