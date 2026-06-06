-- #131 tuning-lab persistence — and the substrate for the federated tournament
-- (#124). Every sweep and its ranked result is kept so history survives a
-- restart, and (later) feeds crowd-curated, per-language config presets:
-- "which recipe won, for which language/content, with what scores".
--
-- The run is stored as a row plus JSON blobs for variants/outcomes/result, so
-- the schema doesn't have to chase the tournament scorecard shape. `winner` and
-- `source_language` are denormalized into indexed columns because those are the
-- axes the per-language aggregation will group by.
CREATE TABLE IF NOT EXISTS arena_runs (
    id              TEXT PRIMARY KEY,
    media_path      TEXT NOT NULL,
    source_language TEXT,
    status          TEXT NOT NULL,                 -- pending | running | done | error
    source_text     TEXT,
    variants        TEXT NOT NULL,                 -- JSON: [{label, kwargs}]
    outcomes        TEXT NOT NULL DEFAULT '[]',    -- JSON: [{label, ok, error}]
    result          TEXT,                          -- JSON: serialized TournamentResult
    winner          TEXT,                          -- denormalized result.winner_label
    error           TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_arena_runs_created ON arena_runs (created_at);
CREATE INDEX IF NOT EXISTS idx_arena_runs_status ON arena_runs (status);
CREATE INDEX IF NOT EXISTS idx_arena_runs_lang ON arena_runs (source_language);
