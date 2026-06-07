-- 012_task_health.sql
--
-- #157 Phase 1: supervised background tasks. Every long-running loop
-- (coverage refresh, dashboard refresh, scheduler, completion watcher,
-- update checker, subgen watchdog) records the outcome of each cycle here so
-- a silent crash (the #79 class: catch-log-warning-and-keep-looping) becomes
-- visible in the UI instead of freezing quietly for hours.
--
-- One row per task name, upserted every cycle (mirrors update_checks).
-- The TaskHealthStore writes; /api/health/tasks reads.

CREATE TABLE IF NOT EXISTS task_health (
    task_name            TEXT PRIMARY KEY,         -- 'coverage-cache', 'scheduler', ...
    last_success_at      REAL,                     -- epoch of most recent successful cycle
    last_error_at        REAL,                     -- epoch of most recent failed cycle
    last_error_type      TEXT,                     -- exception class name
    last_error_detail    TEXT,                     -- traceback (capped) the silent loops used to discard
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_runs           INTEGER NOT NULL DEFAULT 0,
    total_failures       INTEGER NOT NULL DEFAULT 0,
    expected_interval_s  REAL,                     -- cadence; powers the "no success in too long" staleness check
    updated_at           REAL NOT NULL
);
