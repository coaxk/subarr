"""#666: which background loops are currently paused by operator config.

The Health page reports loop failures, but a loop that is deliberately held
off looks the same as one that is running. `paused_states` maps the affected
task names to the label + link the page shows.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_QUEUE_FEEDER_PAUSED = {"label": "feed paused", "href": "/queue"}
_COVERAGE_WALK_OFF = {"label": "coverage walk off", "href": "/rules"}


def paused_states(schedule) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if schedule is None:
        return out
    try:
        if schedule.get_rules().queue_paused:
            out["queue-feeder"] = dict(_QUEUE_FEEDER_PAUSED)
    except Exception as e:  # noqa: BLE001
        log.debug("paused_states: get_rules failed: %s", type(e).__name__)
    try:
        row = schedule.get_schedule("coverage_walk")
        if row is not None and not row.enabled:
            out["scheduler"] = dict(_COVERAGE_WALK_OFF)
    except Exception as e:  # noqa: BLE001
        log.debug("paused_states: get_schedule failed: %s", type(e).__name__)
    return out
