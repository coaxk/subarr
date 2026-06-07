-- 014_pending_queue.sql
--
-- #66 / #116 queue authority: subarr's own pending queue, in front of subgen.
-- Producers (manual / gaps / backfill / auto) enqueue here; a depth-aware
-- feeder drains it into subgen one job at a time. Pending rows are fully
-- subarr-controlled → reorder / promote / demote / pause. Once submitted to
-- subgen they're cancel-only (subgen owns its queue).
--
-- `position` is a REAL sort key so reordering is O(1) (set to the midpoint of
-- two neighbours; no mass renumber). Ordering for the feeder is
-- (priority DESC, position ASC). Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS pending_queue (
    id                      TEXT PRIMARY KEY,          -- uuid4
    canonical_path          TEXT NOT NULL,
    position                REAL NOT NULL,             -- sort key within a priority bucket
    priority                INTEGER NOT NULL DEFAULT 0,-- manual(2) > gaps(1) > backfill(0)
    status                  TEXT NOT NULL DEFAULT 'pending', -- pending|submitted|done|error
    audio_language_override TEXT,
    task                    TEXT,                      -- transcribe|translate|NULL
    source                  TEXT NOT NULL,             -- manual|gaps|backfill|auto
    created_at              REAL NOT NULL,
    submitted_at            REAL,
    error                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_queue_status_order
    ON pending_queue(status, priority DESC, position ASC);

-- Fast dedup lookup ("is this path already pending/submitted?").
CREATE INDEX IF NOT EXISTS idx_pending_queue_path
    ON pending_queue(canonical_path);
