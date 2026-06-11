# #157 Phase 2 — Fleet Crash Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sanitized crash aggregates (`{ExcType:module:line → count}`) flow from every consenting install to the self-owned worker, so a post-release exception spike is visible fleet-wide within hours — plus fix the **live telemetry outage** discovered during recon (worker 400-rejects every real ping since the 2026-06-08 hardening: `docker_tier must be a string` vs subarr sending a number).

**Architecture:** Subarr side — supervised-loop failures (the #79 class) already funnel through `TaskHealthStore.record_failure`; that single choke point additionally records a sanitized `(exc_type, location)` row to a new `crash_events` table (migration 018). `location` = innermost subarr frame as `module:line` (module name only, never paths). The daily ping gains `crash_counts_24h: {"ExcType:module:line": n}` — same object shape as the existing `error_counts_30d`, so the worker validates it with machinery it already has. Worker side — accept number `docker_tier` (coerce), allowlist `crash_counts_24h`, persist to a `crashes_json` column (schema/002), regression-test with subarr's REAL payload verbatim. Privacy line: type + module:line + count ONLY — never messages, tracebacks, or paths (those stay local in task_health).

**Tech Stack:** Python/FastAPI + SQLite migrations (subarr); CF Worker + D1 + vitest (subarr-telemetry; push-to-main deploys via CI — verified working, secrets set 2026-06-08; D1 migrations are MANUAL `wrangler d1 execute`).

---

## Part A — worker (`C:\Projects\subarr-telemetry`) — do FIRST (unblocks live ingestion)

### Task A1: regression test with subarr's real payload + docker_tier fix

**Files:** Modify `src/worker.js`, `test/worker.test.js`

- [ ] Test (vitest): POST subarr's verbatim 1.4.0 payload (incl. `docker_tier: 1` number, `scheduler_enabled: false`, `error_counts_30d: {}`, `scheduler_mode: null`) → expect 200. Pin this forever — the 06-08 hardening broke prod because no test used a real client payload.
- [ ] Fix: in the value validation, accept `docker_tier` as string OR finite number (coerce `String(v)` before storage). Keep the string rules for actual strings.
- [ ] `npm test` green.

### Task A2: `crash_counts_24h` ingestion

**Files:** Modify `src/worker.js`, `test/worker.test.js`; Create `schema/002_crashes.sql`

- [ ] Tests: payload with `crash_counts_24h: {"NameError:coverage_engine:1639": 4}` → 200 and stored; reject >64 keys, non-number values, XSS chars in keys (reuse the `error_counts_30d` object-validator tests as the template).
- [ ] Implement: add `crash_counts_24h` to `ALLOWED_FIELDS`; validate with the same object-field validator as `error_counts_30d`; `recordPing()` stores `crashes_json` column.
- [ ] `schema/002_crashes.sql`: `ALTER TABLE pings ADD COLUMN crashes_json TEXT;`
- [ ] `npm test` green; commit + push main → CI deploys; **manually** run the D1 migration: `wrangler d1 execute subarr-telemetry --remote --file=schema/002_crashes.sql`.
- [ ] Verify LIVE: re-POST the dev instance's real payload → 200 (outage fixed); POST one with crash_counts_24h → 200. Watch the deploy job actually ran (it's-in-CI lesson).

## Part B — subarr (`C:\Projects\subarr`), branch `feat/157-phase2-crash-telemetry`

### Task B1: migration 018 + CrashStore

**Files:** Create `src/subarr/migrations/018_crash_events.sql`, `src/subarr/crash_store.py`; Test `tests/test_crash_telemetry.py`

```sql
-- 018_crash_events.sql — #157 Phase 2: sanitized fleet crash aggregates.
-- Stores ONLY exception class + module:line (never message/traceback/path);
-- the rich local detail lives in task_health. Aggregated into the daily
-- telemetry ping as crash_counts_24h.
CREATE TABLE IF NOT EXISTS crash_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exc_type    TEXT NOT NULL,
    location    TEXT NOT NULL,
    occurred_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crash_events_occurred ON crash_events (occurred_at);
```

`crash_store.py` (ErrorStore pattern: best-effort, never raises):
- `crash_location(exc) -> str` — walk `exc.__traceback__` to the INNERMOST frame whose module is under the `subarr` package; return `"{module}:{lineno}"` with module = `frame.f_globals.get('__name__','?')` stripped of the leading `subarr.`; fallback `"?:0"`. Cap 64 chars (worker key limit, with exc_type prefix budgeted).
- `CrashStore.record(exc)` — insert `(type(exc).__name__[:40], crash_location(exc), now)`.
- `CrashStore.counts_since(cutoff) -> dict[str,int]` keyed `"{exc_type}:{location}"`.
- `CrashStore.prune(days=30)` — housekeeping on boot (error_events has none; don't repeat that gap).

Tests: location extraction from a real raised exception inside a subarr module; record + counts_since; key format contains no path separators.

### Task B2: hook the choke point

**Files:** Modify `src/subarr/task_health.py` (record_failure), `src/subarr/app.py` (wire store)

- [ ] `TaskHealthStore.__init__` gains optional `crash_recorder: callable | None`; `record_failure` calls `self._crash_recorder(exc)` best-effort (try/except pass) after extracting exc_type.
- [ ] app.py lifespan: build `CrashStore(settings.db_path)`, prune on boot, pass `crash_recorder=crash_store.record` where TaskHealthStore is constructed; stash on `app_.state.crashes`.
- [ ] Test: a supervised-loop-style `record_failure` with a raised exception lands a row in crash_events.

### Task B3: payload + transparency

**Files:** Modify `src/subarr/telemetry.py`, `src/subarr/static/v1/home-hifi/settings.jsx`

- [ ] `TelemetryPayload` gains `crash_counts_24h: dict[str,int]`; built from `CrashStore.counts_since(now - 86400)` (mirror `_error_counts_30d` wiring); in `to_dict()`.
- [ ] Test: build payload after recording crashes → key present, sanitized shape, capped at 64 entries (drop lowest counts beyond cap — worker rejects >64 keys).
- [ ] TelemetryPanel: one row "Crash reports (24h): N types · M total" sourced from `last_payload.crash_counts_24h` (the JSON preview already discloses the full detail). Rebuild settings bundle only.

### Task B4: gate + live verify + ship

- [ ] Full suite + ruff + bandit; deploy-check on :9923: `POST /api/telemetry/send-now` → state shows healthy ping (200 from the fixed worker), preview shows `crash_counts_24h`.
- [ ] PR `feat(#157): Phase 2 — fleet crash telemetry`, merge on green; comment on #157 closing Phase 2 scope (Phase 3 stays parked).

## Self-Review
1. Spec coverage: sanitized aggregate ✅ (B1 key format), opt-out ✅ (rides existing consent — no new toggle), transparency ✅ (B3 row + JSON preview), CF Worker + D1 ✅ (A2), regression alarm = stats view explicitly deferred to the v1.1 dashboard (store now, render later — noted on #157). Outage fix ✅ (A1, with the real-payload pin).
2. No placeholders; code shapes given where novel, established patterns referenced by exact name (ErrorStore, `_error_counts_30d`).
3. Types: `crash_counts_24h: dict[str,int]` consistent across CrashStore.counts_since → payload → worker object validator.
