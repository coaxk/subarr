-- 001_baseline.sql
--
-- Captures the cumulative subarr v0.1.0 schema as the v1.0 baseline.
-- All tables use CREATE TABLE IF NOT EXISTS so existing v0.x installs
-- upgrade transparently (their tables already exist, this migration
-- is recorded in schema_versions without re-creating anything).
--
-- Future column additions / table changes go in 002_*.sql, 003_*.sql, ...

-- ─── scan_store ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scans (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    status        TEXT NOT NULL,
    reverse       INTEGER NOT NULL DEFAULT 0,
    current_index INTEGER NOT NULL DEFAULT 0,
    paths_json    TEXT NOT NULL,
    results_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans (created_at DESC);

-- ─── provenance ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subs_generated (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_path               TEXT NOT NULL,
    series_id                    INTEGER,
    sonarr_episode_id            INTEGER,
    radarr_movie_id              INTEGER,
    scan_id                      TEXT,
    source                       TEXT NOT NULL,
    subgen_version               TEXT,
    queued_at                    REAL NOT NULL,
    completed_at                 REAL,
    bazarr_scan_triggered_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_subs_generated_path
    ON subs_generated (canonical_path);
CREATE INDEX IF NOT EXISTS idx_subs_generated_pending
    ON subs_generated (completed_at) WHERE completed_at IS NULL;

-- ─── schedule_store ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schedule_config (
    name             TEXT PRIMARY KEY,
    enabled          INTEGER NOT NULL DEFAULT 0,
    kind             TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 360,
    daily_hhmm       TEXT NOT NULL DEFAULT '03:00',
    day_of_week      INTEGER NOT NULL DEFAULT 0,
    last_run_at      REAL,
    next_run_at      REAL,
    last_result      TEXT,
    probe_roots      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS auto_queue_rules (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    payload_json     TEXT NOT NULL,
    updated_at       REAL NOT NULL
);

-- ─── enrichment ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS lang_enrichment (
    canonical_path  TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    raw_response    TEXT,
    iso_code        TEXT,
    model           TEXT,
    inferred_at     REAL NOT NULL,
    error           TEXT
);

-- ─── probe_store ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS media_probe (
    canonical_path  TEXT PRIMARY KEY,
    mtime           REAL NOT NULL,
    size            INTEGER NOT NULL,
    duration_s      REAL,
    audio_json      TEXT NOT NULL,
    sub_json        TEXT NOT NULL,
    probed_at       REAL NOT NULL
);

-- ─── pending_store ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pending_walks (
    id                TEXT PRIMARY KEY,
    created_at        REAL NOT NULL,
    status            TEXT NOT NULL,
    considered        INTEGER NOT NULL DEFAULT 0,
    decisions_total   INTEGER NOT NULL DEFAULT 0,
    approved_count    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pending_decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    walk_id      TEXT NOT NULL,
    item_json    TEXT NOT NULL,
    approved     INTEGER,
    scan_id      TEXT,
    FOREIGN KEY (walk_id) REFERENCES pending_walks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pending_decisions_walk
    ON pending_decisions (walk_id);
CREATE INDEX IF NOT EXISTS idx_pending_walks_status
    ON pending_walks (status);
