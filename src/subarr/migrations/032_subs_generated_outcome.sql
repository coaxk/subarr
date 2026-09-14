-- 032_subs_generated_outcome.sql
--
-- subarr #545: a file with no speech looped forever through Auto Feed. subgen's
-- VAD removed all the audio, the transcription failed, no subtitle was written,
-- and the completion watcher still marked the job complete when its path left
-- subgen's queue. Coverage kept seeing the gap and the next walk re-queued it.
--
-- The ledger now records what a completed job produced:
--   'written'   a subtitle sidecar was found for the file
--   'no_output' the job left subgen's queue and no sidecar exists
--   NULL        legacy rows, directory jobs, and completions not judged
ALTER TABLE subs_generated ADD COLUMN outcome TEXT;
CREATE INDEX IF NOT EXISTS idx_subs_generated_outcome
    ON subs_generated (outcome, completed_at);
