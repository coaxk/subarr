-- 020_aftercare_preview.sql
--
-- #216: the existing-subtitle audit surfaces external (provider/scene) subs in
-- the review UI. To explain WHY a sub is flagged, we store a short, already-
-- SANITIZED snippet of a representative cue (e.g. the scene-release ad line).
-- Captured + sanitized at audit time (subtitle_sanitize) so the column never
-- holds raw untrusted markup; NULL for completion-watcher rows (our own clean
-- Whisper output needs no preview).
ALTER TABLE aftercare_results ADD COLUMN preview TEXT;
