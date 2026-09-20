-- 033_series_rule_suggestion_dismissed.sql
--
-- #568 series-rule suggestions: per-series user dismiss. When several
-- per-episode audio-language verdicts in one show agree, Review offers a
-- series rule (#226). A user who does not want one for that show dismisses the
-- suggestion, and it stays quiet however many more agreeing verdicts arrive.
--
-- Separate from #140's `mixed_language_dismissed`: that one says "this show is
-- genuinely multilingual" (and also suppresses a suggestion), while this one
-- says only "do not offer me a rule for this show".
--
-- Keyed by the series prefix WITH its trailing slash, the same identifier
-- `series_lang_intent.series_prefix` uses, so the two compare directly.
-- Idempotent: CREATE TABLE IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS series_rule_suggestion_dismissed (
    series_prefix  TEXT PRIMARY KEY,
    dismissed_at   REAL NOT NULL,
    note           TEXT
);
