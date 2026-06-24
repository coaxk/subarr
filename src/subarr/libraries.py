"""Multi-library config model (#134 Phase 1, approach A).

A library = one media location with its own filesystem root (subarr's view),
subgen-space prefix, and *arr container path prefix. Library identity is a
short slug carried INSIDE the opaque canonical key as a reserved `@<slug>/`
head (the default/legacy library uses the EMPTY slug, so existing canonicals
stay valid and need no backfill).

Pure module: no env, no IO, no `settings` import — `build_libraries` is a
deterministic function so it unit-tests in isolation. config.py owns the IO
seam (reads persisted extras) and the fail-soft load wrapper.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Kebab-case slug: lowercase, non-alphanumeric runs → single '-',
    trimmed. Returns '' when the name has no alphanumeric content (caller
    must treat '' as invalid for a non-default library)."""
    return _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")


@dataclass(frozen=True)
class Library:
    slug: str  # "" = default/legacy library 0; else unique kebab slug
    name: str  # human-facing label
    fs_root: Path  # subarr's filesystem view of this library's root
    subgen_prefix: str  # subgen-space absolute prefix (e.g. "/media")
    arr_prefix: str  # *arr container path prefix (e.g. "/data/Media/")


class LibraryConfigError(ValueError):
    """Invalid libraries[] config (dup/empty slug, missing fields, ...)."""


def build_libraries(default: Library, extras: list[dict]) -> tuple[Library, ...]:
    """Assemble the validated library tuple.

    `default` is library 0 (its slug is FORCED to "" regardless of input).
    `extras` is a list of dicts from the persisted override store, each:
        {name: str, fs_root: str, arr_prefix: str,
         subgen_prefix?: str, slug?: str}
    - slug: explicit value (durable/immutable) if present, else slugify(name).
      Must be non-empty and unique; "" is reserved for the default library.
    - subgen_prefix defaults to the default library's (common single-NAS mount).
    Raises LibraryConfigError on any invalid/duplicate entry — config.load()
    catches this and falls back to the single default library (fail-soft boot).
    """
    lib0 = replace(default, slug="")
    out: list[Library] = [lib0]
    seen: set[str] = {""}

    # #285 NIT: arr_prefix must be unique across libraries — two identical
    # prefixes make strip_arr_prefix's longest-match assignment arbitrary (a
    # real #161 multi-instance footgun). Normalize like the resolver (backslash
    # + trailing slash) before comparing.
    def _norm_ap(ap: str) -> str:
        return ap.replace("\\", "/").rstrip("/")

    seen_arr: set[str] = set()
    if default.arr_prefix:
        seen_arr.add(_norm_ap(default.arr_prefix))

    for i, raw in enumerate(extras):
        if not isinstance(raw, dict):
            raise LibraryConfigError(f"library[{i}] is not an object: {raw!r}")
        name = str(raw.get("name", "")).strip()
        fs_root = str(raw.get("fs_root", "")).strip()
        arr_prefix = str(raw.get("arr_prefix", "")).strip()
        if not name:
            raise LibraryConfigError(f"library[{i}] missing 'name'")
        if not fs_root:
            raise LibraryConfigError(f"library {name!r} missing 'fs_root'")
        if not arr_prefix:
            raise LibraryConfigError(f"library {name!r} missing 'arr_prefix'")
        slug = str(raw.get("slug", "")).strip() or slugify(name)
        if not slug:
            raise LibraryConfigError(f"library {name!r} has no usable slug")
        if slug in seen:
            raise LibraryConfigError(f"duplicate library slug {slug!r}")
        seen.add(slug)
        ap_norm = _norm_ap(arr_prefix)
        if ap_norm in seen_arr:
            raise LibraryConfigError(f"library {name!r} has a duplicate arr_prefix {arr_prefix!r}")
        seen_arr.add(ap_norm)
        subgen_prefix = str(raw.get("subgen_prefix", "")).strip() or lib0.subgen_prefix
        out.append(
            Library(
                slug=slug,
                name=name,
                fs_root=Path(fs_root),
                subgen_prefix=subgen_prefix,
                arr_prefix=arr_prefix,
            )
        )
    return tuple(out)
