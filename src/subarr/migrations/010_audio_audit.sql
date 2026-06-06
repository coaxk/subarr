-- #155 phase 2 — library-wide audio-language audit walker.
--
-- Phase 1 (the /api/arena/audio-issues endpoint) harvests mislabel/bilingual
-- findings from sweeps that ALREADY ran (zero extra GPU). Phase 2 is the
-- proactive side: a throttled, opt-in background walker that runs robust
-- language detection over files that were NEVER swept, so the audit covers the
-- whole library — not just what the tuning lab happened to touch.
--
-- One row per file (keyed by canonical_path). `mtime` makes the walk RESUMABLE:
-- a file whose mtime matches the stored row is skipped on a re-run. `status` is
-- the resolved verdict from resolve_source_language (the SAME classifier the
-- sweeps use), so the audit and the sweep flags stay coherent. Indexed on
-- status (the UI filters to the actionable buckets) and checked_at (newest
-- first / staleness).
CREATE TABLE IF NOT EXISTS audio_lang_audit (
    canonical_path  TEXT PRIMARY KEY,
    tag_lang        TEXT,                          -- the file's known audio tag (ISO-639-1)
    detected_lang   TEXT,                          -- what Whisper robust-detect heard (plurality)
    status          TEXT NOT NULL,                 -- agrees | mislabel | bilingual | multitrack | confused | undetermined
    languages_heard TEXT NOT NULL DEFAULT '[]',    -- JSON: [iso...] heard across chunks
    n_agreeing      INT,                           -- chunks agreeing on the plurality language
    n_total         INT,                           -- chunks voted
    mtime           REAL,                          -- source file mtime at audit time (resumability)
    checked_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audio_lang_audit_status ON audio_lang_audit (status);
CREATE INDEX IF NOT EXISTS idx_audio_lang_audit_checked ON audio_lang_audit (checked_at);
