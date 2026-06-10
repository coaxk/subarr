# #134 Phase 1 — Multi-Library Path Layer (Foundation: slices 1–2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-`media_root` assumption with a validated **list of libraries** and make `paths.py` library-aware via library-qualified canonicals (`@<slug>/relative/path`), with byte-identical behavior for existing single-library installs.

**Architecture:** Approach A from issue #134 — library identity travels *inside* the opaque canonical key as a reserved `@<slug>/` head. The default (legacy) library uses the **empty** slug, so every existing `"TV/Show/ep.mkv"` key stays valid and no DB backfill is needed. A new `libraries.py` module owns the `Library` model + validation; `config.load()` builds `settings.libraries` (legacy env → library 0, plus persisted extras); `paths.py`'s five translation functions resolve library identity from `settings.libraries` internally, so downstream call sites that pass canonicals through untouched stay untouched.

**Tech Stack:** Python 3.12, frozen dataclasses, stdlib only (no new deps), pytest (`PYTHONPATH=src`), ruff (pinned 0.15.15).

**Scope:** This plan covers **only the foundation** — slice 1 (config model) and slice 2 (paths). Walkers, the call-site sweep, onboarding/Settings UI, and live-DB verification (slices 3–6) are **re-planned after slice 2's `paths.py` API is reviewed and locked** (see "Deferred" at the end). This matches the handoff directive: get 1–2 reviewed before fanning out.

---

## Locked design decisions (from the planning session, 2026-06-11)

