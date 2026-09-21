-- 034_verdict_arr_ids.sql
--
-- #571: carry a verdict by Sonarr/Radarr id.
--
-- #563 carries a verdict onto a replaced file by matching the `SxxExx` token in
-- the filename. A date-named episode (`Daily Show - 2026-09-08.mkv`, #561) has
-- no token and a movie has none at all, so those verdicts died with the old
-- file. Recording the id the verdict was made against lets the carry-over match
-- on identity instead of on the name.
--
-- Both nullable: a verdict recorded before this migration, or one for a file no
-- arr knows about, simply has no id and falls back to the token match. The
-- back-fill fills them in for files still on disk.
--
-- Indexed because the sweep looks verdicts up by id on every pass.

ALTER TABLE audio_lang_verifications ADD COLUMN sonarr_episode_id INTEGER;
ALTER TABLE audio_lang_verifications ADD COLUMN radarr_movie_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_audio_lang_sonarr_episode_id
    ON audio_lang_verifications (sonarr_episode_id)
    WHERE sonarr_episode_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_audio_lang_radarr_movie_id
    ON audio_lang_verifications (radarr_movie_id)
    WHERE radarr_movie_id IS NOT NULL;
