"""Multi-instance config model (#161 Phase 1).

An Instance = one Sonarr/Radarr/Bazarr container's credentials (url + api_key).
Instance identity is a short immutable slug; the default/legacy instance per
service uses the EMPTY slug (its credentials come from the env scalars), so
existing single-stack installs need no config and stay byte-identical.

Pure module: no env, no IO, no `settings` import -- `build_instances` is a
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
    id: str  # "" = default/legacy instance (env-backed); else unique kebab slug within its service
    service: str  # "sonarr" | "radarr" | "bazarr"
    name: str  # human-facing label
    url: str
    api_key: str


class InstanceConfigError(ValueError):
    """Invalid instances config (dup/empty id, missing fields, ...)."""


def build_instances(defaults: list[Instance], extras: dict) -> tuple[Instance, ...]:
    """Assemble the validated instance tuple.

    `defaults` is the per-service instance 0 list (their ids are FORCED to "").
    `extras` is the persisted override dict: {service: [{name, url, api_key,
    slug?}, ...]}. Unknown service keys are ignored. Raises InstanceConfigError
    on any invalid/duplicate extra -- config.load() catches this and falls back
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
