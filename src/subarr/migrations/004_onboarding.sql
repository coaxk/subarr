-- 004_onboarding.sql
--
-- Onboarding wizard state. Single row, survives container restarts so
-- a partially-completed wizard resumes exactly where the user left off
-- instead of starting over.
--
-- progress_json accumulates form values as the user advances:
--   {
--     "media_root": "/media/library",
--     "arr_path_prefix": "/data/Media/",
--     "bazarr_url": "http://bazarr:6767",
--     "bazarr_api_key": "...",
--     ...
--   }
-- (API keys live here UNTIL the user finalizes; finalization writes them
--  to the in-memory settings + clears them from progress_json.)
--
-- step values:
--   0  = welcome
--   1  = media layout
--   2  = bazarr
--   3  = sonarr
--   4  = radarr
--   5  = tautulli
--   6  = subgen
--   7  = ollama
--   8  = gpu (informational only)
--   9  = first walk
--   10 = done

CREATE TABLE IF NOT EXISTS onboarding_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    step            INTEGER NOT NULL DEFAULT 0,
    completed_at    REAL,
    progress_json   TEXT NOT NULL DEFAULT '{}',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
