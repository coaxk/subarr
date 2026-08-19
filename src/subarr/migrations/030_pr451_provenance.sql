-- 030_pr451_provenance.sql
--
-- #451: explicit nullable provenance for the subtitle-text language sanity
-- checker. New transcribe/translate jobs carry explicit task/language/origin
-- claims; the completion webhook records its own evidence; the checker compares
-- the two. Legacy rows keep NULL = unknown — NO default reinterpretation, and
-- no language claim ever comes from a filename.
--
-- Every column here is nullable with no DEFAULT, so pre-existing rows (and any
-- path that doesn't know a claim) remain NULL forever. This mirrors the
-- 015/025/026 pattern (nullable additive columns, legacy rows stay NULL).
--
--   pending_queue  — submission-side claims travel with the job so the feeder
--                    can persist them on the ledger row at drain time
--                    (task already exists since 014).
--   subs_generated — full submission + webhook evidence, plus the comparison
--                    outcome. provenance_conflict is a nullable enum:
--                      NULL = no conflict / evidence not compared
--                      0    = compared and no conflict
--                      1    = one or more conflicting non-NULL claims (sticky,
--                             never cleared)
--
-- Unique open-row backstop (the "separate multi-process nit" from #287):
-- record() already dedups open rows under its process lock; this partial
-- UNIQUE index makes it a DB-level invariant so two processes can't both hold
-- an open row for one canonical_path. Concurrent webhook delivery then always
-- has exactly one row to complete. NOTE: if a pre-030 DB already contains
-- duplicate open rows (the exact corruption this backstop exists to prevent),
-- this CREATE UNIQUE INDEX fails loudly rather than silently deduplicating —
-- dropping rows is outside a migration's data-integrity remit.

-- ── pending_queue: submission claims ride on the job ───────────────────
ALTER TABLE pending_queue ADD COLUMN source_language TEXT;
ALTER TABLE pending_queue ADD COLUMN target_language TEXT;
ALTER TABLE pending_queue ADD COLUMN submission_origin TEXT;

-- ── subs_generated: full provenance evidence + conflict outcome ────────
ALTER TABLE subs_generated ADD COLUMN task TEXT;
ALTER TABLE subs_generated ADD COLUMN source_language TEXT;
ALTER TABLE subs_generated ADD COLUMN target_language TEXT;
ALTER TABLE subs_generated ADD COLUMN submission_origin TEXT;
ALTER TABLE subs_generated ADD COLUMN webhook_event TEXT;
ALTER TABLE subs_generated ADD COLUMN webhook_language TEXT;
ALTER TABLE subs_generated ADD COLUMN webhook_subtitle TEXT;
ALTER TABLE subs_generated ADD COLUMN provenance_conflict INTEGER;

-- One open (in-flight) row per canonical_path, lowest id wins.
CREATE UNIQUE INDEX IF NOT EXISTS uq_subs_generated_open
    ON subs_generated (canonical_path)
    WHERE completed_at IS NULL;