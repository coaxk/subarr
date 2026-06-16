"""#234: on-demand control over background loops (run-now).

The list + health status of every loop already exists (#157 task_health →
/api/health/tasks). This adds the *control* half: a small registry of which
loops can be triggered on demand, plus a runner that invokes the right one.

Scope is deliberately the safe, idempotent triggers (re-poll updates, nudge
the queue feeder). Periodic/self-driven loops (scheduler, watchdog, caches)
stay monitor-only — surfaced for status, no manual button. Pause/resume +
edit-trigger are the stretch (a follow-up issue), not this MVP.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# task_health task_name -> on-demand trigger. Only these get a "Run now" button.
RUNNABLE = {"update-checker", "queue-feeder"}


def can_run_now(task_name: str) -> bool:
    return task_name in RUNNABLE


async def run_job(app_state: Any, task_name: str) -> bool:
    """Invoke a job's on-demand trigger. Returns True if it fired, False if the
    job isn't runnable or its component isn't on app.state yet. Never raises —
    a control action must not 500 the page."""
    if task_name == "update-checker":
        checker = getattr(app_state, "update_checker", None)
        if checker is None:
            return False
        await checker.refresh_now()  # re-poll all tracked products now
        return True
    if task_name == "queue-feeder":
        feeder = getattr(app_state, "queue_feeder", None)
        if feeder is None:
            return False
        feeder.kick()  # wake the feeder to drain pending immediately
        return True
    return False
