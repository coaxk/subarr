-- 015_pending_queue_provenance.sql
--
-- #66/#116 slice 6: carry provenance identity through the pending queue.
--
-- When producers route through the pending queue, the FEEDER (not the producer)
-- creates the scan + writes the provenance ledger row. For completion_watcher
-- to fire Bazarr's scan-disk task the instant subgen finishes, that row needs
-- series_id + sonarr_episode_id — which the producer resolved at enqueue time.
-- So we stash them on the pending row and the feeder passes them to
-- provenance.record(). Without this, routed jobs would still transcribe but
-- Bazarr would only pick up the .srt on its slower scheduled scan.
--
-- Nullable (movies / ad-hoc paths have no episode id). Idempotent via the
-- migrate runner's duplicate-column tolerance.

ALTER TABLE pending_queue ADD COLUMN series_id INTEGER;
ALTER TABLE pending_queue ADD COLUMN sonarr_episode_id INTEGER;
