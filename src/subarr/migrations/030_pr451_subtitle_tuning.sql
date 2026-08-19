-- 030_pr451_subtitle_tuning.sql
--
-- Consolidated PR #451 migration: explicit nullable provenance columns for the
-- subtitle-text language sanity checker (section 1) PLUS the advisory
-- text-language-check result column on aftercare_results (section 2).
--
-- Merged pre-merge from the two-file form (030_pr451_provenance.sql +
-- 031_pr451_text_lang_check.sql) so installs apply ONE coherent change. Since
-- upstream/main has 29 migrations (001..029), 030 remains the correct next
-- version — there is no 031 anymore.
--
-- Load-bearing invariants (unchanged from the original 030 header):
--   * every new column is nullable with NO default — legacy rows stay NULL
--     forever (no default reinterpretation; mirrors the 015/026 pattern; 025
--     is the deliberate NOT NULL DEFAULT 0 exception);
--   * no language claim ever comes from a filename.
--
-- Section 1 (provenance) is copied VERBATIM from 030_pr451_provenance.sql:
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
--
-- Section 2 (text-lang-check) is copied VERBATIM from
-- 031_pr451_text_lang_check.sql:
--
--   aftercare_results — one bounded advisory text-LID checker result persisted
--                       on the aftercare result row. Advisory only; NULL until a
--                       check lands.

-- ── Section 1: pending_queue submission claims ride on the job ──────────
ALTER TABLE pending_queue ADD COLUMN source_language TEXT;
ALTER TABLE pending_queue ADD COLUMN target_language TEXT;
ALTER TABLE pending_queue ADD COLUMN submission_origin TEXT;

-- ── Section 1: subs_generated full provenance evidence + conflict outcome ─
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

-- ── Section 2: aftercare_results advisory text-language-check result ─────
ALTER TABLE aftercare_results ADD COLUMN text_lang_check_json TEXT;
