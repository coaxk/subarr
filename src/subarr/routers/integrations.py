"""GET /api/integrations/health — per-upstream up/down + version + summary.

One concurrent fan-out call. Per-upstream errors don't fail the whole
endpoint — each integration reports its own status. The frontend's
Settings tab renders this as a status grid.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..integrations import IntegrationError

router = APIRouter(prefix="/api", tags=["integrations"])
log = logging.getLogger(__name__)


# ─── #75: in-app credential editing ─────────────────────────────────
#
# Per-service map of the editable credential fields. Each entry maps the
# request-body key the UI sends → the Settings attribute + env var it backs.
# This is the single source of truth for "what can the credential editor
# write", deliberately narrower than the full Settings surface.
#
#   request key  →  (settings_attr, coerce)
#
# env-set authority: config.env_is_set(settings_attr) is checked per field
# before any write; an env-set field is the operator's authoritative
# declaration and is never overwritten (mirrors the onboarding clobber
# guard). subgen + ollama also carry non-credential knobs (model) but those
# flow through the same path.
_CREDENTIAL_FIELDS: dict[str, dict[str, tuple[str, type]]] = {
    "bazarr":   {"url": ("bazarr_url", str),   "api_key": ("bazarr_api_key", str)},
    "sonarr":   {"url": ("sonarr_url", str),   "api_key": ("sonarr_api_key", str)},
    "radarr":   {"url": ("radarr_url", str),   "api_key": ("radarr_api_key", str)},
    "tautulli": {"url": ("tautulli_url", str), "api_key": ("tautulli_api_key", str)},
    # Plex authenticates with a token rather than an X-Api-Key header.
    "plex":     {"url": ("plex_url", str),     "token": ("plex_token", str)},
    "subgen":   {"url": ("subgen_url", str)},
    "ollama":   {"url": ("ollama_url", str),   "model": ("ollama_model", str)},
}


# Field keys whose value is a secret — masked in GET responses so the
# editor can show "set / not set" without leaking the key into the DOM.
_SECRET_KEYS = {"api_key", "token"}


def _mask(value: str) -> str:
    """Mask a secret for display: keep the last 4 chars, never the rest."""
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


@router.get("/integrations/{name}/config")
def get_credentials(name: str, request: Request) -> dict[str, Any]:
    """Report the editor's view of an integration's credentials: per editable
    field, whether it's env-managed (read-only in the UI) and whether a value
    is currently set. Secret values are masked; URLs are returned verbatim so
    the form can prefill them."""
    from ..config import env_is_set, settings

    svc = name.lower()
    schema = _CREDENTIAL_FIELDS.get(svc)
    if schema is None:
        raise HTTPException(404, detail=f"unknown integration: {svc}")

    fields: dict[str, Any] = {}
    for key, (settings_attr, _coerce) in schema.items():
        current = str(getattr(settings, settings_attr, "") or "")
        is_secret = key in _SECRET_KEYS
        fields[key] = {
            "env_managed": env_is_set(settings_attr),
            "has_value": bool(current),
            # URLs prefill; secrets only ever expose a masked hint.
            "value": _mask(current) if is_secret else current,
            "secret": is_secret,
        }
    return {"name": svc, "fields": fields}


class CredentialsBody(BaseModel):
    url: str | None = None
    api_key: str | None = None
    token: str | None = None     # Plex
    model: str | None = None     # Ollama


class TestBody(BaseModel):
    url: str
    api_key: str | None = None
    token: str | None = None     # Plex


@router.put("/integrations/{name}/credentials")
@router.post("/integrations/{name}/credentials")
async def save_credentials(name: str, body: CredentialsBody,
                           request: Request) -> dict[str, Any]:
    """Persist + live-apply integration credentials without a restart.

    For each supplied field:
      - env-set fields are NOT overwritten (reported in `managed_by_env`);
      - otherwise the value is persisted to the config-store override file
        (survives restart, below env) AND applied to the running Settings
        singleton, then the affected clients are rebuilt on app.state so the
        change takes effect live.

    Returns {ok, applied: [...], managed_by_env: [...], ignored: [...]}.
    """
    from .. import config_store
    from ..config import env_is_set, settings
    from .onboarding import _rebuild_runtime_clients

    svc = name.lower()
    schema = _CREDENTIAL_FIELDS.get(svc)
    if schema is None:
        raise HTTPException(404, detail=f"unknown integration: {svc}")

    supplied = body.model_dump(exclude_none=True)
    # Only keys this service actually accepts; a stray key is ignored, not
    # an error, but an entirely-empty / unrecognised body is a 400 (caller
    # bug, surface it).
    relevant = {k: v for k, v in supplied.items() if k in schema}
    if not relevant:
        raise HTTPException(
            400,
            detail=f"no editable fields for {svc}; accepts {sorted(schema)}",
        )

    applied: list[str] = []
    managed_by_env: list[str] = []
    for key, raw in relevant.items():
        settings_attr, coerce = schema[key]
        if env_is_set(settings_attr):
            # Operator's env is authoritative — never clobber it.
            managed_by_env.append(settings_attr)
            log.info("creds: %s.%s managed by env, ignored UI write",
                     svc, settings_attr)
            continue
        value = coerce(raw)
        # Persist below env (survives restart).
        config_store.save_override(settings_attr, value)
        # Apply to the running frozen Settings (live). object.__setattr__
        # bypasses the dataclass freeze — same deliberate runtime patch the
        # onboarding flush uses.
        try:
            object.__setattr__(settings, settings_attr, value)
        except Exception as e:  # defensive — never 500 on a config write
            log.warning("creds: applying %s failed: %s", settings_attr, e)
            continue
        applied.append(settings_attr)

    # Rebuild the integration clients so the new creds take effect without a
    # restart. Reuses the onboarding live-reload path (rebuilds
    # state.integrations / subgen / ollama; the background loops resolve
    # their clients through providers, so the swap is enough).
    if applied:
        await _rebuild_runtime_clients(request.app.state)

    return {
        "ok": True,
        "applied": applied,
        "managed_by_env": managed_by_env,
    }


@router.post("/integrations/{name}/test")
async def test_credentials(name: str, body: TestBody,
                           request: Request) -> dict[str, Any]:
    """Test-connection-before-save: verify a (url, api_key/token) reaches
    the named service WITHOUT committing it. Reuses the onboarding
    per-service probe handlers so the same reachability logic backs both
    the wizard and the Settings editor.

    Always returns 200 with {ok, version, detail, error} (never a 500 on an
    unreachable upstream) so the UI can render the failure inline. Unknown
    service is a 404.
    """
    from .onboarding import TestRequest, test_connection

    svc = name.lower()
    if svc not in _CREDENTIAL_FIELDS:
        raise HTTPException(404, detail=f"unknown integration: {svc}")
    # Map the credential body onto the onboarding TestRequest. Plex's token
    # rides in `token`; everything else in `api_key`.
    req = TestRequest(url=body.url, api_key=body.api_key, token=body.token)
    return await test_connection(svc, req, request)


@dataclass
class OllamaProbeResult:
    """Last-known ollama reachability, cached on app.state by the
    integrations-health endpoint. Mirrors the subgen_caps `.reachable`
    pattern so telemetry can report ollama on a REAL signal (was
    /api/tags reachable?) rather than the defaulted OLLAMA_URL, which is
    non-empty on every install (#119)."""
    reachable: bool


async def _probe(name: str, client, summary_kind: str = "version") -> dict[str, Any]:
    if not client.is_configured():
        return {"name": name, "online": False, "configured": False}
    try:
        if summary_kind == "ollama_models":
            # Ollama uses /api/tags as both liveness probe + model list.
            # Surface model count + first-5 names for the Settings panel.
            tags = await client.tags()
            models = tags.get("models", []) if isinstance(tags, dict) else []
            # #232: surface vision-pre-filter capability so the Settings
            # panel can show "Vision pre-filter active / inactive" with
            # the resolved model name, instead of users discovering it
            # only when a vision call fails.
            client.reset_vision_cache()
            vision_resolved = await client.resolve_vision_model()
            return {
                "name": name,
                "online": True,
                "configured": True,
                "badges": {
                    "models": len(models),
                    "model_names": ", ".join(m.get("name", "?") for m in models[:3])
                                   + (" ..." if len(models) > 3 else ""),
                    "vision_model_config": client.vision_model_config,
                    "vision_model_resolved": vision_resolved or "",
                    "vision_capable": bool(vision_resolved),
                },
            }
        if summary_kind == "bazarr_badges":
            status_task = asyncio.create_task(client.status())
            badges_task = asyncio.create_task(client.badges())
            status = await status_task
            badges = await badges_task
            return {
                "name": name,
                "online": True,
                "configured": True,
                "version": status.get("bazarr_version") or status.get("version"),
                "badges": badges,
            }
        status = await client.status()
        return {
            "name": name,
            "online": True,
            "configured": True,
            "version": status.get("version") if isinstance(status, dict) else None,
        }
    except IntegrationError as e:
        return {"name": name, "online": False, "configured": True, "error": str(e)}
    except Exception as e:  # defensive
        log.warning("%s probe unexpected error: %s", name, e)
        return {"name": name, "online": False, "configured": True, "error": repr(e)}


@router.get("/integrations/health")
async def integrations_health(request: Request) -> dict[str, Any]:
    integrations = request.app.state.integrations
    # Pull Ollama from app.state — it's not part of the IntegrationBundle
    # (used only by lang_enrichment, not coverage flow). Treating it like
    # a first-class integration here so the Settings panel can show its
    # model list + reachability without us inventing a separate endpoint.
    ollama = getattr(request.app.state, "ollama", None)
    probes_coros = [
        _probe("bazarr", integrations.bazarr, "bazarr_badges"),
        _probe("sonarr", integrations.sonarr),
        _probe("radarr", integrations.radarr),
        _probe("plex", integrations.plex),
        _probe("tautulli", integrations.tautulli),
    ]
    if ollama is not None:
        probes_coros.append(_probe("ollama", ollama, "ollama_models"))
    probes = await asyncio.gather(*probes_coros)
    # #119: cache ollama reachability on app.state so telemetry reports it
    # on a real signal (was /api/tags up?) instead of the defaulted URL.
    # The ollama probe, when present, is always the last entry appended.
    if ollama is not None:
        ollama_probe = next((p for p in probes if p.get("name") == "ollama"), None)
        request.app.state.ollama_probe_result = OllamaProbeResult(
            reachable=bool(ollama_probe and ollama_probe.get("online"))
        )
    # Subgen capabilities probed once at boot, cached on app.state.
    # Surfaced here so the UI can show "compat mode" badges + gate
    # scan-submit on has_batch.
    caps = getattr(request.app.state, "subgen_caps", None)
    subgen_block = caps.to_dict() if caps else {"reachable": False}
    subgen_block["name"] = "subgen"
    return {"integrations": probes, "subgen": subgen_block}
