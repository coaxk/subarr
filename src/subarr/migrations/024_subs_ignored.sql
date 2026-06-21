-- 024_subs_ignored.sql
--
-- #316: per-title "ignore / I don't want subs here". A deliberate user exclude
-- of a real gap (distinct from #311, which is detection wrongly flagging files
-- that already have subs). When a title is ignored, build_coverage drops its
-- rows so it stops appearing in gaps / Review and stops feeding the auto-queue.
--
-- `path` is EITHER a movie's file_canonical_path (exact match) OR a TV series
-- prefix ending in '/' (prefix match), mirroring the series-intent key. Keeping
-- both in one table lets build_coverage apply a single suppression pass.
-- Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS subs_ignored (
    path        TEXT PRIMARY KEY,
    ignored_at  REAL NOT NULL,
    note        TEXT
);
