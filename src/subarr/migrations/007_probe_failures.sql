-- Probe-gate foundation: remember files that FAILED to probe so the
-- Coverage probe-gate can show a distinct "couldn't analyze" state instead
-- of silently treating them as never-probed (and silently dropping them).
-- A successful probe clears the row (see ProbeStore.upsert).
CREATE TABLE IF NOT EXISTS probe_failures (
    canonical_path TEXT PRIMARY KEY,
    error          TEXT NOT NULL,
    failed_at      REAL NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 1
);
