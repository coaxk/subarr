"""#117 settle-window: hold freshly-imported gaps out of auto-queue for
`settle_minutes` after Sonarr/Radarr imported them, so Bazarr/providers get
first crack at a real sub. Opt-in (settle_minutes=0 = off). Manual transcribe
bypasses (it never runs through evaluate()).

Covers:
  - settle_seconds_left pure logic (disabled / no ts / within / elapsed)
  - evaluate() gates a settling item with a "settling (Xm left)" skip
  - evaluate() is a no-op when settle_minutes=0 (default)
  - evaluate() lets through items past the window or with no import_ts
  - AutoQueueRules.settle_minutes round-trips through to_dict/from_dict
"""

from __future__ import annotations

from subarr.auto_queue import evaluate, settle_seconds_left
from subarr.coverage_engine import CoverageItem
from subarr.schedule_store import AutoQueueRules, MODE_AUTO_RULES


NOW = 1_780_000_000.0


def _item(title: str, import_ts: float | None) -> CoverageItem:
    return CoverageItem(
        media_type="episode",
        title=title,
        verification_state="verified",
        score=500,
        monitored=True,
        canonical_path=f"TV/{title}",
        import_ts=import_ts,
    )


def _rules(settle_minutes: int) -> AutoQueueRules:
    # Isolate the settle gate from the other filters.
    return AutoQueueRules(
        mode=MODE_AUTO_RULES,
        min_score=0,
        deny_languages=[],
        require_monitored=False,
        skip_stale_disk=False,
        skip_embedded_en=False,
        settle_minutes=settle_minutes,
    )


# ─── settle_seconds_left ────────────────────────────────────────────


def test_settle_seconds_left_disabled():
    # settle_minutes=0 → never settling, regardless of import_ts
    assert settle_seconds_left(_item("a", NOW), 0, NOW) == 0


def test_settle_seconds_left_no_import_ts():
    assert settle_seconds_left(_item("a", None), 60, NOW) == 0


def test_settle_seconds_left_within_window():
    # imported 10 min ago, 60 min window → ~50 min (3000s) left
    left = settle_seconds_left(_item("a", NOW - 600), 60, NOW)
    assert 2990 <= left <= 3000


def test_settle_seconds_left_elapsed():
    # imported 2h ago, 60 min window → window long gone
    assert settle_seconds_left(_item("a", NOW - 7200), 60, NOW) == 0


# ─── evaluate() gate ────────────────────────────────────────────────


def test_evaluate_gates_settling_item():
    items = [_item("Fresh", NOW - 600)]  # imported 10 min ago
    decisions = evaluate(items, _rules(60), now=NOW)
    d = decisions[0]
    assert d.action == "skip"
    assert "settling" in d.reason
    assert "50m left" in d.reason  # ceil(3000s) = 50 min


def test_evaluate_no_gate_when_disabled():
    # Default opt-in OFF: a freshly-imported item still queues normally.
    items = [_item("Fresh", NOW - 60)]
    decisions = evaluate(items, _rules(0), now=NOW)
    assert decisions[0].action == "queue"


def test_evaluate_no_gate_after_window():
    items = [_item("Old", NOW - 7200)]  # 2h ago, 60 min window elapsed
    decisions = evaluate(items, _rules(60), now=NOW)
    assert decisions[0].action == "queue"


def test_evaluate_no_gate_without_import_ts():
    # No import timestamp (older file / outside lookback) → not settling.
    items = [_item("Unknown", None)]
    decisions = evaluate(items, _rules(60), now=NOW)
    assert decisions[0].action == "queue"


def test_evaluate_settle_ceils_to_whole_minutes():
    # imported 1s ago, 5 min window → 299s left → ceil → 5m
    items = [_item("JustNow", NOW - 1)]
    decisions = evaluate(items, _rules(5), now=NOW)
    assert "5m left" in decisions[0].reason


# ─── persistence round-trip ─────────────────────────────────────────


def test_settle_minutes_round_trips():
    r = AutoQueueRules(settle_minutes=90)
    assert r.to_dict()["settle_minutes"] == 90
    assert AutoQueueRules.from_dict(r.to_dict()).settle_minutes == 90


def test_settle_minutes_defaults_to_zero_when_absent():
    # Backward-compat: a rules blob saved before #117 has no settle_minutes.
    legacy = {"mode": MODE_AUTO_RULES, "min_score": 200}
    assert AutoQueueRules.from_dict(legacy).settle_minutes == 0
