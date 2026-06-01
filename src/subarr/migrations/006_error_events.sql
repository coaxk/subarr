-- Real telemetry (error_counts_30d): persist anonymous error-class counts
-- so the public stats dashboard reflects actual failure classes instead of
-- an empty {}. Stores ONLY type(e).__name__ (e.g. 'SubgenUnavailable',
-- 'ConnectError') plus a timestamp -- never the exception message -- so no
-- paths / titles / API keys can leak into telemetry.
CREATE TABLE IF NOT EXISTS error_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exc_class   TEXT NOT NULL,
    occurred_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_error_events_occurred ON error_events (occurred_at);
