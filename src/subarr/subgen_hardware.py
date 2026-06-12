"""Guided subgen setup — pure hardware logic (spec §2.3/§2.4).

Three responsibilities, all side-effect-free so the spec matrices are
directly table-testable:
  - parse_smi_csv: one parser for BOTH the auto-detected nvidia-smi line
    and the user-pasted CSV (manual fallback) — same code path, same trust.
  - derive_compute_type: compute_type is a derived optimization, not a
    user preference. There is a correct answer per card + model.
  - model_fit / recommend_model: fit math keys off memory.TOTAL with a
    headroom rule — the live free figure is a snapshot shown only as an
    advisory (the card may be momentarily busy or momentarily idle).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Approximate steady-state VRAM footprint in MB (CTranslate2/faster-whisper,
# beam_size 5). float16 figures; int8 variants roughly halve. Deliberately
# conservative — the headroom margin below absorbs measurement noise.
MODEL_FOOTPRINT_MB: dict[str, int] = {
    "tiny": 1000,
    "base": 1200,
    "small": 1800,
    "medium": 3500,
    "large-v3": 5500,
}
# Room left for CUDA context + other tenants (Plex/Ollama bursts).
HEADROOM_MB = 1536
# compute_cap >= 7.0 (Volta+) has native FP16; below it float16 is emulated
# and SLOWER than int8.
FP16_MIN_COMPUTE_CAP = 7.0

MODEL_VRAM_RECOMMEND_MB = (
    ("large-v3", 10240),  # >=10GB
    ("medium", 6144),  # >=6GB
    ("small", 0),  # anything less / CPU
)


@dataclass(frozen=True)
class GpuInfo:
    name: str
    vram_total_mb: int
    compute_cap: float | None = None
    vram_free_mb: int | None = None
    driver_version: str | None = None


@dataclass(frozen=True)
class ComputeDerivation:
    compute_type: str
    reason: str


def _num(field: str) -> float | None:
    m = re.search(r"[\d.]+", field)
    return float(m.group()) if m else None


def parse_smi_csv(line: str) -> GpuInfo | None:
    """Parse one nvidia-smi CSV row: `name, memory.total[, memory.free],
    compute_cap[, driver_version]`. Tolerates ' MiB' suffixes (paste without
    ,nounits). Returns None on anything unparseable — callers fall through
    to manual entry, never crash."""
    parts = (
        [p.strip() for p in (line or "").strip().splitlines()[0].split(",")] if (line or "").strip() else []
    )
    if len(parts) < 3:
        return None
    name = parts[0]
    total = _num(parts[1])
    if not name or total is None or total <= 0:
        return None
    # Disambiguate the two shapes by column count:
    #   3 cols: name, total, compute_cap            (the paste command)
    #   5 cols: name, total, free, cap, driver      (the auto-detect query)
    if len(parts) >= 5:
        free, cap, driver = _num(parts[2]), _num(parts[3]), parts[4]
    else:
        free, cap, driver = None, _num(parts[2]), None
    if cap is None or cap > 20:  # compute_cap is single-digit.dot; a big number here means columns are off
        return None
    return GpuInfo(
        name=name,
        vram_total_mb=int(total),
        vram_free_mb=int(free) if free is not None else None,
        compute_cap=cap,
        driver_version=driver,
    )


def model_fit(model: str, compute_type: str, *, vram_total_mb: int | None) -> str:
    """'fits' | 'tight' | 'no_fit' against TOTAL VRAM with headroom.
    CPU (vram None) always 'fits' — the cost there is speed, not memory."""
    if vram_total_mb is None:
        return "fits"
    footprint = MODEL_FOOTPRINT_MB.get(
        model.split(".")[0].replace("-turbo", ""), MODEL_FOOTPRINT_MB["large-v3"]
    )
    if compute_type.startswith("int8"):
        footprint = footprint // 2
    if footprint + HEADROOM_MB <= vram_total_mb:
        return "fits"
    if footprint <= vram_total_mb:
        return "tight"
    return "no_fit"


def derive_compute_type(
    *, compute_cap: float | None, vram_total_mb: int | None, model: str
) -> ComputeDerivation:
    """Spec §2.3 matrix. compute_cap None == no GPU."""
    if compute_cap is None or vram_total_mb is None:
        return ComputeDerivation("int8", "no GPU — int8 is the fast CPU mode")
    if compute_cap < FP16_MIN_COMPUTE_CAP:
        return ComputeDerivation(
            "int8",
            f"compute capability {compute_cap:g} is pre-Volta, so float16 would actually be slower — int8 is the fast path",
        )
    if model_fit(model, "float16", vram_total_mb=vram_total_mb) == "fits":
        return ComputeDerivation("float16", "your GPU supports fast half-precision")
    return ComputeDerivation(
        "int8_float16",
        f"fits {model} in less VRAM while keeping FP16 speed",
    )


def recommend_model(*, vram_total_mb: int | None) -> str:
    if vram_total_mb is None:
        return "small"
    for model, min_mb in MODEL_VRAM_RECOMMEND_MB:
        if vram_total_mb >= min_mb:
            return model
    return "small"
