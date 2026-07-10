-- #364 slice 1 — forced-segment scan-result cache (idempotence).
--
-- One row per file (keyed by canonical_path). The (mtime, size) pair makes the
-- deep foreign-scene scan idempotent: the manual walker AND the at-import hook
-- both consult this store, so an unchanged file is never re-scanned and GPU is
-- never re-burned. A changed file (new mtime/size) is a miss and re-scans.
-- Mirrors media_probe's cache-key discipline (probe_store.py).
--
-- status: 'scanned' (>=1 forced span emitted) | 'none' (qualified, no foreign
-- scene found) | 'bailed' (mostly-foreign / mistagged — recorded, nothing
-- emitted). n_spans/total_ms carry the light Aftercare-note summary.
CREATE TABLE IF NOT EXISTS forced_segment_scans (
    canonical_path  TEXT PRIMARY KEY,
    mtime           REAL,
    size            INTEGER,
    status          TEXT NOT NULL,
    n_spans         INTEGER NOT NULL DEFAULT 0,
    total_ms        INTEGER NOT NULL DEFAULT 0,
    scanned_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_forced_segment_scans_status ON forced_segment_scans (status);
