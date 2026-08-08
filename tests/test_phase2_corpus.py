# tests/test_phase2_corpus.py
from subarr.study.corpus import (
    DensityBand,
    band_for_cues_per_minute,
    stratify,
    stratify_eligible,
)


def test_bands_partition_the_range_with_no_gaps():
    assert band_for_cues_per_minute(2.0) is DensityBand.SPARSE
    assert band_for_cues_per_minute(12.0) is DensityBand.NORMAL
    assert band_for_cues_per_minute(25.0) is DensityBand.DENSE
    assert band_for_cues_per_minute(40.0) is DensityBand.VERY_DENSE


def test_stratify_takes_an_equal_quota_from_each_populated_band():
    items = [("sparse%d" % i, 2.0) for i in range(10)] + [("dense%d" % i, 25.0) for i in range(10)]
    picked = stratify(items, per_band=3)
    bands = {band_for_cues_per_minute(d) for _, d in picked}
    assert len(picked) == 6
    assert len(bands) == 2


def test_stratify_does_not_invent_items_for_an_empty_band():
    picked = stratify([("a", 2.0)], per_band=5)
    assert len(picked) == 1


def test_stratify_is_deterministic_for_a_given_input():
    items = [("c%d" % i, float(i)) for i in range(40)]
    assert stratify(items, per_band=2) == stratify(items, per_band=2)


def test_eligible_pick_backfills_within_the_band_rather_than_returning_short():
    # Real case: 5 of the 14 very_dense candidates are 33-second sample files.
    # A blind pick takes them, extraction fails, and the band silently lands
    # under quota -- which is exactly the imbalance the quota exists to stop.
    items = [("bad1", 40.0), ("bad2", 40.0), ("ok1", 40.0), ("ok2", 40.0)]
    picked = stratify_eligible(items, per_band=2, eligible=lambda name: not name.startswith("bad"))
    assert [n for n, _ in picked] == ["ok1", "ok2"]


def test_eligible_pick_returns_short_only_when_the_band_is_genuinely_exhausted():
    items = [("bad1", 40.0), ("ok1", 40.0)]
    picked = stratify_eligible(items, per_band=5, eligible=lambda name: not name.startswith("bad"))
    assert [n for n, _ in picked] == ["ok1"]


def test_eligible_pick_stops_probing_once_the_quota_is_met():
    # The predicate is an ffprobe call over CIFS. Probing every candidate in a
    # 787-item band to fill a quota of 9 would cost minutes for nothing.
    probed: list[str] = []

    def eligible(name: str) -> bool:
        probed.append(name)
        return True

    items = [("c%02d" % i, 40.0) for i in range(50)]
    stratify_eligible(items, per_band=3, eligible=eligible)
    assert len(probed) == 3
