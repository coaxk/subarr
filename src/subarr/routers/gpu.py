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
from ..error_detail import safe_error
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


# ───── #363: NVML fallback ──────────────────────────────────────────────────
#
# On WSL2 + nvidia-container-toolkit the GPU compute libs (libcuda,
# libnvidia-ml.so.1) are mounted but the nvidia-smi BINARY is not — so the
# binary path above finds nothing even though CUDA runs fine. NVML IS present,
# so query it directly (via NVIDIA's official pure-Python binding) to populate
# the same vitals. nvidia-smi stays primary; this is the no-config fallback.


def _nvml_shape(
    *,
    name: str,
    mem_used_b: int | None,
    mem_total_b: int | None,
    mem_free_b: int | None,
    util_gpu_pct: int | None,
    util_mem_pct: int | None,
    temp_c: int | None,
    power_draw_mw: int | None,
    power_limit_mw: int | None,
    cc_major: int | None,
    cc_minor: int | None,
    driver_version: str | None,
    procs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure: shape raw NVML readings into the exact /api/gpu result dict (bytes →
    MiB, milliwatts → W, (major, minor) → the compute_cap float nvidia-smi emits)."""

    def _mib(b: int | None) -> float | None:
        return None if b is None else round(b / (1024 * 1024), 1)

    def _w(mw: int | None) -> float | None:
        return None if mw is None else round(mw / 1000.0, 1)

    cc = float(f"{cc_major}.{cc_minor}") if cc_major is not None and cc_minor is not None else None
    return {
        "online": True,
        "name": name,
        "memory": {
            "used_mib": _mib(mem_used_b),
            "total_mib": _mib(mem_total_b),
            "free_mib": _mib(mem_free_b),
        },
        "utilization": {"gpu_pct": util_gpu_pct, "memory_pct": util_mem_pct},
        "temperature_c": temp_c,
        "power": {"draw_w": _w(power_draw_mw), "limit_w": _w(power_limit_mw)},
        "compute_cap": cc,
        "driver_version": driver_version,
        "processes": procs,
    }


def _nvml_try(fn: Any) -> Any:
    """Best-effort single NVML query — some vitals (power, temp) are unsupported
    on some GPUs and raise; treat those as None rather than failing the snapshot."""
    try:
        return fn()
    except Exception:
        return None


def _nvml_processes(pynvml: Any, handle: Any) -> list[dict[str, Any]]:
    """Running compute processes via NVML (best-effort; [] on any error)."""
    out: list[dict[str, Any]] = []
    try:
        procs = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
    except Exception:
        return out
    for p in procs:
        used = getattr(p, "usedGpuMemory", None)
        name = _nvml_try(lambda pid=p.pid: pynvml.nvmlSystemGetProcessName(pid))
        if isinstance(name, bytes):
            name = name.decode(errors="replace")
        out.append(
            {
                "pid": int(p.pid),
                "name": name or "unknown",
                "memory_mib": round(used / (1024 * 1024), 1) if used else 0.0,
            }
        )
    return out


def _nvml_status() -> dict[str, Any] | None:
    """NVML snapshot for when the nvidia-smi binary is absent. Returns the same
    shape as the nvidia-smi path, or None when NVML itself is unavailable (no GPU
    / lib not mounted) so the caller reports offline exactly as before. Never
    raises — import, init, and every query are guarded."""
    try:
        import pynvml  # nvidia-ml-py; dlopens libnvidia-ml.so.1 lazily
    except Exception:
        return None
    try:
        pynvml.nvmlInit()
    except Exception:
        return None  # lib absent / no GPU — behave like nvidia-smi-missing
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        name = pynvml.nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode(errors="replace")
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        util = _nvml_try(lambda: pynvml.nvmlDeviceGetUtilizationRates(h))
        temp = _nvml_try(lambda: pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU))
        cc = _nvml_try(lambda: pynvml.nvmlDeviceGetCudaComputeCapability(h))
        driver = _nvml_try(pynvml.nvmlSystemGetDriverVersion)
        if isinstance(driver, bytes):
            driver = driver.decode(errors="replace")
        return _nvml_shape(
            name=name,
            mem_used_b=getattr(mem, "used", None),
            mem_total_b=getattr(mem, "total", None),
            mem_free_b=getattr(mem, "free", None),
            util_gpu_pct=getattr(util, "gpu", None) if util else None,
            util_mem_pct=getattr(util, "memory", None) if util else None,
            temp_c=temp,
            power_draw_mw=_nvml_try(lambda: pynvml.nvmlDeviceGetPowerUsage(h)),
            power_limit_mw=_nvml_try(lambda: pynvml.nvmlDeviceGetEnforcedPowerLimit(h)),
            cc_major=cc[0] if cc else None,
            cc_minor=cc[1] if cc else None,
            driver_version=driver,
            procs=_nvml_processes(pynvml, h),
        )
    except Exception as e:
        log.debug("nvml fallback failed: %s", e)
        return None
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


@router.get("/gpu")
async def gpu_status() -> dict[str, Any]:
    exe = _nvidia_smi_path()
    if exe is None:
        # #363: no nvidia-smi binary (common on WSL2-Docker) — try NVML, which
        # the container toolkit DOES mount, before reporting offline.
        nvml = _nvml_status()
        if nvml is not None:
            return nvml
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
            return {"online": False, "error": safe_error(e2)}

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
