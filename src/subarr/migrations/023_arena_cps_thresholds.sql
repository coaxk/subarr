-- 023_arena_cps_thresholds.sql
--
-- #314: the Tuning Lab now exposes the CPS (reading-speed) bar that the
-- readability judge scores recipes against, set per-sweep. Persist the chosen
-- comfortable/critical values on the run so historical results stay
-- interpretable (you can see WHICH bar a winner was picked under). Existing
-- rows backfill to the Netflix/BBC norms the judge used before this change.
ALTER TABLE arena_runs ADD COLUMN cps_max REAL NOT NULL DEFAULT 20.0;
ALTER TABLE arena_runs ADD COLUMN cps_critical REAL NOT NULL DEFAULT 25.0;
