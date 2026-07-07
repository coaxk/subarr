# #157 Phase-1 gap-fill — crash visibility finishing touches

**Issue:** [#157](https://github.com/coaxk/subarr/issues/157) — observability: crash visibility + supervised background tasks (instance-local first).
**Date:** 2026-07-07
**Scope:** close the three genuine remaining gaps in #157 Phase 1. The core (a `TaskHealthStore` supervising the 7 boot loops, `/api/health/tasks`, the `health.jsx` page + header badge, `CrashStore` seed) is **already shipped** — an audit found Phase 1 ~80% built. This slice finishes it: the `SUBARR_DEBUG` verbose knob, supervision of the audio-audit walker, and visibility of subarr's **own** logs (both a recent-errors panel and a live stream).

## Context (what already exists — do not rebuild)

- `TaskHealthStore` + `task_health` table (migration 012): per-task `last_success_at`, `last_error_at`, `last_error_type`, `last_error_detail` (traceback, capped), `consecutive_failures`, `total_runs`, `total_failures`, `expected_interval_s`. `TaskHealth.is_unhealthy` = `consecutive_failures >= 3` OR (never-succeeded-but-failed) OR stale (`now - last_success_at > 3 * expected_interval_s`, **only when an interval is set**).
- All 7 boot loops instrumented (scheduler, completion-watcher, subgen-watchdog, queue-feeder, coverage-cache, dashboard-cache, update-checker) via `_health.record_success/record_failure` in their except blocks; registered at boot ([app.py:352-364](../../../src/subarr/app.py)).
- `GET /api/health/tasks` + `POST /api/health/tasks/{name}/run`; `health.jsx` renders the roster with expandable tracebacks + run-now + GitHub-issue prefill; `chrome.jsx` header badge reads `any_unhealthy`.
- `record_failure` already feeds an optional `crash_recorder` → `CrashStore` (Phase 2 fleet seed).
- Logs page + `GET /api/logs/events`: SSE of **subgen container** logs only (`docker_ops.stream_subgen_logs`, [routers/logs.py:17](../../../src/subarr/routers/logs.py)).

## The three gaps

1. **No `SUBARR_DEBUG` knob** — logging is hardcoded `INFO` ([config.py:126](../../../src/subarr/config.py)); httpx/httpcore pinned to `WARNING`. No way to "go nuts locally."
2. **Audio-audit walker unsupervised** — `AudioAuditWalker` ([audio_audit.py](../../../src/subarr/audio_audit.py)) has no `task_health` wiring and isn't registered; it's opt-in (POST `/api/audio-audit/start`), so a run-level crash never reaches the Health surface (only per-file errors land in its own `AuditState.errors`).
3. **subarr's own logs are invisible in the UI** — the Health page shows only the *last captured traceback* per task; there's no way to see recent warnings/errors across subarr, nor a live tail of subarr's own log (only subgen's).

## Components

### A. `SUBARR_DEBUG` verbose knob
`config.py`: add `debug: bool` parsed from `SUBARR_DEBUG` (`"1"/"true"/"yes"/"on"`, default off), mirroring the existing `retime_enabled` pattern. In the logging setup: when `debug`, root level → `DEBUG` and the httpx/httpcore loggers are left at `INFO` (un-pinned from the `WARNING` silence) so request detail shows. Default behaviour byte-for-byte unchanged when off.

### B. Supervise the audio-audit walker (unified roster)
- Register `"audio-audit"` in `task_health` at boot with `expected_interval_s=None` — no interval means the staleness branch never fires, so a foreground task shows failures/streak without a false "stale" alarm, and sits alongside the 7 loops on the Health page.
- Wire `AudioAuditWalker._health = app.state.task_health` where the walker is constructed ([app.py:594](../../../src/subarr/app.py)).
- In the walker's run entry point: on a **run-level** exception (the outer drive, not per-file), call `record_failure("audio-audit", exc)`; on a clean run completion, `record_success("audio-audit")`. Per-file errors continue to accumulate in `AuditState.errors` unchanged. Best-effort (`getattr(self, "_health", None)`) so a missing health store never breaks a run — matching the 7 loops' pattern.

