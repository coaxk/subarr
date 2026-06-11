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
