"""Guided subgen setup (spec §2): detect → plan → apply.

Module-level indirection functions (_run_local_smi, _nvidia_runtime,
_subgen_caps, _post_config) exist so tests monkeypatch the seams without
faking subprocesses or app state internals.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..subgen_client import SubgenUnavailable
from ..subgen_config_gen import (
    detection_passthrough_snippet,
    generate_env_additions,
    generate_full_compose,
)
from ..subgen_hardware import (
    derive_compute_type,
    model_fit,
    parse_smi_csv,
    recommend_model,
)

router = APIRouter(prefix="/api/subgen-setup", tags=["subgen-setup"])
log = logging.getLogger(__name__)

_SMI_PASTE_COMMAND = "nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader"


async def _run_local_smi() -> str | None:
    """Tier 1: subarr's own nvidia-smi (utility passthrough). None when the
    binary is missing or errors — fail-soft to the next tier."""
    import shutil

    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "--query-gpu=name,memory.total,memory.free,compute_cap,driver_version",
            "--format=csv,nounits,noheader",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return out.decode(errors="replace") if proc.returncode == 0 else None
    except Exception as e:  # noqa: BLE001 - detection must never error the wizard
        log.debug("local smi failed (non-fatal): %s", e)
        return None


async def _nvidia_runtime(app) -> bool | None:
    """Tier 2: docker info Runtimes (async — a dead socket must not stall)."""
    try:
        return await app.state.docker.nvidia_runtime_available()
    except Exception:
        return None


def _subgen_caps(app):
    return getattr(app.state, "subgen_caps", None)


async def _post_config(app, **kw):
    return await app.state.subgen.post_config(**kw)


@router.get("/detect")
async def detect(request: Request) -> dict[str, Any]:
    """Run the §2.1 cascade; report the best tier that produced signal."""
    smi = await _run_local_smi()
    gpu = parse_smi_csv(smi) if smi else None
    # Tier 3 regardless of GPU tier — feeds the current-vs-recommended diff.
    try:
        current = await request.app.state.docker.subgen_current_config()
    except Exception:  # noqa: BLE001 - diff input is optional
        current = None
    if gpu:
        return {
            "tier": "smi",
            "gpu": gpu.__dict__,
            "host_gpu_capable": True,
            "recommended_model": recommend_model(vram_total_mb=gpu.vram_total_mb),
            "subgen_current": current,
            "passthrough_snippet": None,
            "smi_command": _SMI_PASTE_COMMAND,
        }
    capable = await _nvidia_runtime(request.app)
    return {
        "tier": "docker_info" if capable else "manual",
        "gpu": None,
        "host_gpu_capable": capable,
        "recommended_model": None,
        "subgen_current": current,
        # the manual-fallback path teaches the upgrade (spec §2.2 footer)
        "passthrough_snippet": detection_passthrough_snippet(),
        "smi_command": _SMI_PASTE_COMMAND,
    }


class ParseBody(BaseModel):
    pasted: str


@router.post("/parse-smi")
def parse_pasted(body: ParseBody) -> dict[str, Any]:
    """§2.2 paste box: same parser as the auto path."""
    gpu = parse_smi_csv(body.pasted)
    if gpu is None:
        return {
            "ok": False,
            "error": "could not parse that output — paste the unmodified command result",
        }
    return {
        "ok": True,
        "gpu": gpu.__dict__,
        "recommended_model": recommend_model(vram_total_mb=gpu.vram_total_mb),
    }


class PlanBody(BaseModel):
    model: str
    vram_total_mb: int | None = None
    compute_cap: float | None = None
    media_host_path: str = "/path/to/media"


@router.post("/plan")
def plan(body: PlanBody, request: Request) -> dict[str, Any]:
    """Derive compute, fit, and route the delivery mode (§2.5)."""
    derivation = derive_compute_type(
        compute_cap=body.compute_cap, vram_total_mb=body.vram_total_mb, model=body.model
    )
    fit = model_fit(body.model, derivation.compute_type, vram_total_mb=body.vram_total_mb)
    gpu = body.compute_cap is not None
    device = "cuda" if gpu else "cpu"
    env_additions = generate_env_additions(
        model=body.model, device=device, compute_type=derivation.compute_type
    )

    caps = _subgen_caps(request.app)
    if caps is None or not getattr(caps, "reachable", False):
        mode, upsell = "generate_full", None
    elif getattr(caps, "runtime_config", False):
        mode, upsell = "live_apply", None
    elif getattr(caps, "is_subarr_subgen", False):
        mode, upsell = "generate", "upgrade_to_r9"
    else:
        mode, upsell = "generate", "switch_to_subarr_subgen"

    out: dict[str, Any] = {
        "model": body.model,
        "device": device,
        "compute_type": derivation.compute_type,
        "compute_reason": derivation.reason,
        "fit": fit,
        "mode": mode,
        "upsell": upsell,
        "env_additions": env_additions,
    }
    if mode == "generate_full":
        out["compose"] = generate_full_compose(
            model=body.model,
            device=device,
            compute_type=derivation.compute_type,
            media_host_path=body.media_host_path,
            gpu=gpu,
        )
    return out


class ApplyBody(BaseModel):
    model: str
    compute_type: str
    vram_total_mb: int | None = None


@router.post("/apply")
async def apply(body: ApplyBody, request: Request) -> dict[str, Any]:
    """Mode C: pre-check the fit, then POST /config; surface the outcome.
    Defense in depth — the §2.4 math stops known-impossible loads before the
    call, subgen's rollback contract backstops the rest."""
    caps = _subgen_caps(request.app)
    if not (caps and getattr(caps, "runtime_config", False)):
        return {
            "ok": False,
            "reason": "not_supported",
            "detail": "connected subgen has no runtime_config capability (needs subarr-subgen r9+)",
        }
    if model_fit(body.model, body.compute_type, vram_total_mb=body.vram_total_mb) == "no_fit":
        return {
            "ok": False,
            "reason": "precheck_no_fit",
            "detail": (
                f"{body.model} ({body.compute_type}) cannot fit in "
                f"{body.vram_total_mb} MB — pick a smaller model or int8 variant"
            ),
        }
    try:
        status, resp = await _post_config(request.app, model=body.model, compute_type=body.compute_type)
    except SubgenUnavailable as e:
        # Transport failure is AMBIGUOUS — the switch may or may not have
        # landed before the connection died. Tell the user to re-detect.
        return {
            "ok": False,
            "reason": "subgen_unreachable",
            "detail": (
                f"subgen did not answer — the change may or may not have applied; "
                f"re-run detection once subgen is back ({e})"
            ),
        }
    if status == 200 and resp.get("ok"):
        return {"ok": True, "model": resp.get("model"), "compute_type": resp.get("compute_type")}
    return {
        "ok": False,
        "reason": resp.get("reason", f"http_{status}"),
        "detail": resp.get("detail"),
        "rolled_back": resp.get("rolled_back"),
        "current_model": resp.get("current_model"),
        "current_compute_type": resp.get("current_compute_type"),
    }
