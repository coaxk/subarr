-- 031_onboarding_ui_seen.sql
--
-- subarr #480: 87% of installs that never finish onboarding sit at step 0,
-- which cannot distinguish "opened the wizard and bounced" from "never opened
-- the UI at all". The wizard now records the FIRST time it rendered.
-- Survives a re-run/reset on purpose: the question is "was it ever seen".
ALTER TABLE onboarding_state ADD COLUMN ui_seen_at REAL;
