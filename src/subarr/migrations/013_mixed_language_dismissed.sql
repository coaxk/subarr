-- 013_mixed_language_dismissed.sql
--
-- #140 mis-grouped-series detection: per-series user dismiss. When subarr
-- flags a series as "mixed languages — likely mis-grouped" but the user knows
-- it's a genuinely multilingual show (a legit anthology, etc.), they dismiss
-- it and the warning stays quiet across future coverage walks.
--
-- Keyed by the series directory (CoverageItem.canonical_path, relative to
-- media_root) — the same stable, user-visible identifier used elsewhere.
-- Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS mixed_language_dismissed (
    series_path   TEXT PRIMARY KEY,
    dismissed_at  REAL NOT NULL,
    note          TEXT
);
