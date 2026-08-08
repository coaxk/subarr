"""Clip selection for the Phase 2 study.

Pure logic only -- no ffmpeg, no filesystem walk. A corpus of one kind of
material measures one kind of material, so selection is stratified by dialogue
density rather than taken as whatever the library returns first.
"""

from __future__ import annotations

from collections.abc import Callable
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


def stratify_eligible(
    items: list[tuple[str, float]],
    *,
    per_band: int,
    eligible: Callable[[str], bool],
) -> list[tuple[str, float]]:
    """Like :func:`stratify`, but skips items ``eligible`` rejects and backfills
    from the rest of the same band.

    Blind picking silently under-fills a band. The real case: 5 of the 14
    ``very_dense`` candidates are 33-second sample files masquerading as
    episodes. A blind pick took them, extraction refused them, and the band
    landed at 9 of 14 -- the exact imbalance the quota exists to prevent, and
    it only surfaced because the extraction counts were checked afterwards.

    ``eligible`` is an ffprobe call over CIFS, so it is invoked lazily and
    only until each band's quota is met. Probing all 787 items of a band to
    fill a quota of 9 would cost minutes and buy nothing.
    """
    buckets: dict[DensityBand, list[tuple[str, float]]] = {}
    for name, cpm in sorted(items):
        buckets.setdefault(band_for_cues_per_minute(cpm), []).append((name, cpm))
    out: list[tuple[str, float]] = []
    for band in DensityBand:
        taken = 0
        for name, cpm in buckets.get(band, []):
            if taken >= per_band:
                break
            if eligible(name):
                out.append((name, cpm))
                taken += 1
    return out


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
