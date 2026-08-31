"""#234: on-demand control over background loops (run-now).

The list + health status of every loop already exists (#157 task_health ->
/api/health/tasks). This adds the *control* half: a small registry of which
loops can be triggered on demand, plus a runner that invokes the right one.

#252 extends the MVP's two loops to the refresh-style loops as well, via
triggers the loops publish themselves. Pause/resume and edit-trigger remain
the stretch.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from .task_health import redact_secrets

log = logging.getLogger(__name__)

# Loops that publish their OWN no-arg trigger when they start (#252).
#
# They cannot be driven from app.state alone. coverage-cache's refresh() takes
# bundle, probe_store, audio_lang_store, probe_walker and caps_provider, and
# several of those are closures inside the loop function rather than attributes
# anywhere. Rebuilding that argument list here would be a SECOND COPY of the
# loop's wiring, free to drift from it -- and the drift would be silent, since
# the button would go on working while running something subtly different from
# what the schedule runs. So the owner registers its own thunk and this module
# only looks it up.
SELF_REGISTERED = frozenset(
    {
        "coverage-cache",
        "dashboard-cache",
        "subgen-watchdog",
        "completion-watcher",
    }
)

# task_health task_name -> gets a "Run now" button.
#
# Two omissions that look like oversights and are deliberate:
#
#   scheduler     A tick FIRES DUE JOBS. Everything else here refreshes a cache
#                 or re-polls a probe, so running it early costs one request
#                 and changes nothing else. Starting real scans from a button
#                 that looks identical to those is a different promise, and it
#                 needs its own confirmation rather than this registry.
#   db-integrity  Already has a dedicated manual endpoint from the #291 Slice B
#                 deep-integrity work. A second trigger here would be a
#                 divergent copy of one control.
RUNNABLE = frozenset({"update-checker", "queue-feeder"}) | SELF_REGISTERED

# Registrations hang off app.state, never module state, so two app instances
# (or two tests) cannot see each other's triggers.
_TRIGGERS_ATTR = "job_triggers"


def can_run_now(task_name: str) -> bool:
    return task_name in RUNNABLE


def register_trigger(app_state: Any, task_name: str, fn: Callable[[], Any]) -> None:
    """Publish a no-arg trigger for `task_name`.

    Called by the loop that owns the job, because only the loop has the job's
    dependencies in scope. `fn` may be sync or async.
    """
    triggers = getattr(app_state, _TRIGGERS_ATTR, None)
    if triggers is None:
        triggers = {}
        setattr(app_state, _TRIGGERS_ATTR, triggers)
    triggers[task_name] = fn


async def _call(fn: Callable[[], Any]) -> None:
    """Await the result only when there is one, so an owner can register a
    plain function without wrapping it (queue-feeder's kick() is sync)."""
    result = fn()
    if inspect.isawaitable(result):
        await result


async def run_job(app_state: Any, task_name: str) -> bool:
    """Invoke a job's on-demand trigger.

    Returns True if it fired; False if the job isn't runnable, its component
    isn't on app.state yet, or the trigger itself failed.

    Never raises, except CancelledError. That promise used to live only in this
    docstring: nothing caught an exception from the trigger, and the endpoint
    does not catch either, so a throwing trigger returned a 500. It survived
    because the only two triggers were an update poll and a feeder kick. A
    coverage refresh calls out to Bazarr, Sonarr and Plex, where a failure is
    ordinary rather than exceptional.

    CancelledError propagates on purpose: swallowing it would break shutdown.
    """
    try:
        triggers = getattr(app_state, _TRIGGERS_ATTR, None) or {}
        fn = triggers.get(task_name)
        if fn is not None:
            await _call(fn)
            return True

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
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Redacted, not exc_info: integrations put their credential in the
        # request URL and httpx embeds the URL in the exception text, while
        # subarr serves its own recent log over /api/logs/recent (#414) on an
        # install that may have no auth.
        log.warning(
            "run-now trigger for %r failed: %s",
            task_name,
            redact_secrets(f"{type(exc).__name__}: {exc}"),
        )
        return False
