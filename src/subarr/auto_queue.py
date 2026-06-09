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
import time
from dataclasses import dataclass
from typing import Any

from .coverage_engine import CoverageItem
from .schedule_store import AutoQueueRules, MODE_DASHBOARD

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


def settle_seconds_left(item: CoverageItem, settle_minutes: int, now: float) -> int:
    """#117: seconds remaining in the settle window for this gap, or 0 if it
    isn't settling (disabled, no import timestamp, or window already elapsed).

    A freshly-imported file is held out of auto-queue for `settle_minutes`
    after Sonarr/Radarr imported it, giving Bazarr/providers first crack at a
    real sub. Pure + time-relative so the UI can compute the live "Xm left"
    from the item's `import_ts` without a stale cached value.
    """
    if settle_minutes <= 0 or not item.import_ts:
        return 0
    return max(0, int(item.import_ts + settle_minutes * 60 - now))


def evaluate(
    items: list[CoverageItem],
    rules: AutoQueueRules,
    in_flight_paths: set[str] | None = None,
    now: float | None = None,
) -> list[Decision]:
    """Apply rules to a list of coverage items.

    In MODE_DASHBOARD, nothing is queued (everyone gets 'skip: mode=dashboard').
    In other modes, items are filtered by:
      - in_flight_paths (canonical_paths already queued + awaiting completion)
      - min_score
      - allow/deny languages
      - allow/deny tags
      - require_monitored
      - skip_stale_disk
      - skip_embedded_en
    Surviving items are sorted by descending score and capped at max_per_run.

    `in_flight_paths` is the set of canonical paths currently in the provenance
    ledger with `completed_at IS NULL`. Skipping these prevents the scheduler
    in manual_confirm mode from recreating identical pending walks every
    tick (Bazarr's wanted list doesn't shrink until subgen writes the .srt,
    which can be 5-15 minutes after we enqueue).
    """
    decisions: list[Decision] = []
    in_flight = in_flight_paths or set()
    now = now if now is not None else time.time()

    if rules.mode == MODE_DASHBOARD:
        for item in items:
            decisions.append(Decision(item, "skip", "mode=dashboard"))
        return decisions

    eligible: list[CoverageItem] = []
    for item in items:
        # Cheap in-flight match: the canonical_path on the item is the
        # *directory* (series-level); the provenance ledger holds the
        # *file* path. So we check whether ANY provenance entry's path
        # starts with this item's series path + matches the episode-number
        # pattern. Done in O(items × in_flight) which is fine at scale —
        # in_flight is bounded by the number of recently-queued files.
        if _is_in_flight(item, in_flight):
            decisions.append(
                Decision(item, "skip", "already in flight (scan submitted, awaiting subgen completion)")
            )
            continue
        # Probe-gate: never auto-queue a row subarr hasn't verified by
        # probing the file. An un-probed/probe-failed row can't be trusted
        # as a real gap (it may already have an embedded sub subgen would
        # skip) — hold it until the probe runs.
        if getattr(item, "verification_state", "verified") != "verified":
            decisions.append(Decision(item, "skip", "unverified — not probed yet"))
            continue
        # #117 settle-window: hold freshly-imported gaps so Bazarr/providers
        # get first crack. Opt-in (settle_minutes=0 → no-op). Manual
        # transcribe bypasses this entirely (it never reaches evaluate()).
        left = settle_seconds_left(item, rules.settle_minutes, now)
        if left > 0:
            mins = (left + 59) // 60  # ceil to whole minutes
            decisions.append(
                Decision(
                    item,
                    "skip",
                    f"settling ({mins}m left) — letting Bazarr/providers land a real sub first",
                )
            )
            continue
        skip_reason = _filter_reason(item, rules)
        if skip_reason:
            decisions.append(Decision(item, "skip", skip_reason))
        else:
            eligible.append(item)

    # Sort eligible by score desc (CoverageItems already sorted, but defensive).
    eligible.sort(key=lambda i: i.score, reverse=True)
    queued = eligible[: rules.max_per_run]
    cut = eligible[rules.max_per_run :]
    for item in queued:
        decisions.append(Decision(item, "queue", f"matches rules (mode={rules.mode})"))
    for item in cut:
        decisions.append(Decision(item, "skip", f"over max_per_run={rules.max_per_run}"))

    return decisions


def _episode_pattern(item: CoverageItem) -> str | None:
    """Bazarr '1x3' → 's01e03' substring we can match against ledger paths."""
    en = item.episode_number
    if not en or "x" not in en:
        return None
    try:
        season, ep = en.split("x")
        return f"s{int(season):02d}e{int(ep):02d}"
    except (ValueError, TypeError):
        return None


def _is_in_flight(item: CoverageItem, in_flight_paths: set[str]) -> bool:
    """True if any in_flight path matches this item."""
    if not in_flight_paths:
        return False
    # File-level match (when coverage already resolved the file_canonical_path)
    if item.file_canonical_path and item.file_canonical_path in in_flight_paths:
        return True
    # Movie match: item.canonical_path IS the file for movies
    if item.media_type == "movie" and item.canonical_path in in_flight_paths:
        return True
    # Episode match: item.canonical_path is the series dir, ledger has files
    # like TV/Foo/Season 1/Foo - S01E03 ... .mkv — check series prefix +
    # episode-number substring.
    if item.media_type == "episode" and item.canonical_path:
        pat = _episode_pattern(item)
        series_prefix = item.canonical_path.rstrip("/") + "/"
        if pat:
            for p in in_flight_paths:
                if p.startswith(series_prefix) and pat in p.lower():
                    return True
    return False


def _filter_reason(item: CoverageItem, rules: AutoQueueRules) -> str | None:
    if rules.skip_stale_disk and item.has_sub_on_disk:
        return "stale: .srt already on disk"
    if rules.skip_embedded_en and item.embedded_en in {"EN", "EN(SDH)"}:
        return f"embedded English already present ({item.embedded_en})"
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