1. **Canonical id form = stable name slug** (`@disk2/…`), NOT a numeric index. Readable in DB/logs/API. Validated unique; **immutable once assigned** — the persisted library record stores its own `slug`, so renaming the human `name` never re-slugs and never orphans existing keys.
2. **Config mechanism = auto-derive + UI, with env back-compat.** Legacy single-library env vars (`SUBARR_MEDIA_ROOT` / `SUBGEN_MEDIA_PREFIX` / `ARR_PATH_PREFIX`) build **library 0** (empty slug) exactly as today. Additional libraries are persisted as a `libraries` JSON list in the existing `config_store` override file (`subarr-overrides.json`). Slices 3–6 add onboarding auto-detection (Phase-0 `root_folders()` endpoint) **and a manual "Add library" path in the Settings/onboarding UI** for roots auto-detect misses (explicit user request, 2026-06-11). No new env syntax in this phase.
3. **Byte-identical invariant.** With exactly one library (slug `""`), every `paths.py` function emits the exact output it does today. This is pinned by regression tests in slice 2 (the existing `tests/test_paths.py` cases must still pass unchanged).
4. **`sonarr_path_prefix` / `radarr_path_prefix` (#133) are dead config** — defined, consumed nowhere (verified 2026-06-10). They are **removed** in this phase (slice 1, Task 1.5); `libraries[]` per-library `arr_prefix` subsumes them.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/subarr/libraries.py` | `Library` frozen dataclass, `slugify()`, `LibraryConfigError`, `build_libraries()` (pure: default + extras → validated tuple). | **Create** |
| `src/subarr/config.py` | Add `libraries: tuple[Library, ...]` field to `Settings`; build it in `load()` (fail-soft); remove dead `sonarr_path_prefix`/`radarr_path_prefix`. | Modify |
| `src/subarr/paths.py` | Make all five translation fns library-aware via `settings.libraries`; add `_split_canonical()` + `_library_by_slug()` helpers. | Modify |
| `tests/test_libraries.py` | Unit tests for `slugify` + `build_libraries` (pure, no env). | **Create** |
| `tests/test_paths.py` | Existing single-library regression (must stay green) + new multi-library cases. | Modify |
| `tests/test_config_libraries.py` | `settings.libraries` built correctly from env (single) and overrides file (multi); fail-soft on bad config. | **Create** |

**Design boundary:** `libraries.py` is pure (no env, no IO, no `settings` import) so `build_libraries` is unit-testable in isolation. `config.py` owns the IO seam (reads `config_store` extras) and the fail-soft wrapper. `paths.py` is the only consumer that reads `settings.libraries`.

---

## Slice 1 — `Library` model + config parsing + back-compat derivation

No behavior change when one library exists. Pure-config slice.

### Task 1.1: Create `libraries.py` with `Library` + `slugify`

**Files:**
- Create: `src/subarr/libraries.py`
- Test: `tests/test_libraries.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_libraries.py
"""Unit tests for the multi-library config model (#134 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_slugify_basic():
    from subarr.libraries import slugify

    assert slugify("4K Movies") == "4k-movies"
    assert slugify("  Disk 2  ") == "disk-2"
    assert slugify("Anime/JP") == "anime-jp"
    assert slugify("TV___Shows") == "tv-shows"


def test_slugify_empty_when_no_alnum():
    from subarr.libraries import slugify

    assert slugify("///") == ""
    assert slugify("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_libraries.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.libraries'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/subarr/libraries.py
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
from dataclasses import dataclass
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_libraries.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/subarr/libraries.py tests/test_libraries.py
git commit -m "feat(#134): Library model + slugify (Phase 1 slice 1)"
```

### Task 1.2: `build_libraries` — back-compat single library

**Files:**
- Modify: `src/subarr/libraries.py`
- Test: `tests/test_libraries.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_libraries.py
def _default(tmp_path: Path):
    from subarr.libraries import Library

    return Library(
        slug="",
        name="default",
        fs_root=tmp_path / "media",
        subgen_prefix="/media",
        arr_prefix="/data/Media/",
    )


def test_build_libraries_single_is_just_default(tmp_path):
    from subarr.libraries import build_libraries

    libs = build_libraries(_default(tmp_path), [])
    assert len(libs) == 1
    assert libs[0].slug == ""
    assert libs[0].fs_root == tmp_path / "media"


def test_build_libraries_forces_default_slug_empty(tmp_path):
    from subarr.libraries import Library, build_libraries

    # Even if a caller hands a default with a non-empty slug, library 0 is "".
    d = Library(slug="ignored", name="x", fs_root=tmp_path, subgen_prefix="/media", arr_prefix="/data/")
    libs = build_libraries(d, [])
    assert libs[0].slug == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_libraries.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_libraries'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/subarr/libraries.py
from dataclasses import replace


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_libraries.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/subarr/libraries.py tests/test_libraries.py
git commit -m "feat(#134): build_libraries back-compat single-library derivation"
```

### Task 1.3: `build_libraries` — extras, slug rules, validation

**Files:**
- Modify: `tests/test_libraries.py` (no code change — Task 1.2's impl already covers these; this task pins the contract with tests and fixes any gaps found)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_libraries.py
def test_build_libraries_adds_extra_with_generated_slug(tmp_path):
    from subarr.libraries import build_libraries

    libs = build_libraries(
        _default(tmp_path),
        [{"name": "4K Movies", "fs_root": "/mnt/disk2/Movies4K", "arr_prefix": "/data/Movies4K/"}],
    )
    assert [l.slug for l in libs] == ["", "4k-movies"]
    assert libs[1].fs_root == Path("/mnt/disk2/Movies4K")
    # subgen_prefix defaults to the default library's when omitted.
    assert libs[1].subgen_prefix == "/media"


def test_build_libraries_respects_explicit_immutable_slug(tmp_path):
    from subarr.libraries import build_libraries

    # Persisted record carries its own slug; renaming `name` must NOT re-slug.
    libs = build_libraries(
        _default(tmp_path),
        [{"slug": "disk2", "name": "Renamed Later", "fs_root": "/m/d2", "arr_prefix": "/data/d2/"}],
    )
    assert libs[1].slug == "disk2"


def test_build_libraries_rejects_duplicate_slug(tmp_path):
    from subarr.libraries import LibraryConfigError, build_libraries

    with pytest.raises(LibraryConfigError, match="duplicate"):
        build_libraries(
            _default(tmp_path),
            [
                {"name": "Disk 2", "fs_root": "/a", "arr_prefix": "/data/a/"},
                {"slug": "disk-2", "name": "Other", "fs_root": "/b", "arr_prefix": "/data/b/"},
            ],
        )


def test_build_libraries_rejects_empty_slug_extra(tmp_path):
    from subarr.libraries import LibraryConfigError, build_libraries

    with pytest.raises(LibraryConfigError):
        build_libraries(_default(tmp_path), [{"name": "///", "fs_root": "/a", "arr_prefix": "/data/a/"}])


def test_build_libraries_requires_fs_root_and_arr_prefix(tmp_path):
    from subarr.libraries import LibraryConfigError, build_libraries

    with pytest.raises(LibraryConfigError, match="fs_root"):
        build_libraries(_default(tmp_path), [{"name": "X", "arr_prefix": "/data/x/"}])
    with pytest.raises(LibraryConfigError, match="arr_prefix"):
        build_libraries(_default(tmp_path), [{"name": "X", "fs_root": "/x"}])
```

- [ ] **Step 2: Run tests**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_libraries.py -q`
Expected: PASS (9 passed). If any fail, fix `build_libraries` (Task 1.2) until green — the impl above is written to satisfy these; this task is the contract pin.

- [ ] **Step 3: Commit**

```bash
git add tests/test_libraries.py
git commit -m "test(#134): pin build_libraries slug + validation contract"
```

### Task 1.4: Wire `settings.libraries` into `config.load()` (fail-soft)

**Files:**
- Modify: `src/subarr/config.py` (add field at end of `Settings`; build in `load()` before `return`)
- Test: `tests/test_config_libraries.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_libraries.py
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
            {
                "libraries": [
                    {"name": "Disk 2", "fs_root": "/mnt/d2/Movies", "arr_prefix": "/data/d2/"}
                ]
            }
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
    libs = config.settings.libraries
    assert [l.slug for l in libs] == ["", "disk-2"]
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_config_libraries.py -q`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'libraries'`

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/config.py`, add the import near the top:

```python
from .libraries import Library, LibraryConfigError, build_libraries
```

Add the field to the `Settings` dataclass (must have a default since it's set post-construct; place it as the LAST field, after `arena_retention_days`):

```python
    # #134 Phase 1: the validated library list. Library 0 (slug "") mirrors
    # the legacy media_root / subgen_media_prefix / arr_path_prefix scalars
    # for back-compat; additional libraries come from the persisted override
    # store. Built in load() after the scalars + overrides are resolved.
    # Tuple (not list) because Settings is frozen.
    libraries: tuple[Library, ...] = ()
```

In `load()`, immediately before `return _s` (after `_apply_persisted_overrides(_s)`), insert:

```python
    # #134 Phase 1: build the library list. Library 0 = the legacy scalars
    # (which _apply_persisted_overrides may have adjusted). Extras come from
    # the override store's "libraries" key. Fail-soft: any config error logs
    # and degrades to the single default library so boot never breaks.
    _default_lib = Library(
        slug="",
        name="default",
        fs_root=_s.media_root,
        subgen_prefix=_s.subgen_media_prefix,
        arr_prefix=_s.arr_path_prefix,
    )
    try:
        from . import config_store

        _raw_extras = config_store.load_overrides().get("libraries", [])
        if not isinstance(_raw_extras, list):
            _raw_extras = []
        _libs = build_libraries(_default_lib, _raw_extras)
    except LibraryConfigError:
        log.warning("invalid libraries[] config; using single default library", exc_info=True)
        _libs = (_default_lib,)
    object.__setattr__(_s, "libraries", _libs)
    return _s
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_config_libraries.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full config + libraries suite + ruff**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_config_libraries.py tests/test_libraries.py tests/test_config_extractor.py -q`
Then: `ruff check src/subarr/config.py src/subarr/libraries.py; ruff format src/subarr/config.py src/subarr/libraries.py`
Expected: tests PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/subarr/config.py tests/test_config_libraries.py
git commit -m "feat(#134): build settings.libraries in config.load (fail-soft)"
```

### Task 1.5: Remove dead `sonarr_path_prefix` / `radarr_path_prefix` (#133)

**Files:**
- Modify: `src/subarr/config.py` (remove 2 fields + their `load()` assignments)

- [ ] **Step 1: Confirm they are unconsumed**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -c "import subprocess"` then
Run: `rg -n "sonarr_path_prefix|radarr_path_prefix|SONARR_PATH_PREFIX|RADARR_PATH_PREFIX" src tests`
Expected: matches ONLY in `config.py` (definition + load). If any other consumer appears, STOP and wire it instead of removing.

- [ ] **Step 2: Remove the dataclass fields**

Delete these two lines (and their `# #133:` comment block above them) from `Settings`:

```python
    sonarr_path_prefix: str
    radarr_path_prefix: str
```

- [ ] **Step 3: Remove the `load()` assignments**

Delete this block from the `Settings(...)` constructor call in `load()`:

```python
        sonarr_path_prefix=_env_or(
            "SONARR_PATH_PREFIX",
            _env_or("ARR_PATH_PREFIX", "/data/TV/"),
        ),
        radarr_path_prefix=_env_or(
            "RADARR_PATH_PREFIX",
            _env_or("ARR_PATH_PREFIX", "/data/Movies/"),
        ),
```

- [ ] **Step 4: Run the full suite (catch any hidden reference)**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS (no `TypeError` about unexpected/missing kwarg, no `AttributeError`).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/config.py
git commit -m "refactor(#134): remove dead sonarr/radarr_path_prefix (#133 superseded by libraries[])"
```

---

## Slice 2 — `paths.py` library-aware

The load-bearing slice. Each function resolves library identity from `settings.libraries`. **Byte-identical** for a single library.

### Task 2.1: Canonical head helpers (`_split_canonical`, `_library_by_slug`)

**Files:**
- Modify: `src/subarr/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_paths.py
def test_split_canonical_default_and_qualified(subarr_env):
    from subarr.paths import _split_canonical

    assert _split_canonical("TV/Show/ep.mkv") == ("", "TV/Show/ep.mkv")
    assert _split_canonical("/TV/Show/") == ("", "TV/Show")
    assert _split_canonical("@disk2/Movies/x.mkv") == ("disk2", "Movies/x.mkv")
    assert _split_canonical("@disk2/") == ("disk2", "")
    assert _split_canonical("@disk2") == ("disk2", "")
    assert _split_canonical("") == ("", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_paths.py::test_split_canonical_default_and_qualified -q`
Expected: FAIL with `ImportError: cannot import name '_split_canonical'`

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/paths.py`, add the `Library` import and helpers below the existing imports / `PathOutsideRootError`:

```python
from .libraries import Library


def _split_canonical(canonical: str) -> tuple[str, str]:
    """Split a canonical into (slug, relative). A leading '@<slug>/' head
    yields that slug; otherwise slug is '' (the default library 0). The
    relative part is stripped of surrounding slashes.

    '@disk2/Movies/x' -> ('disk2', 'Movies/x')
    'TV/Show'         -> ('', 'TV/Show')
    '@disk2'          -> ('disk2', '')
    """
    s = (canonical or "").strip()
    if s.startswith("@"):
        head, _, rest = s[1:].partition("/")
        return head, rest.strip("/")
    return "", s.strip("/")


def _library_by_slug(slug: str) -> Library:
    """Resolve a library by slug. Unknown slug is a path-resolution error."""
    for lib in settings.libraries:
        if lib.slug == slug:
            return lib
    raise PathOutsideRootError(f"unknown library @{slug}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_paths.py::test_split_canonical_default_and_qualified -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/paths.py tests/test_paths.py
git commit -m "feat(#134): canonical @slug head helpers in paths.py"
```

### Task 2.2: `canonical_to_fs` / `fs_to_canonical` library-aware

**Files:**
- Modify: `src/subarr/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

This test injects a second library via the overrides-file pattern (same as `tests/test_config_libraries.py`).

```python
# append to tests/test_paths.py
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def two_libraries(subarr_env, monkeypatch, tmp_path):
    """Library 0 = the fixture media_root (empty slug); library 'disk2'
    rooted at a second tmp dir. Reloads config+paths so settings.libraries
    reflects both."""
    d2 = tmp_path / "disk2"
    (d2 / "Movies").mkdir(parents=True)
    (d2 / "Movies" / "film.mkv").write_bytes(b"")
    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps(
            {
                "libraries": [
                    {
                        "slug": "disk2",
                        "name": "Disk 2",
                        "fs_root": str(d2),
                        "subgen_prefix": "/media2",
                        "arr_prefix": "/data/d2/",
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config, paths

    importlib.reload(config)
    importlib.reload(paths)
    return d2


def test_canonical_to_fs_default_library(subarr_env):
    from subarr.config import settings
    from subarr.paths import canonical_to_fs

    # Byte-identical to today: library 0 resolves under media_root.
    assert canonical_to_fs("TV/Show") == (settings.media_root / "TV" / "Show").resolve()


def test_canonical_to_fs_qualified_library(two_libraries):
    from subarr.paths import canonical_to_fs

    assert canonical_to_fs("@disk2/Movies/film.mkv") == (two_libraries / "Movies" / "film.mkv").resolve()


def test_canonical_to_fs_traversal_guard_per_root(two_libraries):
    from subarr.paths import PathOutsideRootError, canonical_to_fs

    with pytest.raises(PathOutsideRootError):
        canonical_to_fs("@disk2/../escape")


def test_canonical_to_fs_unknown_library_raises(subarr_env):
    from subarr.paths import PathOutsideRootError, canonical_to_fs

    with pytest.raises(PathOutsideRootError):
        canonical_to_fs("@nope/x")


def test_fs_to_canonical_roundtrip_both_libraries(two_libraries):
    from subarr.config import settings
    from subarr.paths import canonical_to_fs, fs_to_canonical

    p0 = settings.media_root / "TV" / "Show" / "ep.mkv"
    assert fs_to_canonical(p0) == "TV/Show/ep.mkv"  # library 0: no @head
    p2 = two_libraries / "Movies" / "film.mkv"
    assert fs_to_canonical(p2) == "@disk2/Movies/film.mkv"
    # round-trips
    assert canonical_to_fs(fs_to_canonical(p2)) == p2.resolve()


def test_fs_to_canonical_outside_all_roots_raises(subarr_env, tmp_path):
    from subarr.paths import PathOutsideRootError, fs_to_canonical

    with pytest.raises(PathOutsideRootError):
        fs_to_canonical(tmp_path / "nowhere" / "x.mkv")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_paths.py -q -k "canonical_to_fs or fs_to_canonical"`
Expected: FAIL (qualified/roundtrip cases fail; `@disk2` not resolved).

- [ ] **Step 3: Write minimal implementation**

Replace the existing `canonical_to_fs` and `fs_to_canonical` bodies in `paths.py`:

```python
def canonical_to_fs(canonical: str) -> Path:
    """Resolve a canonical to an absolute filesystem path under its library's
    root, guarding against traversal. A leading '@<slug>/' selects the
    library; no head means library 0 (media_root). Empty relative part means
    the library root itself."""
    slug, rel_str = _split_canonical(canonical)
    rel = PurePosixPath(rel_str)
    if any(part == ".." for part in rel.parts):
        raise PathOutsideRootError(canonical)
    lib = _library_by_slug(slug)
    root = lib.fs_root.resolve()
    target = (root / Path(*rel.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise PathOutsideRootError(canonical) from e
    return target


def fs_to_canonical(p: Path) -> str:
    """Inverse of canonical_to_fs: find the owning library (longest-matching
    fs_root) and emit its canonical, prefixing '@<slug>/' for non-default
    libraries. Raises PathOutsideRootError (a ValueError subclass, preserving
    the previous raise-type contract) if p is under no library root."""
    rp = p.resolve()
    best: tuple[Path, PurePosixPath, Library] | None = None
    for lib in settings.libraries:
        root = lib.fs_root.resolve()
        try:
            rel = PurePosixPath(rp.relative_to(root).as_posix())
        except ValueError:
            continue
        if best is None or len(root.parts) > len(best[0].parts):
            best = (root, rel, lib)
    if best is None:
        raise PathOutsideRootError(str(p))
    _, rel, lib = best
    rel_posix = rel.as_posix()
    if lib.slug:
        return f"@{lib.slug}/{rel_posix}" if rel_posix != "." else f"@{lib.slug}/"
    return rel_posix
```

> Note: for library 0 the `rel_posix != "."` special-case is intentionally omitted to preserve today's exact output (`fs_to_canonical(media_root)` returned `"."` before and still does — no caller relies on it, and changing it would break byte-identity).

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_paths.py -q -k "canonical_to_fs or fs_to_canonical"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/paths.py tests/test_paths.py
git commit -m "feat(#134): canonical_to_fs / fs_to_canonical library-aware"
```

### Task 2.3: `canonical_to_subgen_batch` / `subgen_to_canonical` per-library prefix

**Files:**
- Modify: `src/subarr/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_paths.py
def test_canonical_to_subgen_batch_per_library(two_libraries):
    from subarr.paths import canonical_to_subgen_batch

    # library 0 keeps /media (existing regression tests still assert this)
    assert canonical_to_subgen_batch("TV/Foo") == "/media/TV/Foo"
    # library disk2 uses its own subgen prefix
    assert canonical_to_subgen_batch("@disk2/Movies/film.mkv") == "/media2/Movies/film.mkv"
    assert canonical_to_subgen_batch("@disk2/") == "/media2/"


def test_subgen_to_canonical_per_library(two_libraries):
    from subarr.paths import subgen_to_canonical

    assert subgen_to_canonical("/media/TV/Foo/ep.mkv") == "TV/Foo/ep.mkv"
    assert subgen_to_canonical("/media2/Movies/film.mkv") == "@disk2/Movies/film.mkv"
    assert subgen_to_canonical("/media2") == "@disk2/"
    # unknown prefix -> best-effort stripped (unchanged behavior)
    assert subgen_to_canonical("/elsewhere/x.mkv") == "elsewhere/x.mkv"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_paths.py -q -k "subgen"`
Expected: FAIL on the `@disk2` / `/media2` cases.

- [ ] **Step 3: Write minimal implementation**

Replace `canonical_to_subgen_batch` and `subgen_to_canonical` bodies:

```python
def canonical_to_subgen_batch(canonical: str) -> str:
    """Form subgen's /batch `directory` value for a canonical, using the
    owning library's subgen_prefix. Library 0 keeps the legacy prefix, so
    single-library output is byte-identical."""
    slug, rel = _split_canonical(canonical)
    lib = _library_by_slug(slug)
    prefix = lib.subgen_prefix.rstrip("/")
    if rel:
        return f"{prefix}/{rel}"
    return prefix + "/"


def subgen_to_canonical(subgen_path: str) -> str:
    """Inverse of canonical_to_subgen_batch: map a subgen-space absolute path
    back to a canonical, choosing the library whose subgen_prefix is the
    longest match and prefixing '@<slug>/' for non-default libraries. A path
    under no known prefix is returned slash-stripped (benign best-effort, as
    before)."""
    p = (subgen_path or "").strip()
    best: Library | None = None
    best_len = -1
    for lib in settings.libraries:
        prefix = lib.subgen_prefix.rstrip("/")
        if prefix and (p == prefix or p.startswith(prefix + "/")):
            if len(prefix) > best_len:
                best, best_len = lib, len(prefix)
    if best is None:
        return p.strip("/")
    prefix = best.subgen_prefix.rstrip("/")
    rel = "" if p == prefix else p[len(prefix) + 1 :].strip("/")
    if best.slug:
        return f"@{best.slug}/{rel}" if rel else f"@{best.slug}/"
    return rel
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_paths.py -q -k "subgen"`
Expected: PASS (incl. the pre-existing `test_canonical_to_subgen_batch_*` regression tests).

- [ ] **Step 5: Commit**

```bash
git add src/subarr/paths.py tests/test_paths.py
git commit -m "feat(#134): subgen batch translation per-library prefix"
```

### Task 2.4: `strip_arr_prefix` library-aware (longest-matching arr_prefix)

**Files:**
- Modify: `src/subarr/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_paths.py
def test_strip_arr_prefix_default_library_byte_identical(subarr_env):
    from subarr.paths import strip_arr_prefix

    # Single library: behaves exactly as today (no @head, default prefix).
    assert strip_arr_prefix("/data/Media/TV/Foo") == "TV/Foo"
    assert strip_arr_prefix(None) is None
    assert strip_arr_prefix("") == ""
    # Non-matching path passes through slash-stripped (today's behavior).
    assert strip_arr_prefix("/somewhere/else") == "somewhere/else"


def test_strip_arr_prefix_qualifies_non_default_library(two_libraries):
    from subarr.paths import strip_arr_prefix

    # /data/Media/ -> library 0 (no head); /data/d2/ -> @disk2
    assert strip_arr_prefix("/data/Media/TV/Foo") == "TV/Foo"
    assert strip_arr_prefix("/data/d2/Movies/film.mkv") == "@disk2/Movies/film.mkv"


def test_strip_arr_prefix_longest_match_wins(two_libraries):
    # If two prefixes both match, the longer one owns the path. Here only one
    # matches, but the test documents the longest-match rule.
    from subarr.paths import strip_arr_prefix

    assert strip_arr_prefix("/data/d2/x") == "@disk2/x"


def test_strip_arr_prefix_explicit_override_unchanged(subarr_env):
    # The explicit `prefix=` seam (Phase 0) keeps single-prefix, no-head
    # behavior — used by callers/tests that already know the prefix.
    from subarr.paths import strip_arr_prefix

    assert strip_arr_prefix("/custom/TV/Foo", prefix="/custom/") == "TV/Foo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_paths.py -q -k "strip_arr_prefix"`
Expected: FAIL on the `@disk2` qualification cases.

- [ ] **Step 3: Write minimal implementation**

Replace the `strip_arr_prefix` body:

```python
def strip_arr_prefix(arr_path: str | None, prefix: str | None = None) -> str | None:
    """Strip an *arr's container-view path prefix to canonical form.

    Two modes:
    - Explicit `prefix=` (Phase 0 seam): strip exactly that prefix, emit a
      library-0-namespace canonical (no '@head'). Back-compat / tests.
    - Library-aware (`prefix is None`): pick the library whose `arr_prefix`
      is the LONGEST match for `arr_path`, strip it, and prefix '@<slug>/'
      for non-default libraries. With a single library this is byte-identical
      to the old single-prefix strip. A path matching no library passes
      through slash-stripped (unchanged from before).

    Falsy input passes through unchanged (None -> None, '' -> '').
    """
    if not arr_path:
        return arr_path

    if prefix is not None:
        s = arr_path
        if prefix and s.startswith(prefix):
            s = s[len(prefix) :]
        return s.strip("/")

    best: Library | None = None
    best_len = -1
    for lib in settings.libraries:
        ap = lib.arr_prefix
        if ap and arr_path.startswith(ap) and len(ap) > best_len:
            best, best_len = lib, len(ap)
    if best is None:
        return arr_path.strip("/")
    rel = arr_path[best_len:].strip("/")
    if best.slug:
        return f"@{best.slug}/{rel}" if rel else f"@{best.slug}/"
    return rel
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/test_paths.py -q -k "strip_arr_prefix"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/paths.py tests/test_paths.py
git commit -m "feat(#134): strip_arr_prefix library-aware (longest arr_prefix match)"
```

### Task 2.5: Full-suite byte-identity gate + lint

**Files:** none (verification task)

- [ ] **Step 1: Run the entire backend suite**

Run: `$env:PYTHONPATH="C:\Projects\subarr\src"; python -m pytest tests/ -q --ignore=tests/e2e`
Expected: PASS, count ≥ the pre-Phase-1 baseline (793) + the new tests added here. The pre-existing `tests/test_paths.py` regression cases passing unchanged IS the byte-identity proof for single-library installs.

- [ ] **Step 2: Lint + format (CI parity)**

Run: `ruff check src/subarr tests; ruff format --check src/subarr/paths.py src/subarr/config.py src/subarr/libraries.py`
Expected: clean. If format flags, run `ruff format` on the listed files and re-commit.

- [ ] **Step 3: Bandit (B608 SQL hoist guard — no SQL added here, should be clean)**

Run: `bandit -ll -ii --skip B101 -r src/subarr/paths.py src/subarr/libraries.py src/subarr/config.py`
Expected: no issues.

- [ ] **Step 4: Commit any format fixes**

```bash
git add -A
git commit -m "chore(#134): ruff format pass for Phase 1 foundation"
```

---

## Self-Review (run against the slice 1–2 spec)

1. **Spec coverage** — Library model ✅ (1.1), back-compat single-library ✅ (1.2), extras + slug immutability + validation ✅ (1.3), `settings.libraries` wired fail-soft ✅ (1.4), dead #133 vars removed ✅ (1.5, satisfies acceptance bullet 4), all five path fns library-aware ✅ (2.1–2.4), byte-identity gate ✅ (2.5, satisfies acceptance bullet 2 for the path layer).
2. **Placeholder scan** — every code step shows complete code; every run step has an exact command + expected result. No TBD/"handle edge cases".
3. **Type consistency** — `Library(slug, name, fs_root: Path, subgen_prefix, arr_prefix)` used identically in `libraries.py`, the config default-library construction, and all `paths.py` consumers. `_split_canonical -> (str, str)`, `_library_by_slug -> Library` consistent across 2.1–2.4. `build_libraries(default, extras) -> tuple[Library, ...]` matches its config caller.

---

## Deferred to a follow-up plan (slices 3–6) — re-plan AFTER slice 2 review

These depend on the now-locked `paths.py` API and should be planned once it's merged. Concrete seams already identified:

- **Slice 3 — walkers / coverage partition.** `probe_walker.ProbeWalker._run` line ~132 does `root_fs = settings.media_root / state.root` — wrong under multi-library; must become `canonical_to_fs(state.root)` and the full-library walk must iterate **all** `settings.libraries` roots (probe-gate's eager walk already uses `canonical_to_fs`, so it's library-correct for free once 2.2 lands). `fs_to_canonical(p)` in `_one` already returns `@slug/...` correctly after 2.2.
- **Slice 4 — producers/consumers sweep (16 call sites).** Most Just Work via the opaque key. Verify each, especially the direct `settings.media_root / Path(canonical)` joins that bypass `canonical_to_fs`: `coverage_engine.py:275` and `:295` (`_subtitles_exist` / srt walk) — swap to `canonical_to_fs`. The `strip_arr_prefix` sites (`coverage_engine.py:1393,1432,1507,1709,1717,1736`, plus `scheduler`, `coverage_actions`, `arr_mediainfo`) now auto-qualify — confirm each receives an *arr `path` (not an already-canonical value).
- **Slice 5 — onboarding + Settings UI.** Surface `root_folders()` (Phase 0 endpoint), map each detected root to a library (fs_root + prefixes), persist to the `config_store` `libraries` key (writing back the assigned `slug` for immutability). **Plus a manual "Add library" form** (explicit requirement, 2026-06-11) for roots auto-detect misses, with per-library reachability validation reusing `/onboarding/probe-paths`. Respect the env-authoritative rule (#33) — env-set library 0 isn't overwritten.
- **Slice 6 — live-DB verification + docs.** Fresh DB + a copy of the live dev DB: confirm coverage counts match pre-refactor and the 6 canonical-keyed tables need no backfill. README + compose templates: multi-mount examples.

**Acceptance (full Phase 1, tracked on issue #134):** 2+ disjoint libraries walk→probe→cover→queue→transcribe→complete on :9923; single-library legacy install byte-identical; live dev DB loads with matching coverage counts.

## Process notes

- Board #6 `PVT_kwHOADDHj84BZfo7`, Status field `PVTSSF_lAHOADDHj84BZfo7zhUd5bo` (In Progress `47fc9ee4`). Move #134 → In Progress at build start.
- Each task above = its own commit; slices 1 and 2 should each be their own reviewed PR (slice 1 = config foundation, slice 2 = paths). Merge with `gh pr merge --squash --admin` once all five CI gates are green (ruff, pytest, bandit, zizmor, trivy). A fresh base-image CVE reds trivy on every open PR — fix once in the Dockerfile, update-branch the rest.
