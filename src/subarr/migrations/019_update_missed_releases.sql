-- 019 (#203): "what you're missing" update nudge. Stores the releases
-- between an install's current version and the latest, as JSON
-- [{tag, title, notes_url, released_at}] parsed from the releases.atom
-- feed (titles only — no HTML bodies, no sanitization surface).
ALTER TABLE update_checks ADD COLUMN missed_json TEXT;