### C. In-process log ring (shared core for both surfaces)
A new `log_ring.py`: a `logging.Handler` subclass holding a bounded `deque(maxlen=N)` of structured records (`ts`, `level`, `logger_name`, `message`, `exc_text` when present), plus an asyncio fan-out for live subscribers (SSE). Installed on the root logger during logging setup (Component A's area), capturing at `INFO` (so the live tail is useful) — the snapshot endpoint filters by level.

- **Snapshot** `GET /api/logs/recent?level=WARNING&limit=200` → the ring filtered/tail'd. Read-only, auth-gated.
- **Live SSE** `GET /api/logs/subarr/events?tail=200` → replays the last `tail` records then streams new ones, reusing the `StreamingResponse`/event-stream shape of the subgen endpoint.

Capacity: `maxlen` ~1000 (bounded memory; a ring, never persisted — this is local, ephemeral, no privacy cost per the issue's transmit-boundary principle).

### D. Frontend surfaces
- **Health page** (`health.jsx`): a "Recent errors" panel fed by `/api/logs/recent?level=WARNING`, rendered below the task roster (level chip, logger, message, expandable `exc_text`). It gives the *context* around a red task the single last-traceback can't.
- **Logs page** (`logs.jsx`): a **source switcher** (`subgen | subarr`). `subgen` keeps `/api/logs/events`; `subarr` connects `/api/logs/subarr/events`. The existing filter/pause/search/autoscroll chrome is reused for both.

## Data flow

```
any subarr logger.{warning,error,exception} ─▶ LogRing handler ─┬─▶ GET /api/logs/recent      ─▶ Health "Recent errors" panel
                                                                └─▶ GET /api/logs/subarr/events ─▶ Logs page (source: subarr)
audio-audit run crash ─▶ walker._health.record_failure("audio-audit") ─▶ task_health roster (no-interval) + CrashStore
                          (+ logger.exception in the walker ─▶ LogRing, so it also shows in both log surfaces)
SUBARR_DEBUG=1 ─▶ root logger DEBUG + httpx/httpcore un-silenced ─▶ richer records in the ring + stdout
```

## Error handling

- LogRing handler must **never raise into logging** — its `emit` wraps formatting in a try/except that drops the record on failure (a logging handler that throws would break every log call). Bounded deque = no unbounded growth.
- SSE subscriber cleanup on client disconnect (drop the queue); a slow subscriber gets a bounded queue that drops oldest rather than back-pressuring the handler.
- Audit-walker health calls are best-effort (`getattr` guard), never fail a run.
- `/api/logs/recent` + the SSE endpoint tolerate a missing ring (returns empty / closes) so tests and early-boot are safe.

## Testing

- **Config**: `SUBARR_DEBUG` env-parse (default off, on via `1/true`, blank → off) — mirrors `test_config_retime`.
- **Audit-walker supervision**: a fake `_health` store records `record_success` on a completed run and `record_failure` on an injected run-level crash; per-file errors still land in `AuditState.errors`; a missing `_health` never raises.
- **LogRing**: captures records; caps at `maxlen`; snapshot filters by level and honours `limit`; `emit` swallows a formatting error instead of raising; a subscriber receives a newly-emitted record.
- **Endpoints**: `GET /api/logs/recent` returns seeded records filtered by level (via `app_with_stub`); SSE endpoint smoke (opens, replays tail, is auth-gated).
- **Frontend**: pure helpers where possible (the source-switcher state, the recent-errors row formatter); vitest.
- **Regression**: existing subgen log stream, the 7-loop health roster, and default (non-debug) logging unchanged.

## Acceptance

1. `SUBARR_DEBUG=1` raises subarr to DEBUG logging (and shows httpx detail); unset = today's INFO behaviour exactly.
2. An audio-audit run that crashes at the run level surfaces as an unhealthy `audio-audit` row on the Health page (no false "stale" when idle); a clean run shows healthy/last-success.
3. The Health page shows a "Recent errors" panel of subarr's own recent WARN+ records with expandable tracebacks.
4. The Logs page can switch to a live tail of subarr's own log alongside subgen.
5. Full suite + vitest green; ruff clean; no bundle drift.

## Out of scope

- Phase 2 fleet telemetry (already seeded via `CrashStore`); Phase 3 perf/APM trends.
- Persisting the log ring (deliberately ephemeral/in-memory).
- Per-arr-instance health breakdown ("instance-local" in #157 means *this install vs fleet*, not per-Sonarr-instance).
- Payload/perf debug-points beyond the `SUBARR_DEBUG` log-level flip (added ad hoc later where useful).

## Risk tier

**Tier-1** — additive throughout: one config flag, one store-wiring on an existing task, one new in-memory logging handler + two read endpoints, and two UI surfaces. No data-model change, no migration, no writeback. The load-bearing care points: the LogRing `emit` must be exception-proof (a throwing handler breaks all logging), and the audit-walker registration must use `expected_interval_s=None` so an idle walker never false-alarms as stale.
