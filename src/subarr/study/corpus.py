"""Clip selection for the Phase 2 study.

Pure logic only -- no ffmpeg, no filesystem walk. A corpus of one kind of
material measures one kind of material, so selection is stratified by dialogue
density rather than taken as whatever the library returns first.
"""

from __future__ import annotations

from enum import Enum


class DensityBand(Enum):
    SPARSE = "sparse"
    NORMAL = "normal"
    DENSE = "dense"
    VERY_DENSE = "very_dense"


def band_for_cues_per_minute(cpm: float) -> DensityBand:
    """Bands are contiguous and cover the whole range, so nothing is unclassifiable."""
    if cpm < 8.0:
        return DensityBand.SPARSE
    if cpm < 18.0:
        return DensityBand.NORMAL
    if cpm < 30.0:
        return DensityBand.DENSE
    return DensityBand.VERY_DENSE


def stratify(items: list[tuple[str, float]], *, per_band: int) -> list[tuple[str, float]]:
    """Take up to ``per_band`` items from each populated band.

    Sorted before slicing so the same library yields the same corpus -- a study
    you cannot re-run against the same material is not reproducible.
    """
    buckets: dict[DensityBand, list[tuple[str, float]]] = {}
    for name, cpm in sorted(items):
        buckets.setdefault(band_for_cues_per_minute(cpm), []).append((name, cpm))
    out: list[tuple[str, float]] = []
    for band in DensityBand:
        out.extend(buckets.get(band, [])[:per_band])
    return out
