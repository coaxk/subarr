-- 016_track_mismatch_dismissed.sql
--
-- #159 default-audio-track mismatch: per-FILE user dismiss. When subarr flags a
-- file whose default audio track isn't the show's original language (a dub set
-- as default, causing double-translated subs) but the user wants to keep that
-- default as-is, they dismiss it and the prompt stays quiet across future
-- coverage walks.
--
-- Keyed by the FILE path (CoverageItem.file_canonical_path, relative to
-- media_root) — mismatch is a file-level attribute, not series-level (unlike
-- the #140 mixed dismiss). Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS track_mismatch_dismissed (
    file_path     TEXT PRIMARY KEY,
    dismissed_at  REAL NOT NULL,
    note          TEXT
);
