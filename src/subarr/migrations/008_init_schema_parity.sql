-- 008_init_schema_parity.sql
--
-- Bring the migration set to FULL parity with the per-store init_schema()
-- methods, so migrations become the single source of truth for the schema
-- and init_schema() can be removed (see follow-up PR).
--
-- Everything here is idempotent on a transitional DB (one a prior
-- init_schema already built):
--   - CREATE TABLE / INDEX IF NOT EXISTS  → no-op if present.
--   - ALTER TABLE ADD COLUMN              → raises "duplicate column name"
--                                           when present; the migration
--                                           runner tolerates exactly that
--                                           error per-statement (see
--                                           migrate.py _apply_one).
--   - INSERT OR IGNORE                    → no-op if the row exists.

-- ─── audio_lang_store: per-file verifications + series-level intent ──
CREATE TABLE IF NOT EXISTS audio_lang_verifications (
    canonical_path  TEXT PRIMARY KEY,
    lang_code       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'user',
    confidence      REAL NOT NULL DEFAULT 1.0,
    verified_at     REAL NOT NULL,
    verified_by     TEXT,
    evidence        TEXT
);
CREATE INDEX IF NOT EXISTS idx_audio_lang_verifications_lang
    ON audio_lang_verifications(lang_code);

CREATE TABLE IF NOT EXISTS series_lang_intent (
    series_prefix   TEXT PRIMARY KEY,
    lang_code       TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'user',
    confidence      REAL NOT NULL DEFAULT 1.0,
    declared_at     REAL NOT NULL,
    declared_by     TEXT,
    note            TEXT
);

-- ─── coverage_cache: single-row coverage snapshot ───────────────────
CREATE TABLE IF NOT EXISTS coverage_snapshot (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    generated_at    REAL NOT NULL,
    items_json      TEXT NOT NULL,
    totals_json     TEXT NOT NULL,
    sources_json    TEXT NOT NULL,
    build_duration_s REAL,
    item_count      INTEGER
);

-- ─── enrichment #156: structured-output columns ─────────────────────
-- Nullable so legacy free-text rows stay valid until eviction.
ALTER TABLE lang_enrichment ADD COLUMN confidence REAL;
ALTER TABLE lang_enrichment ADD COLUMN reasoning TEXT;

-- ─── probe_store: probe source column (v1.1-A) ──────────────────────
ALTER TABLE media_probe ADD COLUMN source TEXT NOT NULL DEFAULT 'ffprobe';

-- ─── seed: default coverage_walk schedule (disabled, user opts in) ──
-- auto_queue_rules needs no seed — ScheduleStore.get_rules() returns a
-- default AutoQueueRules() when the row is absent.
INSERT OR IGNORE INTO schedule_config
    (name, enabled, kind, interval_minutes, daily_hhmm, day_of_week, probe_roots)
    VALUES ('coverage_walk', 0, 'daily', 360, '03:00', 0, '');
