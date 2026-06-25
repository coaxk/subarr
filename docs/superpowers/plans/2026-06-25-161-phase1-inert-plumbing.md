# #161 Phase 1 — Inert Plumbing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the multi-instance data model, per-service client lists, and the `clients_for` resolver as *inert plumbing* — every behaviour byte-identical for single-stack installs, with no UI to add a second instance yet.

**Architecture:** Mirror #134's `libraries.py` pattern exactly. A new pure `instances.py` module models `Instance`; `config.py` seeds instance 0 from the existing env scalars and rebuilds on runtime edits; the arr/Bazarr clients gain optional explicit `(base_url, api_key)` defaulting to the scalars; `IntegrationBundle` becomes per-service dicts keyed by instance id with `bundle.sonarr/.radarr/.bazarr` retained as instance-0 alias properties so the ~52 read sites are untouched. A `clients_for(canonical)` resolver maps a row's `@slug` library to its bound instances, defaulting to instance 0 when bindings are empty.

**Tech Stack:** Python 3.11+, frozen dataclasses, pytest, httpx. Spec: `docs/superpowers/specs/2026-06-25-161-multi-instance-design.md`.

**Back-compat invariant (CI-enforced):** With no `instances` override configured, every binding id is `""`, `clients_for` returns instance 0, and `bundle.sonarr/.radarr/.bazarr` resolve to a client with base_url/headers identical to today. Task 8 locks this in.

---

## File Structure

- **Create:** `src/subarr/instances.py` — pure `Instance` model + `build_instances` (no env/IO/settings import), mirroring `libraries.py`.
- **Create:** `tests/test_instances.py` — unit tests for `build_instances`.
- **Modify:** `src/subarr/libraries.py` — add `sonarr_id`/`radarr_id`/`bazarr_id` binding fields to `Library` + passthrough in `build_libraries`.
- **Modify:** `src/subarr/config.py` — `Settings.instances` field, `rebuild_instances()`, `INSTANCE_DEFINING_FIELDS`, call in `load()`.
- **Modify:** `src/subarr/integrations/sonarr.py`, `radarr.py`, `bazarr.py` — constructors accept optional explicit `(base_url, api_key)`.
- **Modify:** `src/subarr/coverage_engine.py` — `IntegrationBundle` per-service dicts + alias properties + `client_for`; module-level `clients_for`.
- **Modify:** `src/subarr/paths.py` — public `library_for_canonical()` helper.
- **Modify/Create tests:** `tests/test_config_libraries.py` (binding fields), `tests/test_integration_bundle_multi.py` (new), `tests/test_clients_for.py` (new).

---

## Task 0: Test fixture for a second instance + bound library

The suite reads the import-time `config.settings` singleton; `coverage_engine.py`
does `from .config import settings` (a by-value binding). So tests can NOT
`monkeypatch.setattr(config, "settings", ...)` — that wouldn't change what the
bundle reads. The established pattern (conftest `two_libraries`) is: set
`SUBARR_CONFIG_STORE`, write the override JSON, then `importlib.reload(...)`.
`subarr_env` already reloads `config`, `paths`, `coverage_engine`, and the client
modules, so single-stack tests just use `subarr_env`. Multi-instance tests need a
fixture that also reloads after writing an `instances` override.

**Files:**
- Modify: `tests/conftest.py` (add the `anime_stack` fixture after `two_libraries`, ~line 76)

- [ ] **Step 1: Add the fixture (no test-first here — it is test infrastructure)**

```python
# tests/conftest.py  (add after the two_libraries fixture)
@pytest.fixture
def anime_stack(subarr_env, monkeypatch, tmp_path: Path):
    """A second Sonarr instance 'anime' plus a library 'anime' bound to it.
    Writes both override keys and reloads config/paths/coverage_engine so
    settings.instances, settings.libraries, and a freshly built IntegrationBundle
    all reflect them. Returns the anime library fs_root. (#161 Phase 1)"""
    import importlib
    import json

    anime_root = tmp_path / "anime"
    (anime_root / "Show").mkdir(parents=True)
    store = tmp_path / "ov.json"
    store.write_text(
        json.dumps(
            {
                "instances": {
                    "sonarr": [
                        {"name": "Anime", "url": "http://s2.test:8989", "api_key": "anime-key"}
                    ]
                },
                "libraries": [
                    {
                        "slug": "anime",
                        "name": "Anime",
                        "fs_root": str(anime_root),
                        "subgen_prefix": "/media",
                        "arr_prefix": "/data/anime/",
                        "sonarr_id": "anime",
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config, coverage_engine, paths

    importlib.reload(config)
    importlib.reload(paths)
    importlib.reload(coverage_engine)
    return anime_root
```

