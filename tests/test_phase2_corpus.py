# tests/test_phase2_corpus.py
from subarr.study.corpus import DensityBand, band_for_cues_per_minute, stratify


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
