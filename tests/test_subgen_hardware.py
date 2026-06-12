"""Guided subgen setup: pure hardware logic (spec §2.3/§2.4).

No I/O here — parsing nvidia-smi CSV, deriving compute_type, and VRAM fit
math are all table-driven pure functions so every row of the spec matrix
is directly testable.
"""

from __future__ import annotations

import pytest


# ── nvidia-smi CSV parsing (auto-detect AND the paste box use the same parser)


def test_parse_smi_csv_full_row(subarr_env):
    from subarr.subgen_hardware import parse_smi_csv

    gpu = parse_smi_csv("NVIDIA GeForce RTX 3060, 12288, 4343, 8.6, 610.52")
    assert gpu.name == "NVIDIA GeForce RTX 3060"
    assert gpu.vram_total_mb == 12288
    assert gpu.vram_free_mb == 4343
    assert gpu.compute_cap == 8.6
    assert gpu.driver_version == "610.52"


def test_parse_smi_csv_paste_variant_without_free_and_driver(subarr_env):
    # The manual paste command asks for name,memory.total,compute_cap only.
    from subarr.subgen_hardware import parse_smi_csv

    gpu = parse_smi_csv("NVIDIA GeForce GTX 1070, 8192, 6.1")
    assert gpu.name == "NVIDIA GeForce GTX 1070"
    assert gpu.vram_total_mb == 8192
    assert gpu.vram_free_mb is None
    assert gpu.compute_cap == 6.1


def test_parse_smi_csv_units_suffix_tolerated(subarr_env):
    # nvidia-smi without ,nounits appends " MiB" — paste box must cope.
    from subarr.subgen_hardware import parse_smi_csv

    gpu = parse_smi_csv("NVIDIA RTX A4000, 16376 MiB, 8.6")
    assert gpu.vram_total_mb == 16376


def test_parse_smi_csv_four_column_mispaste_rejected(subarr_env):
    # A 4-col paste (name,total,free,cap) would mis-assign free->cap;
    # the cap>20 sanity guard must reject it -> manual entry, never wrong numbers.
    from subarr.subgen_hardware import parse_smi_csv

    assert parse_smi_csv("NVIDIA GeForce RTX 3060, 12288, 4343, 8.6") is None


def test_parse_smi_csv_multi_gpu_first_line_wins(subarr_env):
    from subarr.subgen_hardware import parse_smi_csv

    two = "NVIDIA RTX 4090, 24564, 9.0\nNVIDIA GTX 1660, 6144, 7.5"
    gpu = parse_smi_csv(two)
    assert gpu.name == "NVIDIA RTX 4090"
    assert gpu.vram_total_mb == 24564


def test_parse_smi_csv_garbage_returns_none(subarr_env):
    from subarr.subgen_hardware import parse_smi_csv

    assert parse_smi_csv("") is None
    assert parse_smi_csv("bash: nvidia-smi: command not found") is None


# ── compute_type derivation (every row of the spec §2.3 matrix)


@pytest.mark.parametrize(
    "compute_cap,vram_mb,model,expected,reason_fragment",
    [
        (None, None, "small", "int8", "no GPU"),  # CPU
        (8.6, 12288, "large-v3", "float16", "half-precision"),  # fits
        (8.6, 6144, "large-v3", "int8_float16", "less VRAM"),  # tight
        (6.1, 8192, "medium", "int8", "pre-Volta"),  # old card
    ],
)
def test_derive_compute_type_matrix(subarr_env, compute_cap, vram_mb, model, expected, reason_fragment):
    from subarr.subgen_hardware import derive_compute_type

    d = derive_compute_type(compute_cap=compute_cap, vram_total_mb=vram_mb, model=model)
    assert d.compute_type == expected
    assert reason_fragment.lower() in d.reason.lower()


# ── model fit (total-with-headroom, never the live-free snapshot)


def test_model_fit_indicators(subarr_env):
    from subarr.subgen_hardware import model_fit

    # 12GB card: large-v3 (~5.5GB + headroom) fits comfortably
    assert model_fit("large-v3", "float16", vram_total_mb=12288) == "fits"
    # 6GB card: large-v3 float16 is tight (int8_float16 territory)
    assert model_fit("large-v3", "float16", vram_total_mb=6144) == "tight"
    # 2GB card: large-v3 doesn't fit at all
    assert model_fit("large-v3", "float16", vram_total_mb=2048) == "no_fit"
    # CPU (no VRAM): every model "fits" (speed is the cost, not memory)
    assert model_fit("large-v3", "int8", vram_total_mb=None) == "fits"


def test_model_fit_normalizes_model_variants(subarr_env):
    from subarr.subgen_hardware import model_fit

    # .en and -turbo variants map onto their base footprints
    assert model_fit("small.en", "float16", vram_total_mb=12288) == "fits"
    assert model_fit("large-v3-turbo", "float16", vram_total_mb=2048) == "no_fit"
    # int8 halving: medium (3500 -> 1750) + headroom fits a 4GB card
    assert model_fit("medium", "int8", vram_total_mb=4096) == "fits"


def test_derive_compute_type_never_claims_false_fit(subarr_env):
    from subarr.subgen_hardware import derive_compute_type

    d = derive_compute_type(compute_cap=8.6, vram_total_mb=2048, model="large-v3")
    assert d.compute_type == "int8_float16"
    assert "fits" not in d.reason.lower().split(" is ")[0] or "larger than" in d.reason
    # plainly: the reason must steer to a smaller model, not claim a fit
    assert "smaller model" in d.reason


def test_recommend_model_by_vram(subarr_env):
    from subarr.subgen_hardware import recommend_model

    assert recommend_model(vram_total_mb=12288) == "large-v3"  # >=10GB
    assert recommend_model(vram_total_mb=8192) == "medium"  # 6-10GB
    assert recommend_model(vram_total_mb=4096) == "small"  # <6GB
    assert recommend_model(vram_total_mb=None) == "small"  # CPU