- [ ] **Step 2: Sanity-check the fixture loads (no assertion logic yet)**

Run: `pytest tests/test_libraries.py -q` (existing suite still imports conftest cleanly)
Expected: PASS — conftest imports without error.

- [ ] **Step 3: Commit**

```bash
ruff check tests/conftest.py && ruff format tests/conftest.py
git add tests/conftest.py
git commit -m "test(#161): anime_stack fixture (second instance + bound library)"
```

---

## Task 1: Instance model + `build_instances`

**Files:**
- Create: `src/subarr/instances.py`
- Test: `tests/test_instances.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instances.py
"""Unit tests for the multi-instance config model (#161 Phase 1)."""
from __future__ import annotations

import pytest


def _defaults():
    from subarr.instances import Instance

    return [
        Instance(id="", service="sonarr", name="default", url="http://sonarr:8989", api_key="k1"),
        Instance(id="", service="radarr", name="default", url="http://radarr:7878", api_key="k2"),
        Instance(id="", service="bazarr", name="default", url="http://bazarr:6767", api_key="k3"),
    ]


def test_build_instances_single_is_just_defaults():
    from subarr.instances import build_instances

    insts = build_instances(_defaults(), {})
    assert len(insts) == 3
    assert {i.service for i in insts} == {"sonarr", "radarr", "bazarr"}
    assert all(i.id == "" for i in insts)


def test_build_instances_forces_default_id_empty():
    from subarr.instances import Instance, build_instances

    bad_default = [Instance(id="nonempty", service="sonarr", name="d", url="u", api_key="k")]
    insts = build_instances(bad_default, {})
    assert insts[0].id == ""


def test_build_instances_adds_extra_with_slug_from_name():
    from subarr.instances import build_instances

    extras = {"sonarr": [{"name": "Anime", "url": "http://s2:8989", "api_key": "kk"}]}
    insts = build_instances(_defaults(), extras)
    sonarrs = [i for i in insts if i.service == "sonarr"]
    assert len(sonarrs) == 2
    extra = next(i for i in sonarrs if i.id != "")
    assert extra.id == "anime"
    assert extra.name == "Anime"
    assert extra.url == "http://s2:8989"


def test_build_instances_explicit_slug_preferred():
    from subarr.instances import build_instances

    extras = {"radarr": [{"slug": "fixed", "name": "Anime Films", "url": "u", "api_key": "k"}]}
    insts = build_instances(_defaults(), extras)
    assert any(i.id == "fixed" for i in insts if i.service == "radarr")


@pytest.mark.parametrize(
    "extras",
    [
        {"sonarr": [{"name": "", "url": "u", "api_key": "k"}]},          # missing name
        {"sonarr": [{"name": "x", "url": "", "api_key": "k"}]},          # missing url
        {"sonarr": [{"name": "x", "url": "u", "api_key": ""}]},          # missing api_key
        {"sonarr": [{"name": "///", "url": "u", "api_key": "k"}]},       # unslugifiable name
        {"sonarr": [{"slug": "", "name": "x", "url": "u", "api_key": "k"},
                    {"slug": "", "name": "x", "url": "u2", "api_key": "k2"}]},  # dup ids ("" twice via name x->x)
    ],
)
def test_build_instances_rejects_bad(extras):
    from subarr.instances import InstanceConfigError, build_instances

    with pytest.raises(InstanceConfigError):
        build_instances(_defaults(), extras)


def test_build_instances_ignores_unknown_service_key():
    from subarr.instances import build_instances

    insts = build_instances(_defaults(), {"plex": [{"name": "x", "url": "u", "api_key": "k"}]})
    assert len(insts) == 3  # plex is not a multi-instance service; key ignored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_instances.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'subarr.instances'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/subarr/instances.py
"""Multi-instance config model (#161 Phase 1).

An Instance = one Sonarr/Radarr/Bazarr container's credentials (url + api_key).
Instance identity is a short immutable slug; the default/legacy instance per
service uses the EMPTY slug (its credentials come from the env scalars), so
existing single-stack installs need no config and stay byte-identical.

Pure module: no env, no IO, no `settings` import — `build_instances` is a
deterministic function so it unit-tests in isolation. config.py owns the IO
seam (reads persisted extras) and the fail-soft load wrapper. Mirrors
libraries.py by design.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .libraries import slugify  # reuse the exact kebab-case rule

MULTI_INSTANCE_SERVICES = ("sonarr", "radarr", "bazarr")


@dataclass(frozen=True)
class Instance:
    id: str        # "" = default/legacy instance (env-backed); else unique kebab slug within its service
    service: str    # "sonarr" | "radarr" | "bazarr"
    name: str       # human-facing label
    url: str
    api_key: str


class InstanceConfigError(ValueError):
    """Invalid instances config (dup/empty id, missing fields, ...)."""


def build_instances(defaults: list[Instance], extras: dict) -> tuple[Instance, ...]:
    """Assemble the validated instance tuple.

    `defaults` is the per-service instance 0 list (their ids are FORCED to "").
    `extras` is the persisted override dict: {service: [{name, url, api_key,
    slug?}, ...]}. Unknown service keys are ignored. Raises InstanceConfigError
    on any invalid/duplicate extra — config.load() catches this and falls back
    to defaults only (fail-soft boot).
    """
    out: list[Instance] = []
    seen: dict[str, set[str]] = {svc: {""} for svc in MULTI_INSTANCE_SERVICES}

    for d in defaults:
        out.append(replace(d, id=""))

    for svc in MULTI_INSTANCE_SERVICES:
        raw_list = extras.get(svc, []) if isinstance(extras, dict) else []
        if not isinstance(raw_list, list):
            raise InstanceConfigError(f"{svc} instances is not a list: {raw_list!r}")
        for i, raw in enumerate(raw_list):
            if not isinstance(raw, dict):
                raise InstanceConfigError(f"{svc}[{i}] is not an object: {raw!r}")
            name = str(raw.get("name", "")).strip()
            url = str(raw.get("url", "")).strip()
            api_key = str(raw.get("api_key", "")).strip()
            if not name:
                raise InstanceConfigError(f"{svc}[{i}] missing 'name'")
            if not url:
                raise InstanceConfigError(f"{svc} instance {name!r} missing 'url'")
            if not api_key:
                raise InstanceConfigError(f"{svc} instance {name!r} missing 'api_key'")
            iid = str(raw.get("slug", "")).strip() or slugify(name)
            if not iid:
                raise InstanceConfigError(f"{svc} instance {name!r} has no usable id")
            if iid in seen[svc]:
                raise InstanceConfigError(f"duplicate {svc} instance id {iid!r}")
            seen[svc].add(iid)
            out.append(Instance(id=iid, service=svc, name=name, url=url, api_key=api_key))

    return tuple(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_instances.py -v`
