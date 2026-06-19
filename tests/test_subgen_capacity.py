from subarr.subgen_capacity import subgen_capacity_free


def test_capacity_free_when_n_none_is_always_true():
    assert subgen_capacity_free(processing_count=99, arena_in_flight=99, n=None) is True


def test_single_job_serializes():
    assert subgen_capacity_free(processing_count=0, arena_in_flight=0, n=1) is True
    assert subgen_capacity_free(processing_count=1, arena_in_flight=0, n=1) is False
    assert subgen_capacity_free(processing_count=0, arena_in_flight=1, n=1) is False


def test_default_two_allows_one_batch_plus_one_sweep():
    assert subgen_capacity_free(processing_count=1, arena_in_flight=0, n=2) is True
    assert subgen_capacity_free(processing_count=1, arena_in_flight=1, n=2) is False


def test_large_n_never_throttles():
    assert subgen_capacity_free(processing_count=4, arena_in_flight=1, n=1000) is True
