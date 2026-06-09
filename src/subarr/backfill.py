"""#116 — throttled library backfill (selection/throttle CORE).

Borrowed from Sonarr_Backfiller: instead of dumping the whole subtitle-gap
backlog into subgen at once (and flooding the GPU), trickle it — keep subgen's
queue topped up to a target depth, adding a bounded batch each tick. On a big
library this is the difference between a 500-job stampede and steadily closing
gaps in the background.

This module is the PURE decision math — given the eligible gap backlog, the
current queue depth, and the config, decide what to enqueue NOW. It does no
I/O, holds no state, and couples to nothing, so it's fully unit-tested.

WIRING (the documented next step, NOT built here — needs care + live tests):
  - eligibility: pull gaps from the coverage cache, filtered to probe-VERIFIED
    gaps only (never raw Bazarr-wanted) and past the settle-window (#117);
    order them (oldest? Tautulli-popularity? user-pickable).
  - queue depth: read subgen's live queue length (SubgenClient.queue()).
  - tick: call select_backfill_batch() on the scheduler cadence, enqueue the
    returned gaps, surface progress ("backfilling: N gaps, M in flight").
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BackfillConfig:
    # Default OFF — backfill never runs unless the user opts in. (Even when on,
    # the caller must still only feed VERIFIED gaps; see module docstring.)
    enabled: bool = False
    # Hold subgen's queue at this depth — the throttle that prevents a stampede.
    target_queue_depth: int = 3
    # Max gaps to enqueue per tick when below target (smooths bursty draining).
    batch: int = 5


def select_backfill_batch(gaps, queue_depth: int, config: BackfillConfig) -> list:
    """Return the slice of ``gaps`` to enqueue right now, bounded so the queue
    never overfills past ``target_queue_depth`` and never grows by more than
    ``batch`` in one tick. Pure: ``gaps`` is the caller's pre-filtered, ordered
    eligible backlog; the return is its front slice.

    NOTE (#66/#116): with the queue-authority feeder in place, the per-tick
    throttle is the FEEDER (it drains the pending queue into subgen at
    target_depth). So the live wiring loads the whole eligible backlog into the
    pending queue via ``eligible_backfill_items`` below and lets the feeder
    trickle it — this batch-selector is retained for callers that want to
    trickle directly into subgen without the pending queue."""
    if not config.enabled:
        return []
    headroom = config.target_queue_depth - queue_depth
    if headroom <= 0:
        return []
    n = min(headroom, config.batch, len(gaps))
    return list(gaps[:n])


def eligible_backfill_items(items, rules, in_flight_paths=None):
    """The full backfill backlog: every VERIFIED gap that auto-queue *would*
    queue — ignoring the per-run cap, the dashboard-mode gate, and the
    settle-window (an explicit backfill is a deliberate "close the backlog"
    action). Reuses auto_queue.evaluate() so backfill honours the SAME quality
    filters as auto-queue (min_score, deny_languages, monitored, embedded-EN,
    stale-disk) — backfill never grabs English-original junk or already-covered
    files. The feeder's target_depth provides the gentle drain; we just load the
    pending queue with the whole eligible set (dedup keeps re-runs idempotent)."""
    import dataclasses

    from .auto_queue import evaluate
    from .schedule_store import MODE_AUTO_RULES

    eval_rules = dataclasses.replace(
        rules,
        mode=MODE_AUTO_RULES,
        max_per_run=10_000_000,
        settle_minutes=0,
    )
    decisions = evaluate(items, eval_rules, in_flight_paths=in_flight_paths)
    return [d.item for d in decisions if d.action == "queue"]
