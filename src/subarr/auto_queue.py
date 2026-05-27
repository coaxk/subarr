"""Auto-queue decision engine.

Given a CoverageReport + AutoQueueRules, decide which items to enqueue.
Returns a list of decisions: each is one of
  (item, "queue"|"skip", reason).

No side effects — the caller (scheduler or HTTP handler) loops over the
"queue" decisions and posts each to /api/coverage/queue (or invokes the
underlying flow directly).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .coverage_engine import CoverageItem
from .schedule_store import AutoQueueRules, MODE_AUTO_RULES, MODE_DASHBOARD, MODE_MANUAL_CONFIRM

log = logging.getLogger(__name__)


@dataclass
class Decision:
    item: CoverageItem
    action: str  # "queue" or "skip"
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.item.title,
            "media_type": self.item.media_type,
            "episode_number": self.item.episode_number,
            "score": self.item.score,
            "action": self.action,
            "reason": self.reason,
            "sonarr_episode_id": self.item.bazarr_episode_id,
            "canonical_path": self.item.canonical_path,
        }


def evaluate(items: list[CoverageItem], rules: AutoQueueRules) -> list[Decision]:
    """Apply rules to a list of coverage items.

    In MODE_DASHBOARD, nothing is queued (everyone gets 'skip: mode=dashboard').
    In other modes, items are filtered by:
      - min_score
      - allow/deny languages
      - allow/deny tags
      - require_monitored
      - skip_stale_disk
    Surviving items are sorted by descending score and capped at max_per_run.
    """
    decisions: list[Decision] = []

    if rules.mode == MODE_DASHBOARD:
        for item in items:
            decisions.append(Decision(item, "skip", "mode=dashboard"))
        return decisions

    eligible: list[CoverageItem] = []
    for item in items:
        skip_reason = _filter_reason(item, rules)
        if skip_reason:
            decisions.append(Decision(item, "skip", skip_reason))
        else:
            eligible.append(item)

    # Sort eligible by score desc (CoverageItems already sorted, but defensive).
    eligible.sort(key=lambda i: i.score, reverse=True)
    queued = eligible[:rules.max_per_run]
    cut = eligible[rules.max_per_run:]
    for item in queued:
        decisions.append(Decision(item, "queue", f"matches rules (mode={rules.mode})"))
    for item in cut:
        decisions.append(Decision(item, "skip", f"over max_per_run={rules.max_per_run}"))

    return decisions


def _filter_reason(item: CoverageItem, rules: AutoQueueRules) -> str | None:
    if rules.skip_stale_disk and item.has_sub_on_disk:
        return "stale: .srt already on disk"
    if rules.require_monitored and item.monitored is False:
        return "not monitored"
    if item.score < rules.min_score:
        return f"score {item.score} < min_score {rules.min_score}"
    lang = (item.original_language or "").strip()
    if rules.allow_languages and lang not in rules.allow_languages:
        return f"language {lang!r} not in allow_languages"
    if lang and lang in rules.deny_languages:
        return f"language {lang!r} in deny_languages"
    tags_set = set(item.tags or [])
    if rules.allow_tags and not tags_set.intersection(rules.allow_tags):
        return "no allowed tag matched"
    if rules.deny_tags and tags_set.intersection(rules.deny_tags):
        return "denied tag matched"
    return None