Expected: PASS (all cases)

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/instances.py tests/test_instances.py && ruff format src/subarr/instances.py tests/test_instances.py
git add src/subarr/instances.py tests/test_instances.py
git commit -m "feat(#161): instances.py multi-instance config model (phase 1)"
```

---

## Task 2: Library binding fields

**Files:**
- Modify: `src/subarr/libraries.py:30-37` (the `Library` dataclass) and `src/subarr/libraries.py:94-102` (the `out.append(Library(...))` block)
- Test: `tests/test_config_libraries.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_libraries.py  (append)
def test_library_binding_fields_default_empty(tmp_path):
    from subarr.libraries import Library

    lib = Library(slug="", name="d", fs_root=tmp_path, subgen_prefix="/media", arr_prefix="/data/")
    assert lib.sonarr_id == ""
    assert lib.radarr_id == ""
    assert lib.bazarr_id == ""


def test_build_libraries_passes_through_bindings(tmp_path):
    from subarr.libraries import Library, build_libraries

    default = Library(slug="", name="default", fs_root=tmp_path / "m", subgen_prefix="/media", arr_prefix="/data/tv/")
    extras = [{
        "name": "Anime", "fs_root": str(tmp_path / "anime"), "arr_prefix": "/data/anime/",
        "sonarr_id": "anime", "bazarr_id": "anime",
    }]
    libs = build_libraries(default, extras)
    anime = next(l for l in libs if l.slug == "anime")
    assert anime.sonarr_id == "anime"
    assert anime.radarr_id == ""   # unset stays empty
    assert anime.bazarr_id == "anime"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_libraries.py -k binding -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'sonarr_id'`

- [ ] **Step 3: Write minimal implementation**

In `src/subarr/libraries.py`, add three fields to the `Library` dataclass (after `arr_prefix`):

```python
@dataclass(frozen=True)
class Library:
    slug: str  # "" = default/legacy library 0; else unique kebab slug
    name: str  # human-facing label
    fs_root: Path  # subarr's filesystem view of this library's root
    subgen_prefix: str  # subgen-space absolute prefix (e.g. "/media")
    arr_prefix: str  # *arr container path prefix (e.g. "/data/Media/")
    # #161 Phase 1: which instance serves this library. "" = instance 0
    # (env-backed default). A library uses one media-manager (Sonarr OR Radarr,
    # by content type) + one Bazarr. Inert in Phase 1 — no UI sets these yet.
    sonarr_id: str = ""
    radarr_id: str = ""
    bazarr_id: str = ""
