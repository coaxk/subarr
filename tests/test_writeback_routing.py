"""#161 Phase 3 — writeback routing: writes go to the instance owning the row."""

from __future__ import annotations


def test_writeback_stack_resolves_instances(writeback_stack):
    from subarr.coverage_engine import clients_for

    b = writeback_stack.bundle
    # an @anime/ row resolves to the anime instances of all three services
    rc = clients_for(b, "@anime/Naruto/Season 1/Naruto.S01E01.mkv")
    assert rc.bazarr is b._clients["bazarr"]["anime"]
    assert rc.sonarr is b._clients["sonarr"]["anime"]
    assert rc.radarr is b._clients["radarr"]["anime"]
    # a default-library row resolves to instance 0
    rc0 = clients_for(b, "ShowTV/Season 1/ShowTV.S01E01.mkv")
    assert rc0.bazarr is b._clients["bazarr"][""]
    assert rc0.sonarr is b._clients["sonarr"][""]
