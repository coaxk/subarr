-- 002_update_checks.sql
--
-- Caches the result of GitHub release polls so the UI can show
-- "update available" without hitting the GitHub API on every page
-- load.
--
-- One row per tracked product (subarr, subarr-subgen). The
-- background checker writes; the /api/updates router reads.

CREATE TABLE IF NOT EXISTS update_checks (
    product             TEXT PRIMARY KEY,        -- 'subarr' | 'subarr-subgen'
    repo                TEXT NOT NULL,           -- 'coaxk/subarr' etc.
    current_version     TEXT,                    -- what's running locally
    latest_version      TEXT,                    -- latest release tag from GitHub
    latest_released_at  REAL,                    -- when GitHub says it was published
    release_notes_url   TEXT,                    -- direct link to the release page
    is_breaking         INTEGER NOT NULL DEFAULT 0,
                                                 -- 1 iff release body mentions 'breaking'
    checked_at          REAL NOT NULL,           -- when we last successfully polled
    last_error          TEXT                     -- non-null when last poll failed
);
