"""Probe-gate enforcement at the coverage router: unverified rows are
sticky (no hide/lang filter may drop them) and bucket counts are exposed.
"""

from __future__ import annotations


def _body():
    return {
        "items": [
            # verified, embedded EN + stale disk → the filters SHOULD drop it
            {"media_type": "episode", "verification_state": "verified",
             "embedded_en": "EN", "has_sub_on_disk": True,
             "bazarr": {"missing_subtitles": ["nl"]}},
            # unprobed — must survive every filter
            {"media_type": "episode", "verification_state": "unprobed",
             "embedded_en": "EN", "has_sub_on_disk": True,
             "bazarr": {"missing_subtitles": ["nl"]}},
            # probe_failed — must survive every filter
            {"media_type": "episode", "verification_state": "probe_failed",
             "embedded_en": "EN", "has_sub_on_disk": True,
             "bazarr": {"missing_subtitles": ["nl"]}},
        ],
        "totals": {"items": 3, "episodes": 3, "movies": 0},
    }


def test_unverified_rows_are_sticky_under_filters():
    from subarr.routers.coverage import _apply_filters_and_pack
    out = _apply_filters_and_pack(
        _body(), now=0.0,
        hide_embedded_en=True, hide_stale_disk=True,
        hide_english_audio=True, hide_pending_download=True,
        only_wanted_langs="eng",  # would also drop the nl-missing rows
    )
    states = [i["verification_state"] for i in out["items"]]
    assert "verified" not in states          # verified row dropped by filters
    assert states.count("unprobed") == 1     # sticky
    assert states.count("probe_failed") == 1  # sticky


def test_verification_bucket_counts_exposed():
    from subarr.routers.coverage import _apply_filters_and_pack
    out = _apply_filters_and_pack(
        _body(), now=0.0,
        hide_embedded_en=False, hide_stale_disk=False,
        hide_english_audio=False, hide_pending_download=False,
        only_wanted_langs="",
    )
    # no filters: all three kept; counts reflect them
    assert out["totals"]["verification"] == {
        "verified": 1, "unprobed": 1, "probe_failed": 1, "unsupported": 0,
    }
