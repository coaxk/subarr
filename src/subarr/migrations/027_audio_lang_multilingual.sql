-- 027_audio_lang_multilingual.sql
--
-- #357 multilingual + zxx audio: represent files that legitimately break the
-- one-language-per-file assumption. Two additive columns on the existing
-- audio_lang_verifications table:
--
--   lang_class  'single' (default) | 'multi'. A confident-multilingual detection
--               (>=2 high-confidence chunk languages, e.g. The Beasts gl+es+fr)
--               is stored as 'multi'.
--   lang_codes  JSON array, populated ONLY when lang_class='multi' -- the ordered
--               high-conf set, e.g. ["gl","es","fr"]. NULL for single files.
--
-- The singular lang_code column is ALWAYS populated (multi = first-of-set), so
-- every existing single-language consumer keeps working unchanged. No PK change,
-- no backfill: existing rows default to 'single' / NULL, which is correct.
ALTER TABLE audio_lang_verifications ADD COLUMN lang_class TEXT NOT NULL DEFAULT 'single';
ALTER TABLE audio_lang_verifications ADD COLUMN lang_codes TEXT;
