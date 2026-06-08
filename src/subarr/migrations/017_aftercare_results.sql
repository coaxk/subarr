-- 017_aftercare_results.sql
--
-- #156 Track A: one row per completed transcription job, carrying subarr's own
-- quality judging of the produced .srt (readability + failure-mode signals).
-- Stores EVERY job (foundation for #95 passive tuning); the UI surfaces flagged
-- ones. `reviewed_at` NULL = pending review. Keyed by file path; a requeue
-- appends a new row (history), the list view shows the latest per path.

CREATE TABLE IF NOT EXISTS aftercare_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_path   TEXT NOT NULL,
    completed_at     REAL NOT NULL,
    composite        REAL NOT NULL,
    cue_count        INTEGER NOT NULL,
    flagged          INTEGER NOT NULL,          -- 0/1
    readability_json TEXT,                      -- ReadabilityReport.to_dict()
    signals_json     TEXT,                      -- score_entrant signals dict
    source           TEXT,                      -- 'subgenscan' | 'gaps' | 'manual' | ...
    reviewed_at      REAL,                      -- NULL = pending
    created_at       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aftercare_pending
    ON aftercare_results (flagged, reviewed_at, completed_at);
CREATE INDEX IF NOT EXISTS idx_aftercare_path
    ON aftercare_results (canonical_path, completed_at);
