"""#357 — parse_robust_detect additively captures per-chunk probability."""

from __future__ import annotations

from subarr.arena import parse_robust_detect


def test_captures_per_chunk_probability_as_chunks_conf():
    resp = {
        "aggregate": {"language": "gl", "n_agreeing": 1, "n_total": 3},
        "chunks": [
            {"language": "gl", "probability": 0.91},
            {"language": "es", "probability": 0.88},
            {"language": "fr", "probability": 0.76},
        ],
    }
    out = parse_robust_detect(resp)
    assert out is not None
    # additive: existing keys untouched
    assert out["languages_heard"] == ["es", "fr", "gl"]
    assert out["n_total"] == 3
    # new: ordered (lang, prob) per chunk, preserving chunk order
    assert out["chunks_conf"] == [("gl", 0.91), ("es", 0.88), ("fr", 0.76)]


def test_absent_probability_degrades_to_none_confidence():
    resp = {
        "aggregate": {"language": "nl", "n_agreeing": 3, "n_total": 3},
        "chunks": [{"language": "nl"}, {"language": "nl"}, {"language": "nl"}],
    }
    out = parse_robust_detect(resp)
    assert out is not None
    # graceful: probability missing -> None, no crash, existing behaviour intact
    assert out["chunks_conf"] == [("nl", None), ("nl", None), ("nl", None)]
    assert out["unanimous"] is True


def test_no_chunks_still_returns_none():
    assert parse_robust_detect({"aggregate": {"n_total": 0}, "chunks": []}) is None
