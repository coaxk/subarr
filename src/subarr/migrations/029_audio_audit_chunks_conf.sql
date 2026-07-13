-- 029_audio_audit_chunks_conf.sql
--
-- #407 Part A: persist the raw per-chunk (language, probability) list from robust
-- detection so the multilingual chunk-confidence threshold T can be tuned from
-- real accrued data. Additive + nullable; existing rows predate capture and read
-- back as NULL (no backfill). Payload is tiny (~3 [lang, prob] pairs per file).
ALTER TABLE audio_lang_audit ADD COLUMN chunks_conf TEXT;  -- JSON [[lang, prob], ...]
