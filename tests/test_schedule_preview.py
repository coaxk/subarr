"""/api/schedule/preview reconstructs CoverageItem objects from the cached
coverage snapshot (dicts) so it can evaluate rules without a 60-90s
rebuild. This guards that reconstruction against to_dict / dataclass-field
drift — if a field evaluate() relies on stops round-tripping, this fails.
"""

from __future__ import annotations


def test_reconstruct_coverage_item_from_snapshot_dict(subarr_env):
    from subarr.coverage_engine import CoverageItem
    from subarr.routers.schedule import _COVERAGE_ITEM_FIELDS

    # A snapshot dict carries extra computed keys (reason, score_reasons,
    # cache_age_s, …) that are NOT constructor fields. Reconstruction must
    # drop them and preserve the fields evaluate() reads.
    snap_dict = {
        "media_type": "episode",
        "title": "Foreign Show",
        "verification_state": "verified",
        "score": 500,
        "monitored": True,
        "canonical_path": "TV/Foreign",
        "missing_subtitles": ["en"],
        "tags": ["premium"],
        "audio_langs": ["kor"],
        "original_language": "Korean",
        "embedded_en": None,
        "has_sub_on_disk": False,
        "pending_download": False,
        "audio_label_suspect": False,
        # computed-only keys that must NOT break the constructor:
        "reason": "no-track",
        "score_reasons": ["foreign"],
        "cache_age_s": 12.3,
    }
    item = CoverageItem(**{k: v for k, v in snap_dict.items() if k in _COVERAGE_ITEM_FIELDS})

    # Fields evaluate() depends on survived the round-trip.
    assert item.verification_state == "verified"
    assert item.score == 500
    assert item.monitored is True
    assert item.tags == ["premium"]
    assert item.missing_subtitles == ["en"]
    assert item.canonical_path == "TV/Foreign"
    assert item.original_language == "Korean"


def test_evaluate_runs_on_reconstructed_items(subarr_env):
    """End-to-end of the reconstruction → evaluate path (auto_rules mode so
    the score/monitored filters actually run)."""
    from subarr.auto_queue import evaluate
    from subarr.coverage_engine import CoverageItem
    from subarr.routers.schedule import _COVERAGE_ITEM_FIELDS
    from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES

    dicts = [
        {
            "media_type": "episode",
            "title": "A",
            "verification_state": "verified",
            "score": 500,
            "monitored": True,
            "canonical_path": "TV/A",
            "deny_key": "ignored",
        },
        {
            "media_type": "episode",
            "title": "B",
            "verification_state": "unprobed",
            "score": 500,
            "monitored": True,
            "canonical_path": "TV/B",
        },
    ]
    items = [CoverageItem(**{k: v for k, v in d.items() if k in _COVERAGE_ITEM_FIELDS}) for d in dicts]
    rules = AutoQueueRules(mode=MODE_AUTO_RULES, min_score=0, deny_languages=[], require_monitored=True)
    decisions = evaluate(items, rules)
    by_title = {d.item.title: d for d in decisions}
    assert by_title["A"].action == "queue"  # verified + passes filters
    assert by_title["B"].action == "skip"  # unprobed → gated out
    assert "unverified" in by_title["B"].reason
