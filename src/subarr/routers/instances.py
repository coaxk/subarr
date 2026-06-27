"""#161 Phase 4A — instance + topology config API.

Exposes the multi-instance model (already built in Phase 1-3) for management:
list/test/add/edit/remove Sonarr/Radarr/Bazarr instances and view/override the
resolved library->arr/Bazarr/Plex-section topology. Persists to the config
override store and applies changes live via rebuild_instances + a runtime
client rebuild — no restart.

Config (settings/rebuild_*) is imported function-locally so the running config
module is re-read each call — this matches the reload-safe convention used by
admin.py/onboarding.py and keeps the reload-based test fixtures working.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config_store
from ..integrations.bazarr import BazarrClient
from ..integrations.radarr import RadarrClient
from ..integrations.sonarr import SonarrClient

router = APIRouter(prefix="/api", tags=["instances"])
log = logging.getLogger(__name__)

_ARR_SERVICES = ("sonarr", "radarr", "bazarr")
_CTORS = {"sonarr": SonarrClient, "radarr": RadarrClient, "bazarr": BazarrClient}

# Serializes add/edit/delete: each is a read-modify-write of the override store
# plus a live bundle swap; concurrent calls must not interleave.
_APPLY_LOCK = asyncio.Lock()


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
    from ..config import settings

    return {"instances": [_serialize(i) for i in settings.instances]}


class TestConnRequest(BaseModel):
    service: str
    url: str
    api_key: str


async def _probe_connection(service: str, url: str, api_key: str) -> dict:
    """Construct a throwaway client and hit a cheap authenticated endpoint.
    A "test connection" probe: ANY failure (incl. a malformed URL that raises
    httpx.InvalidURL at construction, before the request) is reported as
    {ok: false} rather than a 500. Always closes the client if one was built."""
    client = None
    try:
        client = _CTORS[service](base_url=url, api_key=api_key)
        if service in ("sonarr", "radarr"):
            folders = await client.root_folders()
            paths = [f.get("path") for f in folders if isinstance(f, dict) and f.get("path")]
            return {"ok": True, "detail": "connected", "root_folders": paths}
        # bazarr: list_tasks is a cheap authenticated GET
        await client.list_tasks()
        return {"ok": True, "detail": "connected", "root_folders": []}
    except Exception as e:  # noqa: BLE001 - a test probe: any failure = not ok
        return {"ok": False, "detail": str(e), "root_folders": []}
    finally:
        if client is not None:
            await client.aclose()


@router.post("/instances/test")
async def test_connection(req: TestConnRequest) -> dict:
    if req.service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {req.service!r}")
    if not req.url or not req.api_key:
        raise HTTPException(422, detail="url and api_key are required")
    return await _probe_connection(req.service, req.url, req.api_key)


# Health dots are a UI affordance, not a deep diagnostic: bound each probe well
# under the integration client's 90s read timeout so a single wedged instance
# (TCP-accepts then stalls the HTTP read) can't make the whole fan-out crawl.
# The Test-connection button keeps the full client timeout (it calls
# _probe_connection directly) — only the dots are time-boxed.
_HEALTH_PROBE_TIMEOUT = 10.0


async def _probe_instance(inst) -> dict:
    """#378: live reachability for one configured instance, feeding the Instances
    UI health dots. Unconfigured instances (no url/api_key — e.g. a default whose
    env scalars are unset) are reported online=False WITHOUT a network call, so a
    fresh single-stack install never fires a doomed probe. Never raises: the
    underlying _probe_connection already maps every failure to {ok: False}; a
    wedged instance that blows the _HEALTH_PROBE_TIMEOUT is reported offline."""
    configured = bool(inst.url and inst.api_key)
    base = {"id": inst.id, "service": inst.service, "name": inst.name, "configured": configured}
    if not configured:
        return {**base, "online": False, "detail": "not configured"}
    try:
        res = await asyncio.wait_for(
            _probe_connection(inst.service, inst.url, inst.api_key), timeout=_HEALTH_PROBE_TIMEOUT
        )
    except asyncio.TimeoutError:
        return {**base, "online": False, "detail": "probe timed out"}
    return {**base, "online": bool(res.get("ok")), "detail": res.get("detail", "")}


@router.get("/instances/health")
async def instances_health() -> dict:
    """Per-instance live health for the Settings ▸ Instances dots. Fans the probes
    out concurrently (one cheap authenticated GET each); a slow/down instance never
    blocks the others. Read-only — never mutates config or the live bundle."""
    from ..config import settings

    results = await asyncio.gather(*(_probe_instance(i) for i in settings.instances))
    return {"health": list(results)}


def _extras() -> dict:
    raw = config_store.load_overrides().get("instances", {})
    return raw if isinstance(raw, dict) else {}


async def _apply_instances(request: Request, extras: dict) -> None:
    """Validate, persist the extras dict, rebuild settings.instances, rebuild the
    live bundle. Raises HTTPException(422) on invalid config (and does NOT
    persist) — a 422 beats rebuild_instances' silent fail-soft drop.

    Serialized by _APPLY_LOCK: the persist is a read-modify-write of the override
    store, and the bundle swap mutates app.state — concurrent add/edit/delete must
    not interleave (lost-update on disk, or overlapping bundle rebuilds)."""
    async with _APPLY_LOCK:
        await _apply_instances_locked(request, extras)


async def _apply_instances_locked(request: Request, extras: dict) -> None:
    from ..config import rebuild_instances, settings
    from ..instances import Instance, InstanceConfigError, build_instances

    defaults = [
        Instance(
            id="", service="sonarr", name="default", url=settings.sonarr_url, api_key=settings.sonarr_api_key
        ),
        Instance(
            id="", service="radarr", name="default", url=settings.radarr_url, api_key=settings.radarr_api_key
        ),
        Instance(
            id="", service="bazarr", name="default", url=settings.bazarr_url, api_key=settings.bazarr_api_key
        ),
    ]
    try:
        build_instances(defaults, extras)
    except InstanceConfigError as e:
        raise HTTPException(422, detail=str(e))
    config_store.save_override("instances", extras)
    rebuild_instances(settings)
    from .onboarding import _rebuild_runtime_clients

    await _rebuild_runtime_clients(request.app.state)


class AddInstanceRequest(BaseModel):
    service: str
    name: str
    url: str
    api_key: str


@router.post("/instances", status_code=201)
async def add_instance(req: AddInstanceRequest, request: Request) -> dict:
    from ..config import settings
    from ..libraries import slugify

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


class EditInstanceRequest(BaseModel):
    name: str
    url: str
    api_key: str | None = None  # omitted/empty -> keep existing (masked-edit)


@router.put("/instances/{service}/{instance_id}")
async def edit_instance(service: str, instance_id: str, req: EditInstanceRequest, request: Request) -> dict:
    from ..config import settings
    from ..libraries import slugify

    if service not in _ARR_SERVICES:
        raise HTTPException(422, detail=f"unknown service {service!r}")
    if instance_id.strip() == "":
        raise HTTPException(400, detail="the default instance is edited via env/onboarding, not here")
    extras = _extras()
    svc_list = list(extras.get(service, []))
    idx = next(
        (n for n, i in enumerate(svc_list) if (i.get("slug") or slugify(i.get("name", ""))) == instance_id),
        None,
    )
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


@router.delete("/instances/{service}/{instance_id}")
async def delete_instance(service: str, instance_id: str, request: Request) -> dict:
    from ..config import settings, validate_library_bindings
    from ..libraries import slugify

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
    warnings = validate_library_bindings(settings.libraries, settings.instances)
    return {"removed": True, "binding_warnings": warnings}


async def _plex_section_for(request: Request, lib) -> dict:
    """Live-match the library's fs_root to a Plex section (displayed, not stored).
    Returns {name|None, matched: bool}. Never raises (Plex may be unconfigured)."""
    plex = getattr(request.app.state.integrations, "plex", None)
    try:
        if plex is not None and plex.is_configured():
            section = await plex._section_for_path(str(lib.fs_root))
            return {"name": section, "matched": bool(section)}
    except Exception:  # noqa: BLE001 - topology must render even if Plex errs
        log.debug("plex section match failed for %s", lib.slug, exc_info=True)
    return {"name": None, "matched": False}


@router.get("/topology")
async def topology(request: Request) -> dict:
    from ..config import settings, validate_library_bindings

    libs = []
    for lib in settings.libraries:
        plex = await _plex_section_for(request, lib)
        libs.append(
            {
                "slug": lib.slug,
                "name": lib.name or "(default)",
                "sonarr_id": lib.sonarr_id,
                "radarr_id": lib.radarr_id,
                "bazarr_id": lib.bazarr_id,
                "plex_section": plex["name"],
                "plex_matched": plex["matched"],
            }
        )
    return {
        "libraries": libs,
        "binding_warnings": validate_library_bindings(settings.libraries, settings.instances),
    }