```

In `build_libraries`, update the `out.append(Library(...))` call to pass the bindings:

```python
        out.append(
            Library(
                slug=slug,
                name=name,
                fs_root=Path(fs_root),
                subgen_prefix=subgen_prefix,
                arr_prefix=arr_prefix,
                sonarr_id=str(raw.get("sonarr_id", "")).strip(),
                radarr_id=str(raw.get("radarr_id", "")).strip(),
                bazarr_id=str(raw.get("bazarr_id", "")).strip(),
            )
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_libraries.py tests/test_libraries.py -v`
Expected: PASS (new binding tests pass; existing library tests still green — fields are defaulted so `replace(default, slug="")` and lib0 construction are unaffected)

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/libraries.py tests/test_config_libraries.py && ruff format src/subarr/libraries.py tests/test_config_libraries.py
git add src/subarr/libraries.py tests/test_config_libraries.py
git commit -m "feat(#161): library->instance binding fields (phase 1, inert)"
```

---

## Task 3: config.py — seed + rebuild instances

**Files:**
- Modify: `src/subarr/config.py` — add `instances` field to `Settings` (near `libraries`, ~line 235); add `INSTANCE_DEFINING_FIELDS` + `rebuild_instances()` (after `rebuild_libraries`, ~line 354); call `rebuild_instances(_s)` in `load()` (~line 314).
- Test: `tests/test_config_store.py` (append) — uses the existing override-store fixtures.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_store.py  (append)
def test_rebuild_instances_seeds_instance0_from_scalars(subarr_env):
    from subarr import config

    s = config.settings  # the reloaded singleton (subarr_env seeded it from env)
    sonarrs = [i for i in s.instances if i.service == "sonarr"]
    inst0 = next(i for i in sonarrs if i.id == "")
    assert inst0.url == s.sonarr_url     # "http://sonarr.test:8989" from subarr_env
    assert inst0.api_key == s.sonarr_api_key


def test_rebuild_instances_picks_up_extras(subarr_env, monkeypatch, tmp_path):
    import importlib
    import json

    store = tmp_path / "ov.json"
    store.write_text(json.dumps({
        "instances": {"sonarr": [{"name": "Anime", "url": "http://s2:8989", "api_key": "kk"}]}
    }))
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config

    importlib.reload(config)
    ids = {i.id for i in config.settings.instances if i.service == "sonarr"}
    assert "" in ids and "anime" in ids


def test_rebuild_instances_failsoft_on_bad_config(subarr_env, monkeypatch, tmp_path):
    import importlib
    import json

    store = tmp_path / "ov.json"
    store.write_text(json.dumps({
        "instances": {"sonarr": [{"name": "x", "url": "u"}]}  # missing api_key
    }))
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(store))
    from subarr import config

    importlib.reload(config)  # must not raise
    # degrades to defaults-only (the 3 instance-0 entries)
    assert len([i for i in config.settings.instances if i.service == "sonarr"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_store.py -k instances -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'instances'`

- [ ] **Step 3: Write minimal implementation**

Add the import near the top of `config.py` (alongside the libraries import at line 14):

```python
from .instances import Instance, InstanceConfigError, build_instances
```

Add the field to `Settings` (immediately after the `libraries` field at line 235):

```python
    # #161 Phase 1: validated instance list (flat tuple; each Instance carries
    # its .service). Per-service instance 0 (id "") mirrors the legacy
    # sonarr_url/api_key/... scalars for back-compat; extras come from the
    # override store's "instances" key. Built in load() after scalars+overrides.
    instances: tuple[Instance, ...] = ()
```

Add the call in `load()` immediately after `rebuild_libraries(_s)` (line 314):

```python
    rebuild_libraries(_s)
    rebuild_instances(_s)
    return _s
```

Add `INSTANCE_DEFINING_FIELDS` + `rebuild_instances` after `rebuild_libraries` (after line 353):

```python
# Scalars that define each service's instance 0. A runtime edit of any of these
# must trigger rebuild_instances() so credential changes take effect live (#161,
# mirrors LIBRARY_DEFINING_FIELDS / #285).
INSTANCE_DEFINING_FIELDS = (
    "sonarr_url", "sonarr_api_key",
    "radarr_url", "radarr_api_key",
    "bazarr_url", "bazarr_api_key",
)


def rebuild_instances(s: Settings) -> None:
    """(Re)build ``s.instances`` from the current scalar config + persisted
    extras. Instance 0 per service = the legacy scalars; extras come from the
    override store's ``instances`` key. Fail-soft: any config error logs and
    degrades to the per-service defaults so this never breaks boot."""
    from . import config_store

    defaults = [
        Instance(id="", service="sonarr", name="default", url=s.sonarr_url, api_key=s.sonarr_api_key),
        Instance(id="", service="radarr", name="default", url=s.radarr_url, api_key=s.radarr_api_key),
        Instance(id="", service="bazarr", name="default", url=s.bazarr_url, api_key=s.bazarr_api_key),
    ]
    try:
        raw_extras = config_store.load_overrides().get("instances", {})
        if not isinstance(raw_extras, dict):
            raw_extras = {}
        insts = build_instances(defaults, raw_extras)
    except InstanceConfigError:
        log.warning("invalid instances config; using per-service defaults", exc_info=True)
        insts = tuple(defaults)
    object.__setattr__(s, "instances", insts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_store.py -k instances -v`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/config.py tests/test_config_store.py && ruff format src/subarr/config.py tests/test_config_store.py
git add src/subarr/config.py tests/test_config_store.py
git commit -m "feat(#161): seed + rebuild instances in config (phase 1)"
```

---

## Task 4: Arr/Bazarr clients accept explicit credentials

**Files:**
- Modify: `src/subarr/integrations/sonarr.py:20-24`, `src/subarr/integrations/radarr.py:17-21`, `src/subarr/integrations/bazarr.py:25-28`
- Test: `tests/test_integration_credentials.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_integration_credentials.py  (append)
def test_sonarr_client_explicit_credentials():
    from subarr.integrations.sonarr import SonarrClient

    c = SonarrClient(base_url="http://s2:8989", api_key="explicitkey")
    assert c._base_url == "http://s2:8989"
    assert c._client.headers["X-Api-Key"] == "explicitkey"


def test_sonarr_client_defaults_to_settings(subarr_env):
    # Settings is a frozen dataclass — do NOT mutate it. subarr_env seeds the
    # scalars from env; assert the no-arg client mirrors them (today's behaviour).
    from subarr import config
    from subarr.integrations.sonarr import SonarrClient

    c = SonarrClient()  # no args = today's behaviour
    assert c._base_url == config.settings.sonarr_url        # "http://sonarr.test:8989"
    assert c._client.headers["X-Api-Key"] == config.settings.sonarr_api_key


def test_bazarr_client_explicit_uses_caps_header():
    from subarr.integrations.bazarr import BazarrClient

    c = BazarrClient(base_url="http://b2:6767", api_key="bk")
    # Bazarr header is X-API-KEY (caps), NOT X-Api-Key
    assert c._client.headers["X-API-KEY"] == "bk"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_integration_credentials.py -k "explicit or defaults_to_settings" -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'base_url'`

- [ ] **Step 3: Write minimal implementation**

`src/subarr/integrations/sonarr.py` — replace the constructor:

```python
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        url = settings.sonarr_url if base_url is None else base_url
        key = settings.sonarr_api_key if api_key is None else api_key
        super().__init__(
            base_url=url if key else "",
            headers={"X-Api-Key": key} if key else None,
        )
```

`src/subarr/integrations/radarr.py` — replace the constructor:

```python
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        url = settings.radarr_url if base_url is None else base_url
        key = settings.radarr_api_key if api_key is None else api_key
        super().__init__(
            base_url=url if key else "",
            headers={"X-Api-Key": key} if key else None,
        )
```

`src/subarr/integrations/bazarr.py` — replace the constructor (note the CAPS header):

```python
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        url = settings.bazarr_url if base_url is None else base_url
        key = settings.bazarr_api_key if api_key is None else api_key
        super().__init__(
            base_url=url if key else "",
            headers={"X-API-KEY": key} if key else None,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_integration_credentials.py -v`
Expected: PASS (new tests + all existing credential tests still green — default-None path is byte-identical to the old `settings.*` reads)

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/integrations/sonarr.py src/subarr/integrations/radarr.py src/subarr/integrations/bazarr.py tests/test_integration_credentials.py && ruff format src/subarr/integrations/sonarr.py src/subarr/integrations/radarr.py src/subarr/integrations/bazarr.py tests/test_integration_credentials.py
git add src/subarr/integrations/sonarr.py src/subarr/integrations/radarr.py src/subarr/integrations/bazarr.py tests/test_integration_credentials.py
git commit -m "feat(#161): arr/bazarr clients accept explicit credentials (phase 1)"
```

---

## Task 5: IntegrationBundle per-service dicts + instance-0 alias

**Files:**
- Modify: `src/subarr/coverage_engine.py:227-257` (the `IntegrationBundle` class)
- Test: `tests/test_integration_bundle_multi.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_integration_bundle_multi.py
"""IntegrationBundle multi-instance dicts + instance-0 alias (#161 Phase 1)."""
from __future__ import annotations


def test_bundle_alias_resolves_instance0(subarr_env):
    # single-stack: settings.instances has only the 3 instance-0 defaults
    from subarr.coverage_engine import IntegrationBundle

    bundle = IntegrationBundle()
    assert bundle.sonarr is bundle.client_for("sonarr", "")
    assert bundle.radarr is bundle.client_for("radarr", "")
    assert bundle.bazarr is bundle.client_for("bazarr", "")


def test_bundle_client_for_unknown_id_falls_back_to_instance0(subarr_env):
    from subarr.coverage_engine import IntegrationBundle

    bundle = IntegrationBundle()
    # an unbound/empty id and an unknown id both resolve to instance 0
    assert bundle.client_for("sonarr", "") is bundle.sonarr
    assert bundle.client_for("sonarr", "doesnotexist") is bundle.sonarr


def test_bundle_builds_extra_instance(anime_stack):
    # anime_stack reloaded coverage_engine after writing the override store
    from subarr.coverage_engine import IntegrationBundle

    bundle = IntegrationBundle()
    anime = bundle.client_for("sonarr", "anime")
    assert anime is not bundle.sonarr
    assert anime._base_url == "http://s2.test:8989"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_integration_bundle_multi.py -v`
Expected: FAIL with `AttributeError: 'IntegrationBundle' object has no attribute 'client_for'`

- [ ] **Step 3: Write minimal implementation**

Replace the `IntegrationBundle` class body (`coverage_engine.py:227-257`):

```python
class IntegrationBundle:
    """Holds the integration clients + close-all helper. App lifespan owns one
    instance. #161: the arr/bazarr trio is keyed per instance id; tautulli/plex
    stay singleton. `bundle.sonarr/.radarr/.bazarr` alias instance 0 so the many
    existing read sites keep working unchanged."""

    _ARR_SERVICES = ("sonarr", "radarr", "bazarr")

    def __init__(self):
        # SonarrClient/RadarrClient/BazarrClient are already imported at module
        # scope (coverage_engine.py:30-32) — reuse them, do not re-import locally.
        ctors = {"sonarr": SonarrClient, "radarr": RadarrClient, "bazarr": BazarrClient}
        self._clients: dict[str, dict[str, object]] = {svc: {} for svc in self._ARR_SERVICES}
        for inst in settings.instances:
            if inst.service not in self._clients:
                continue
            self._clients[inst.service][inst.id] = ctors[inst.service](
                base_url=inst.url, api_key=inst.api_key
            )
        # Defensive: guarantee an instance 0 per service even if settings.instances
        # was never rebuilt (keeps the alias properties total). Uses env defaults.
        # Guard with `in` (NOT setdefault) — setdefault would eagerly construct a
        # throwaway client every time, leaking an unclosed httpx.AsyncClient.
        for svc in self._ARR_SERVICES:
            if "" not in self._clients[svc]:
                self._clients[svc][""] = ctors[svc]()

        self.tautulli = TautulliClient()
        from .integrations.plex import PlexClient

        self.plex = PlexClient(
            base_url=settings.plex_url,
            token=settings.plex_token,
            default_section=settings.plex_section,
            path_prefix=settings.plex_path_prefix,
            media_root=str(settings.media_root),
        )

    def client_for(self, service: str, instance_id: str | None):
        """Resolve a client by (service, instance id). Empty/unknown id falls
        back to instance 0 — the single-stack invariant."""
        pool = self._clients[service]
        return pool.get(instance_id or "") or pool[""]

    @property
    def sonarr(self):
        return self._clients["sonarr"][""]

    @property
    def radarr(self):
        return self._clients["radarr"][""]

    @property
    def bazarr(self):
        return self._clients["bazarr"][""]

    async def aclose(self) -> None:
        closers = [c.aclose() for pool in self._clients.values() for c in pool.values()]
        closers.append(self.tautulli.aclose())
        closers.append(self.plex.aclose())
        await asyncio.gather(*closers, return_exceptions=True)
```

Note: the module-level `TautulliClient` import (coverage_engine.py:33) is still used
(singleton). The three arr/bazarr client imports stay used too (the `ctors` map
references them). No import removals expected — but run `ruff check` in Step 5 and
act on anything it reports.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_integration_bundle_multi.py tests/test_integration_versions.py tests/test_integrations_health_probe.py -v`
Expected: PASS (new bundle tests + existing bundle consumers still green)

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/coverage_engine.py tests/test_integration_bundle_multi.py && ruff format src/subarr/coverage_engine.py tests/test_integration_bundle_multi.py
git add src/subarr/coverage_engine.py tests/test_integration_bundle_multi.py
git commit -m "feat(#161): IntegrationBundle per-service dicts + instance-0 alias (phase 1)"
```

---

## Task 6: `library_for_canonical` public helper

**Files:**
- Modify: `src/subarr/paths.py` (add the helper after `_library_by_slug`, ~line 56)
- Test: `tests/test_paths.py` if it exists, else `tests/test_clients_for.py` covers it in Task 7. Create `tests/test_paths_library_for.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths_library_for.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paths_library_for.py -v`
Expected: FAIL with `ImportError: cannot import name 'library_for_canonical'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/subarr/paths.py` after `_library_by_slug` (~line 56):

```python
def library_for_canonical(canonical: str) -> Library:
    """Public, fail-soft resolver: a canonical's '@<slug>/' head -> its Library.
    Unknown/empty slug returns library 0 (the default) rather than raising —
    callers (clients_for, #161) must degrade to instance 0, never crash a row.
    """
    slug, _rel = _split_canonical(canonical)
    try:
        return _library_by_slug(slug)
    except PathOutsideRootError:
        return _library_by_slug("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_paths_library_for.py -v`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/paths.py tests/test_paths_library_for.py && ruff format src/subarr/paths.py tests/test_paths_library_for.py
git add src/subarr/paths.py tests/test_paths_library_for.py
git commit -m "feat(#161): library_for_canonical fail-soft helper (phase 1)"
```

---

## Task 7: `clients_for` resolver

**Files:**
- Modify: `src/subarr/coverage_engine.py` (add module-level `clients_for` + the `ResolvedClients` namedtuple after `IntegrationBundle`)
- Test: `tests/test_clients_for.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_clients_for.py
"""clients_for maps a row's @slug library to its bound instance clients (#161)."""
from __future__ import annotations


def test_clients_for_empty_bindings_resolve_instance0(subarr_env):
    from subarr.coverage_engine import IntegrationBundle, clients_for

    bundle = IntegrationBundle()
    rc = clients_for(bundle, "Show/S01E01.mkv")  # library 0, all bindings ""
    assert rc.sonarr is bundle.sonarr
    assert rc.radarr is bundle.radarr
    assert rc.bazarr is bundle.bazarr


def test_clients_for_bound_library_routes_to_instance(anime_stack):
    # anime_stack: library 'anime' bound sonarr_id='anime'; bazarr_id unset
    from subarr.coverage_engine import IntegrationBundle, clients_for

    bundle = IntegrationBundle()
    rc = clients_for(bundle, "@anime/Naruto/S01E01.mkv")
    assert rc.sonarr is bundle.client_for("sonarr", "anime")
    assert rc.bazarr is bundle.bazarr  # bazarr_id unset -> instance 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_clients_for.py -v`
Expected: FAIL with `ImportError: cannot import name 'clients_for'`

- [ ] **Step 3: Write minimal implementation**

Extend the existing paths import at `coverage_engine.py:28` (do not add a second
`from .paths import` line):

```python
from .paths import UNSUPPORTED_EXTS, canonical_to_fs, library_for_canonical, strip_arr_prefix
```

Add after the `IntegrationBundle` class:

```python
from typing import NamedTuple


class ResolvedClients(NamedTuple):
    sonarr: object
    radarr: object
    bazarr: object


def clients_for(bundle: "IntegrationBundle", canonical: str) -> ResolvedClients:
    """Resolve the (sonarr, radarr, bazarr) clients that own a row's library.
    The row's '@slug' head -> Library -> bound instance ids -> clients. Empty
    bindings resolve to instance 0, so single-stack is byte-identical. The
    caller picks sonarr vs radarr by content type (#161 Phase 2+ wiring)."""
    lib = library_for_canonical(canonical)
    return ResolvedClients(
        sonarr=bundle.client_for("sonarr", lib.sonarr_id),
        radarr=bundle.client_for("radarr", lib.radarr_id),
        bazarr=bundle.client_for("bazarr", lib.bazarr_id),
    )
```

(If `ruff` prefers the `NamedTuple`/`typing` import at the top of the file rather than inline, move it there — match the file's existing import grouping.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_clients_for.py -v`
Expected: PASS

- [ ] **Step 5: Lint + commit**

```bash
ruff check src/subarr/coverage_engine.py tests/test_clients_for.py && ruff format src/subarr/coverage_engine.py tests/test_clients_for.py
git add src/subarr/coverage_engine.py tests/test_clients_for.py
git commit -m "feat(#161): clients_for resolver (phase 1, inert)"
```

---

## Task 8: Back-compat characterization test (CI-enforced invariant)

**Files:**
- Test: `tests/test_multi_instance_backcompat.py` (create) — the byte-identical single-stack guard.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_multi_instance_backcompat.py
"""#161 single-stack byte-identical invariant: with no `instances`/`libraries`
overrides, the multi-instance plumbing must behave exactly like instance 0.
This is the CI guard that single-stack installs (the majority) are unaffected.
"""
from __future__ import annotations


def test_single_stack_bundle_matches_direct_construction(subarr_env):
    from subarr.coverage_engine import IntegrationBundle
    from subarr.integrations.sonarr import SonarrClient

    bundle = IntegrationBundle()
    direct = SonarrClient()  # the pre-#161 construction path

    assert bundle.sonarr._base_url == direct._base_url
    assert dict(bundle.sonarr._client.headers).get("X-Api-Key") == \
        dict(direct._client.headers).get("X-Api-Key")


def test_single_stack_clients_for_is_always_instance0(subarr_env):
    from subarr.coverage_engine import IntegrationBundle, clients_for

    bundle = IntegrationBundle()
    for canonical in ["", "Show/S01E01.mkv", "@unknownlib/x", "Movie (2020)/m.mkv"]:
        rc = clients_for(bundle, canonical)
        assert rc.sonarr is bundle.sonarr
        assert rc.radarr is bundle.radarr
        assert rc.bazarr is bundle.bazarr


def test_single_stack_has_exactly_one_instance_per_service(subarr_env):
    from subarr import config

    for svc in ("sonarr", "radarr", "bazarr"):
        assert len([i for i in config.settings.instances if i.service == svc]) == 1
```

- [ ] **Step 2: Run test to verify it fails (then passes)**

Run: `pytest tests/test_multi_instance_backcompat.py -v`
Expected: PASS immediately if Tasks 1-7 are complete (this task adds no new production code — it *locks in* the invariant). If any assertion fails, a prior task regressed single-stack behaviour — fix the offending task before continuing.

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: PASS — full suite green (was 1318 passing at handoff; expect 1318 + the new Phase 1 tests). Investigate any failure; do not proceed with reds.

- [ ] **Step 4: Run all three lint gates**

Run: `ruff check src tests && ruff format --check src tests && bandit -q -r src`
Expected: all clean (per the handoff: local-verify all three gates before pushing).

- [ ] **Step 5: Commit**

```bash
git add tests/test_multi_instance_backcompat.py
git commit -m "test(#161): single-stack byte-identical back-compat guard (phase 1)"
```

---

## Done criteria for Phase 1

- `instances.py` model + `build_instances` (Task 1).
- Library binding fields, inert (Task 2).
- `Settings.instances` seeded + rebuilt, fail-soft (Task 3).
- Arr/Bazarr clients take explicit creds, default to scalars (Task 4).
- `IntegrationBundle` per-service dicts + instance-0 alias + `client_for` (Task 5).
- `library_for_canonical` fail-soft helper (Task 6).
- `clients_for` resolver, inert (Task 7).
- Back-compat characterization guard green; full suite + all three lint gates green (Task 8).
- **No UI to add a second instance** (Phase 4) and **no call site rewired to `clients_for`** (Phases 2-3) — Phase 1 ships inert.

## Out of scope (later phases — do NOT build here)

- Bazarr wanted-list merge over N instances + instance tagging (Phase 2).
- Dedup re-keying to `(instance_id, id)` (Phase 2).
- Wiring the ~14 writeback sites + `_bazarr_sync_task_id` per-instance cache to `clients_for` (Phase 3).
- Settings ▸ Instances / Libraries binding UI, root-folder auto-enumerate, resolved-topology (Phase 4).
- Wizard "add another stack" branch (Phase 5).
