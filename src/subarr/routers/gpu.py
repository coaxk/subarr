"""GET /api/gpu — nvidia-smi snapshot.

No server-side history. Polled every ~2s from the Monitor tab; the frontend
keeps whatever short rolling buffer it wants for sparklines. Pattern lifted
from Iteratarr's gpu-monitor.js (same nvidia-smi field set), reimplemented
clean in Python.

Gracefully degrades when nvidia-smi is missing (CPU-only host, container
without GPU passthrough): returns {online: false, error: ...} with HTTP 200
so the GUI can render a "no GPU" panel instead of erroring the whole tab.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["gpu"])
log = logging.getLogger(__name__)


# Windows path is what dev runs against; PATH lookup is what container/Linux uses.
def _nvidia_smi_path() -> str | None:
    cand = os.environ.get("NVIDIA_SMI_PATH")
    if cand and os.path.isfile(cand):
        return cand
    if os.name == "nt":
        default = r"C:\Windows\System32\nvidia-smi.exe"
        if os.path.isfile(default):
            return default
    return shutil.which("nvidia-smi")


# Original 9-field query — kept as a fallback for old drivers where
# compute_cap is "not a valid field to query" (nvidia-smi exits non-zero).
_GPU_FIELDS_LEGACY = (
    "name,memory.used,memory.total,memory.free,"
    "utilization.gpu,utilization.memory,temperature.gpu,"
    "power.draw,power.limit"
)

_GPU_FIELDS = _GPU_FIELDS_LEGACY + ",compute_cap,driver_version"


async def _run_smi(exe: str, args: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        exe,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("nvidia-smi timed out")
    if proc.returncode != 0:
        raise RuntimeError(f"nvidia-smi exit {proc.returncode}: {stderr.decode(errors='replace')[:200]}")
    return stdout.decode(errors="replace")


def _parse_float(s: str) -> float | None:
    s = s.strip()
    if not s or s.lower() in {"[n/a]", "n/a", "not supported"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


@router.get("/gpu")
async def gpu_status() -> dict[str, Any]:
    exe = _nvidia_smi_path()
    if exe is None:
        return {"online": False, "error": "nvidia-smi not found"}

    legacy = False
    try:
        gpu_csv = await _run_smi(exe, [f"--query-gpu={_GPU_FIELDS}", "--format=csv,nounits,noheader"])
    except Exception as e:
        # Old drivers don't support the compute_cap/driver_version query
        # fields and exit non-zero. Retry once with the legacy 9-field set so
        # the existing GPU vitals footer keeps working; the new fields just
        # come back null.
        log.debug("11-field nvidia-smi query failed, retrying legacy fields: %s", e)
        try:
            gpu_csv = await _run_smi(
                exe, [f"--query-gpu={_GPU_FIELDS_LEGACY}", "--format=csv,nounits,noheader"]
            )
            legacy = True
        except Exception as e2:
            return {"online": False, "error": str(e2)}

    first = gpu_csv.strip().splitlines()[0] if gpu_csv.strip() else ""
    if not first:
        return {"online": False, "error": "nvidia-smi returned no data"}

    parts = [p.strip() for p in first.split(",")]
    if len(parts) < (9 if legacy else 11):
        return {"online": False, "error": f"unexpected nvidia-smi shape: {first!r}"}

    result: dict[str, Any] = {
        "online": True,
        "name": parts[0],
        "memory": {
            "used_mib": _parse_float(parts[1]),
            "total_mib": _parse_float(parts[2]),
            "free_mib": _parse_float(parts[3]),
        },
        "utilization": {
            "gpu_pct": _parse_float(parts[4]),
            "memory_pct": _parse_float(parts[5]),
        },
        "temperature_c": _parse_float(parts[6]),
        "power": {
            "draw_w": _parse_float(parts[7]),
            "limit_w": _parse_float(parts[8]),
        },
        # [guided setup] parts[9]=compute_cap, parts[10]=driver_version —
        # MUST stay the last two query fields; compute_cap derives
        # compute_type (>=7.0 -> native fp16), driver_version supports a
        # too-old-driver warning.
        "compute_cap": None if legacy else _parse_float(parts[9]),
        "driver_version": None if legacy else parts[10],
        "processes": [],
    }

    try:
        proc_csv = await _run_smi(
            exe, ["--query-compute-apps=pid,process_name,used_memory", "--format=csv,nounits,noheader"]
        )
        for line in proc_csv.strip().splitlines():
            cols = [c.strip() for c in line.split(",")]
            if len(cols) < 3:
                continue
            try:
                pid = int(cols[0])
            except ValueError:
                continue
            result["processes"].append(
                {
                    "pid": pid,
                    "name": cols[1] or "unknown",
                    "memory_mib": _parse_float(cols[2]) or 0.0,
                }
            )
    except Exception as e:
        log.debug("compute-apps query failed (non-fatal): %s", e)

    return result
