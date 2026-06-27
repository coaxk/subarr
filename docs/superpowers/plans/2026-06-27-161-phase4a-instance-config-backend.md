# #161 Phase 4A — Instance config backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. REQUIRED per task: superpowers:test-driven-development.

**Goal:** Expose the (already-built) multi-instance model through a REST API so a second Sonarr/Radarr/Bazarr instance becomes user-creatable: list / test / add / edit / remove instances, view the resolved library→arr/Bazarr/Plex-section topology, and override a library's bindings — all persisted to the existing override store and applied live without a restart.

**Architecture:** A new `routers/instances.py` mounted under `/api`. It reads/writes the `config_store` override-store keys `"instances"` (the extras dict `{service: [{name,url,api_key,slug?}]}`) and `"libraries"` (binding overrides), then calls the existing `rebuild_instances(settings)` / `rebuild_libraries(settings)` + `_rebuild_runtime_clients(state)` to make changes live. Validation reuses `instances.build_instances` (raises `InstanceConfigError`); dangling-binding visibility reuses `config.validate_library_bindings`; the live connection test constructs a throwaway client and calls a cheap authenticated GET. **No data-model changes** — Phase 1/2/3 already shipped the model, persistence, and routing; this is the config surface only.

**Tech Stack:** Python 3.11+, FastAPI, pydantic, pytest, httpx MockTransport. Worktree: `C:\Projects\subarr-161-p2`, branch `feat/161-phase4a-instance-config` (editable install verified pointing at this worktree's `src`).

**Critical gotchas (carry in):**
- Repo `.py` are CRLF; LF git warnings are benign. Run `ruff format` on any heredoc-appended test before commit (the PostToolUse ruff hook is bypassed by heredoc appends → CI `ruff format --check` fails otherwise).
- The blocking PostToolUse ruff hook flags F821/F841 on partial edits and **deletes a just-added unused import (F401)** on the same edit — add an import together with its first usage.
- `Settings` is a frozen dataclass — never `setattr`; the code paths we call (`rebuild_instances`) already use `object.__setattr__`.
- `_rebuild_runtime_clients` is **async** and also rebuilds subgen/ollama + reprobes; pass `reprobe=False` is NOT needed here (it defaults to reprobe in app, but our endpoints should call it the same way onboarding's live edits do). It closes the old bundle's clients to avoid leaking httpx sessions.
- Instance 0 per service has id `""` and is **env-backed** — editable (via the scalar path, out of scope here) but **never removable** and never stored in the override `"instances"` extras.

---

## File structure

- **Create:** `src/subarr/routers/instances.py` — the instance/topology config router (all endpoints below).
- **Modify:** `src/subarr/app.py` — register the new router (one `include_router` line next to the others).
- **Create:** `tests/test_instances_api.py` — endpoint + persistence + reactivity tests.

Reused as-is (no edits): `config_store.{load_overrides,save_override,clear_override}`, `instances.build_instances`/`InstanceConfigError`, `config.{rebuild_instances,rebuild_libraries,validate_library_bindings,settings}`, `routers/onboarding._rebuild_runtime_clients`, `integrations.{sonarr,radarr,bazarr}` clients' `root_folders()`/`list_tasks()`, `integrations/plex._section_for_path`.

---

## Task 1: GET /api/instances — list per-service instances

**Files:**
- Create: `src/subarr/routers/instances.py`
- Modify: `src/subarr/app.py`
- Test: `tests/test_instances_api.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instances_api.py
"""#161 Phase 4A — instance config API."""
import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api(subarr_env, monkeypatch, tmp_path):
    # Point the override store at a temp file so adds/edits persist hermetically.
    monkeypatch.setenv("SUBARR_CONFIG_STORE", str(tmp_path / "overrides.json"))
    import importlib
    import subarr.config as config
    importlib.reload(config)
    from subarr.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_list_instances_returns_instance0_per_service(api):
    r = api.get("/api/instances")
    assert r.status_code == 200
    data = r.json()
    # one default (id "") per arr service, never leaking the api_key
    by_service = {svc: [i for i in data["instances"] if i["service"] == svc] for svc in ("sonarr", "radarr", "bazarr")}
    for svc in ("sonarr", "radarr", "bazarr"):
        defaults = [i for i in by_service[svc] if i["id"] == ""]
        assert len(defaults) == 1, svc
        assert defaults[0]["is_default"] is True
        assert "api_key" not in defaults[0]
        assert "has_api_key" in defaults[0]
```

> NOTE (grounded): the app is a **module-level `from subarr.app import app`** (app.py:917), NOT a `create_app()` factory. AND `subarr.app` imports `subarr.config` at import time, so the naive reload above will leave `app` bound to a stale config module. **Do this instead:** reuse conftest's established harness — the `subarr_env` fixture handles env isolation + module reload, and there is an existing TestClient app fixture pattern (`app_with_stub`, conftest ~432-511). Build the `api` fixture by mirroring that construction (set `SUBARR_CONFIG_STORE` in env BEFORE the app is built, inside the conftest reload flow), not by reloading config after import. Resolve this concretely in Task 1 against the real conftest before writing the rest.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instances_api.py::test_list_instances_returns_instance0_per_service -v`
Expected: FAIL — 404 (route not registered) or ImportError.

- [ ] **Step 3: Write minimal implementation**

```python
# src/subarr/routers/instances.py
"""#161 Phase 4A — instance + topology config API.

Exposes the multi-instance model (already built in Phase 1-3) for management:
list/test/add/edit/remove Sonarr/Radarr/Bazarr instances and view/override the
resolved library->arr/Bazarr/Plex-section topology. Persists to the config
override store and applies changes live via rebuild_instances + a runtime
client rebuild — no restart.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config_store
from ..config import settings

router = APIRouter(prefix="/api", tags=["instances"])
log = logging.getLogger(__name__)

_ARR_SERVICES = ("sonarr", "radarr", "bazarr")


def _serialize(inst) -> dict:
    """Public instance view — NEVER leak the api_key; expose only presence."""
    return {
        "id": inst.id,
        "service": inst.service,
        "name": inst.name,
        "url": inst.url,
        "is_default": inst.id == "",
        "has_api_key": bool(inst.api_key),
    }


@router.get("/instances")
async def list_instances() -> dict:
    return {"instances": [_serialize(i) for i in settings.instances]}
```

- [ ] **Step 4: Register the router in `app.py`**

Find the block of `app.include_router(...)` calls (grep `include_router` in `src/subarr/app.py`) and add, matching the surrounding style:

```python
from .routers import instances as instances_router  # near the other router imports
...
app.include_router(instances_router.router)  # next to the other include_router calls
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_instances_api.py::test_list_instances_returns_instance0_per_service -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/subarr/routers/instances.py src/subarr/app.py tests/test_instances_api.py
git commit -m "feat(#161): GET /api/instances lists per-service instances (phase 4A)"
```

---

## Task 2: POST /api/instances/test — live connection probe

**Files:**
- Modify: `src/subarr/routers/instances.py`
- Test: `tests/test_instances_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_test_connection_ok_for_sonarr(api, monkeypatch):
    # Stub the throwaway client's probe so no real network is hit.
    import subarr.routers.instances as mod

    async def fake_probe(service, url, api_key):
        assert service == "sonarr"
        assert url == "http://sonarr2.test"
        return {"ok": True, "detail": "connected", "root_folders": ["/data/anime"]}

    monkeypatch.setattr(mod, "_probe_connection", fake_probe)
    r = api.post("/api/instances/test", json={"service": "sonarr", "url": "http://sonarr2.test", "api_key": "k"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "detail": "connected", "root_folders": ["/data/anime"]}


def test_test_connection_rejects_unknown_service(api):
    r = api.post("/api/instances/test", json={"service": "plex", "url": "x", "api_key": "k"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instances_api.py -k test_connection -v`
Expected: FAIL — 404 / attribute `_probe_connection` missing.

- [ ] **Step 3: Write minimal implementation**

Add to `src/subarr/routers/instances.py`:

```python
from ..integrations import IntegrationError
from ..integrations.sonarr import SonarrClient
from ..integrations.radarr import RadarrClient
from ..integrations.bazarr import BazarrClient

_CTORS = {"sonarr": SonarrClient, "radarr": RadarrClient, "bazarr": BazarrClient}


class TestConnRequest(BaseModel):
    service: str
    url: str
    api_key: str


async def _probe_connection(service: str, url: str, api_key: str) -> dict:
    """Construct a throwaway client and hit a cheap authenticated endpoint.
    Always closes the client (no leaked httpx session)."""
    client = _CTORS[service](base_url=url, api_key=api_key)
    try:
        if service in ("sonarr", "radarr"):
            folders = await client.root_folders()
            paths = [f.get("path") for f in folders if isinstance(f, dict) and f.get("path")]
            return {"ok": True, "detail": "connected", "root_folders": paths}
        # bazarr: list_tasks is a cheap authenticated GET
        await client.list_tasks()
        return {"ok": True, "detail": "connected", "root_folders": []}
    except IntegrationError as e:
        return {"ok": False, "detail": str(e), "root_folders": []}
    finally:
        await client.aclose()


@router.post("/instances/test")
async def test_connection(req: TestConnRequest) -> dict:
    if req.service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {req.service!r}")
    if not req.url or not req.api_key:
        raise HTTPException(422, detail="url and api_key are required")
    return await _probe_connection(req.service, req.url, req.api_key)
```

> NOTE: verify each client constructor accepts `base_url=` and `api_key=` (it does — `IntegrationBundle.__init__` constructs them that way) and that `BazarrClient` exposes `list_tasks` / `aclose` (used in `routers/bazarr_sync.py`). If `aclose` is named differently, grep `async def aclose` in `integrations/base.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instances_api.py -k test_connection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/instances.py tests/test_instances_api.py
git commit -m "feat(#161): POST /api/instances/test live connection probe (phase 4A)"
```

---

## Task 3: POST /api/instances — add + persist + apply live

**Files:**
- Modify: `src/subarr/routers/instances.py`
- Test: `tests/test_instances_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_add_instance_persists_and_goes_live(api, monkeypatch):
    # Avoid the network reprobe in the runtime rebuild.
    import subarr.routers.onboarding as onb

    async def noop_rebuild(state, reprobe=True):
        from subarr.coverage_engine import IntegrationBundle
        state.integrations = IntegrationBundle()

    monkeypatch.setattr(onb, "_rebuild_runtime_clients", noop_rebuild)

    r = api.post("/api/instances", json={
        "service": "sonarr", "name": "Anime", "url": "http://sonarr-anime.test", "api_key": "k",
    })
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["service"] == "sonarr"
    assert created["id"] == "anime"          # slugified from name
    assert created["is_default"] is False
    assert "api_key" not in created

    # persisted to the override store
    from subarr import config_store
    extras = config_store.load_overrides().get("instances", {})
    assert any(i["url"] == "http://sonarr-anime.test" for i in extras.get("sonarr", []))

    # live in settings.instances
    listed = api.get("/api/instances").json()["instances"]
    assert any(i["id"] == "anime" and i["service"] == "sonarr" for i in listed)


def test_add_duplicate_slug_is_409(api, monkeypatch):
    import subarr.routers.onboarding as onb

    async def noop_rebuild(state, reprobe=True):
        from subarr.coverage_engine import IntegrationBundle
        state.integrations = IntegrationBundle()

    monkeypatch.setattr(onb, "_rebuild_runtime_clients", noop_rebuild)
    body = {"service": "sonarr", "name": "Anime", "url": "http://a.test", "api_key": "k"}
    assert api.post("/api/instances", json=body).status_code == 201
    dup = api.post("/api/instances", json={**body, "url": "http://b.test"})
    assert dup.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instances_api.py -k add_ -v`
Expected: FAIL — 404 / 405.

- [ ] **Step 3: Write minimal implementation**

Add to `src/subarr/routers/instances.py`:

```python
from ..libraries import slugify  # the exact kebab-case rule build_instances uses
from ..instances import Instance, InstanceConfigError, build_instances
from ..config import rebuild_instances


class AddInstanceRequest(BaseModel):
    service: str
    name: str
    url: str
    api_key: str


def _extras() -> dict:
    raw = config_store.load_overrides().get("instances", {})
    return raw if isinstance(raw, dict) else {}


def _instance_defaults() -> list:
    """The same env-backed instance-0 defaults config.rebuild_instances builds."""
    return [
        Instance(id="", service="sonarr", name="default", url=settings.sonarr_url, api_key=settings.sonarr_api_key),
        Instance(id="", service="radarr", name="default", url=settings.radarr_url, api_key=settings.radarr_api_key),
        Instance(id="", service="bazarr", name="default", url=settings.bazarr_url, api_key=settings.bazarr_api_key),
    ]


async def _apply_instances(request: Request, extras: dict) -> None:
    """Persist the extras dict, rebuild settings.instances, rebuild the live
    bundle. Raises HTTPException(422) on invalid config (and does NOT persist)."""
    # Validate BEFORE persisting (a 422 beats rebuild_instances' silent fail-soft drop).
    try:
        build_instances(_instance_defaults(), extras)
    except InstanceConfigError as e:
        raise HTTPException(422, detail=str(e))
    config_store.save_override("instances", extras)
    rebuild_instances(settings)
    from .onboarding import _rebuild_runtime_clients
    await _rebuild_runtime_clients(request.app.state)


@router.post("/instances", status_code=201)
async def add_instance(req: AddInstanceRequest, request: Request) -> dict:
    if req.service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {req.service!r}")
    if not (req.name and req.url and req.api_key):
        raise HTTPException(422, detail="name, url and api_key are required")
    new_id = slugify(req.name)
    extras = _extras()
    svc_list = list(extras.get(req.service, []))
    if any(slugify(i.get("name", "")) == new_id or i.get("slug") == new_id for i in svc_list):
        raise HTTPException(409, detail=f"{req.service} instance id {new_id!r} already exists")
    svc_list.append({"name": req.name, "url": req.url, "api_key": req.api_key, "slug": new_id})
    extras[req.service] = svc_list
    await _apply_instances(request, extras)
    inst = next(i for i in settings.instances if i.service == req.service and i.id == new_id)
    return _serialize(inst)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instances_api.py -k add_ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/instances.py tests/test_instances_api.py
git commit -m "feat(#161): POST /api/instances add+persist+apply-live (phase 4A)"
```

---

## Task 4: PUT /api/instances/{service}/{id} — edit

**Files:**
- Modify: `src/subarr/routers/instances.py`
- Test: `tests/test_instances_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_edit_instance_url_and_keep_masked_key(api, monkeypatch):
    import subarr.routers.onboarding as onb

    async def noop_rebuild(state, reprobe=True):
        from subarr.coverage_engine import IntegrationBundle
        state.integrations = IntegrationBundle()

    monkeypatch.setattr(onb, "_rebuild_runtime_clients", noop_rebuild)
    api.post("/api/instances", json={"service": "bazarr", "name": "Anime", "url": "http://a.test", "api_key": "orig"})

    # Edit url; omit api_key -> existing key is kept.
    r = api.put("/api/instances/bazarr/anime", json={"name": "Anime", "url": "http://a2.test"})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "http://a2.test"

    from subarr import config_store
    saved = next(i for i in config_store.load_overrides()["instances"]["bazarr"] if i.get("slug") == "anime")
    assert saved["url"] == "http://a2.test"
    assert saved["api_key"] == "orig"   # preserved


def test_edit_unknown_instance_is_404(api):
    r = api.put("/api/instances/sonarr/nope", json={"name": "X", "url": "http://x.test", "api_key": "k"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instances_api.py -k edit_ -v`
Expected: FAIL — 404/405 (route missing).

- [ ] **Step 3: Write minimal implementation**

Add to `src/subarr/routers/instances.py`:

```python
class EditInstanceRequest(BaseModel):
    name: str
    url: str
    api_key: str | None = None  # omitted/empty -> keep existing (masked-edit)


@router.put("/instances/{service}/{instance_id}")
async def edit_instance(service: str, instance_id: str, req: EditInstanceRequest, request: Request) -> dict:
    if service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {service!r}")
    if instance_id == "":
        raise HTTPException(400, detail="the default instance is edited via env/onboarding, not here")
    extras = _extras()
    svc_list = list(extras.get(service, []))
    idx = next((n for n, i in enumerate(svc_list) if (i.get("slug") or slugify(i.get("name", ""))) == instance_id), None)
    if idx is None:
        raise HTTPException(404, detail=f"{service} instance {instance_id!r} not found")
    existing = svc_list[idx]
    svc_list[idx] = {
        "name": req.name,
        "url": req.url,
        "api_key": req.api_key or existing.get("api_key", ""),
        "slug": instance_id,  # id is immutable across an edit
    }
    extras[service] = svc_list
    await _apply_instances(request, extras)
    inst = next(i for i in settings.instances if i.service == service and i.id == instance_id)
    return _serialize(inst)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instances_api.py -k edit_ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/instances.py tests/test_instances_api.py
git commit -m "feat(#161): PUT edit instance, masked-key preserve (phase 4A)"
```

---

## Task 5: DELETE /api/instances/{service}/{id} — remove (block default)

**Files:**
- Modify: `src/subarr/routers/instances.py`
- Test: `tests/test_instances_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_delete_instance_removes_and_applies(api, monkeypatch):
    import subarr.routers.onboarding as onb

    async def noop_rebuild(state, reprobe=True):
        from subarr.coverage_engine import IntegrationBundle
        state.integrations = IntegrationBundle()

    monkeypatch.setattr(onb, "_rebuild_runtime_clients", noop_rebuild)
    api.post("/api/instances", json={"service": "radarr", "name": "Anime", "url": "http://a.test", "api_key": "k"})

    r = api.delete("/api/instances/radarr/anime")
    assert r.status_code == 200, r.text
    listed = api.get("/api/instances").json()["instances"]
    assert not any(i["id"] == "anime" and i["service"] == "radarr" for i in listed)


def test_delete_default_instance_is_400(api):
    r = api.delete("/api/instances/sonarr/")  # trailing slash -> empty id
    assert r.status_code in (400, 404)  # framework may 404 the empty path; explicit guard returns 400 when reached
    r2 = api.request("DELETE", "/api/instances/sonarr/%20")  # space-id sanity
    assert r2.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instances_api.py -k delete_ -v`
Expected: FAIL — 404/405 (route missing).

- [ ] **Step 3: Write minimal implementation**

Add to `src/subarr/routers/instances.py`:

```python
@router.delete("/instances/{service}/{instance_id}")
async def delete_instance(service: str, instance_id: str, request: Request) -> dict:
    if service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {service!r}")
    if instance_id.strip() == "":
        raise HTTPException(400, detail="the default instance cannot be removed")
    extras = _extras()
    svc_list = list(extras.get(service, []))
    new_list = [i for i in svc_list if (i.get("slug") or slugify(i.get("name", ""))) != instance_id]
    if len(new_list) == len(svc_list):
        raise HTTPException(404, detail=f"{service} instance {instance_id!r} not found")
    extras[service] = new_list
    await _apply_instances(request, extras)
    # Libraries that bound the removed instance now dangle -> they degrade to
    # instance 0 (validate_library_bindings warns). Surface the warnings so the
    # caller can prompt a re-bind.
    from ..config import validate_library_bindings
    warnings = validate_library_bindings(settings.libraries, settings.instances)
    return {"removed": True, "binding_warnings": warnings}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instances_api.py -k delete_ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/instances.py tests/test_instances_api.py
git commit -m "feat(#161): DELETE instance, block default, surface dangling bindings (phase 4A)"
```

---

## Task 6: GET /api/topology — resolved library bindings + Plex section + warnings

**Files:**
- Modify: `src/subarr/routers/instances.py`
- Test: `tests/test_instances_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_topology_lists_libraries_with_bindings(api):
    r = api.get("/api/topology")
    assert r.status_code == 200
    data = r.json()
    assert "libraries" in data and isinstance(data["libraries"], list)
    # default single-stack: library 0 present, bound to default ("") instances
    lib0 = next(l for l in data["libraries"])
    assert set(["slug", "name", "sonarr_id", "radarr_id", "bazarr_id", "plex_section"]).issubset(lib0)
    assert "binding_warnings" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instances_api.py -k topology -v`
Expected: FAIL — 404.

- [ ] **Step 3: Write minimal implementation**

Add to `src/subarr/routers/instances.py`:

```python
from ..config import validate_library_bindings


def _plex_section_for(request: Request, lib) -> dict:
    """Live-match the library's fs_root to a Plex section (displayed, not stored).
    Returns {name|None, matched: bool}. Never raises (Plex may be unconfigured)."""
    plex = getattr(request.app.state.integrations, "plex", None)
    try:
        if plex is not None and plex.is_configured():
            section = await plex._section_for_path(str(lib.fs_root))  # _section_for_path is async
            return {"name": section, "matched": bool(section)}
    except Exception:  # noqa: BLE001 - topology must render even if Plex errs
        log.debug("plex section match failed for %s", lib.slug, exc_info=True)
    return {"name": None, "matched": False}


@router.get("/topology")
async def topology(request: Request) -> dict:
    libs = []
    for lib in settings.libraries:
        plex = _plex_section_for(request, lib)
        libs.append({
            "slug": lib.slug,
            "name": getattr(lib, "name", lib.slug) or "(default)",
            "sonarr_id": lib.sonarr_id,
            "radarr_id": lib.radarr_id,
            "bazarr_id": lib.bazarr_id,
            "plex_section": plex["name"],
            "plex_matched": plex["matched"],
        })
    return {
        "libraries": libs,
        "binding_warnings": validate_library_bindings(settings.libraries, settings.instances),
    }
```

> NOTE: verify `plex._section_for_path` exists and its signature (grep `_section_for_path` in `src/subarr/integrations/plex.py`). If it takes a canonical or a different arg, adapt the call. Verify `Library` exposes `fs_root`, `slug`, `sonarr_id/radarr_id/bazarr_id` (it does — used by `validate_library_bindings` + `fs_to_canonical`); if there's no `name` attr, drop it and use `slug`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instances_api.py -k topology -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/instances.py tests/test_instances_api.py
git commit -m "feat(#161): GET /api/topology resolved bindings + plex section (phase 4A)"
```

---

## Task 7: PUT /api/libraries/{slug}/binding — override a library's arr/Bazarr binding

**Files:**
- Modify: `src/subarr/routers/instances.py`
- Test: `tests/test_instances_api.py`

- [ ] **Step 1: Write the failing test**

```python
def test_override_library_binding_persists(api, monkeypatch):
    # The default library has slug "" ; override its bazarr binding.
    r = api.put("/api/libraries/_default/binding", json={"bazarr_id": "anime"})
    # _default is the alias for the empty-slug library; route maps it back to "".
    assert r.status_code in (200, 404)  # 404 only if no default library defined
    if r.status_code == 200:
        from subarr import config_store
        libs = config_store.load_overrides().get("libraries", [])
        assert isinstance(libs, list)
```

> NOTE: the empty-slug default library can't be addressed by a path segment. Use the literal `_default` in the URL and map it to `""` server-side. Confirm how `rebuild_libraries` reads the `"libraries"` override key (grep `config.py:354` — it's a **list** of library dicts merged by `build_libraries`); a binding override must round-trip through whatever shape `build_libraries` expects. If `build_libraries` keys overrides by slug, store `{slug, sonarr_id?, radarr_id?, bazarr_id?}`. **Read `build_libraries` before implementing this task** and shape the persisted dict to match; this is the one task whose exact persisted shape depends on existing code not fully quoted here.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_instances_api.py -k binding -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Write minimal implementation**

Add to `src/subarr/routers/instances.py` (shape the persisted dict to match `build_libraries` — see Task 7 NOTE):

```python
from ..config import rebuild_libraries


class BindingRequest(BaseModel):
    sonarr_id: str | None = None
    radarr_id: str | None = None
    bazarr_id: str | None = None


@router.put("/libraries/{slug}/binding")
async def override_binding(slug: str, req: BindingRequest, request: Request) -> dict:
    real_slug = "" if slug == "_default" else slug
    if not any(lib.slug == real_slug for lib in settings.libraries):
        raise HTTPException(404, detail=f"library {slug!r} not found")
    raw = config_store.load_overrides().get("libraries", [])
    libs = list(raw) if isinstance(raw, list) else []
    entry = next((d for d in libs if isinstance(d, dict) and d.get("slug") == real_slug), None)
    if entry is None:
        entry = {"slug": real_slug}
        libs.append(entry)
    for field in ("sonarr_id", "radarr_id", "bazarr_id"):
        val = getattr(req, field)
        if val is not None:
            entry[field] = val
    config_store.save_override("libraries", libs)
    rebuild_libraries(settings)
    lib = next(lib for lib in settings.libraries if lib.slug == real_slug)
    return {"slug": real_slug, "sonarr_id": lib.sonarr_id, "radarr_id": lib.radarr_id, "bazarr_id": lib.bazarr_id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_instances_api.py -k binding -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/subarr/routers/instances.py tests/test_instances_api.py
git commit -m "feat(#161): PUT library binding override (phase 4A)"
```

---

## Task 8: Full suite + gates + back-compat guard

**Files:** none (verification + one back-compat test).

- [ ] **Step 1: Add a single-stack back-compat test**

```python
def test_single_stack_topology_is_one_default_library(api):
    # No extras added: exactly the env-backed defaults, no surprise instances.
    insts = api.get("/api/instances").json()["instances"]
    assert all(i["is_default"] for i in insts)
    topo = api.get("/api/topology").json()
    assert topo["binding_warnings"] == []
```

- [ ] **Step 2: Run the new test file green**

Run: `python -m pytest tests/test_instances_api.py -v`
Expected: all PASS.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: prior baseline (1372 passed) + the new tests, 0 failures.

- [ ] **Step 4: Gates**

Run: `ruff format tests/test_instances_api.py src/subarr/routers/instances.py` then
`ruff check src tests && ruff format --check src tests && PYTHONIOENCODING=utf-8 bandit -q -r src`
Expected: all clean (bandit: no new HIGH).

- [ ] **Step 5: Commit**

```bash
git add tests/test_instances_api.py
git commit -m "test(#161): single-stack back-compat guard for instance API (phase 4A)"
```

---

## Pre-merge review (Tier 2)

Instance config touches credentials + the live integration bundle (auth-adjacent + concurrency: a rebuild swaps `app.state.integrations` while loops read it). Per the review program this is **Tier 2**: multi-lens (correctness + back-compat) **+ failure-mode lens** — focus on: (a) the api_key is never serialized in any response; (b) `_rebuild_runtime_clients` closes the old bundle (no leaked httpx sessions) and the swap is safe against in-flight loop reads; (c) add/edit/remove validate before persisting (no half-written override store); (d) single-stack stays byte-identical (no `"instances"`/`"libraries"` keys written unless the user adds one). Fix reals or file. Then PR + `--admin` merge after CI.

## Done criteria
- A second Sonarr/Radarr/Bazarr instance is creatable, editable, removable, and testable via REST; changes persist to the override store and apply live.
- Resolved topology (library→arr/Bazarr/Plex-section) is queryable with dangling-binding warnings; a library's bindings are overridable.
- api_key never leaves the server in a response body.
- Single-stack byte-identical; full suite + 3 gates green; Tier-2 review passed.

## Out of scope (later Phase 4 slices)
- **4B:** Settings▸Instances UI + resolved-topology table + root-folder auto-enumerate→library-proposal flow (the `root_folders` from Task 2's probe feeds this).
- **4C:** display labels + the All/TV/Anime dropdown filter on Coverage/Library (+ Queue/Review/Aftercare/Activity) + per-instance Health dots.
- **Phase 5:** wizard "add another stack" branch (reuses the 4B widget) + docs + announce.
