-- 003_telemetry.sql
--
-- Anonymous telemetry state. One row, ever. install_id is generated
-- at first boot from os.urandom — never derived from anything
-- personal. opted_in defaults to 1 (telemetry ON by default per the
-- v1.0 product decision; opt-out is one click in Settings).
--
-- last_payload_json is the verbatim JSON we POSTed on the most recent
-- successful send, surfaced in Settings so the user can see exactly
-- what we sent.

CREATE TABLE IF NOT EXISTS telemetry_state (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    install_id        TEXT NOT NULL,
    opted_in          INTEGER NOT NULL DEFAULT 1,
    last_ping_at      REAL,
    last_payload_json TEXT,
    last_error        TEXT,
    created_at        REAL NOT NULL
);
