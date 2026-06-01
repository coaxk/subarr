-- #11: timestamp the last_error so the Settings -> Telemetry panel can
-- show "last error 9m ago" instead of just "last failed send". Without
-- it, an old error sits in the panel forever even after recovery,
-- giving the impression telemetry is broken when it actually healed
-- on the next tick.
--
-- Nullable: existing rows have a NULL last_error_at and we treat that
-- as "no known timestamp" in the UI.
ALTER TABLE telemetry_state ADD COLUMN last_error_at REAL;
