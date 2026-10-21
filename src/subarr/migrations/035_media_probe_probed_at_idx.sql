-- 035_media_probe_probed_at_idx.sql
--
-- #621: index media_probe by probed_at.
--
-- The Dashboard's "discovered" tile reads the most recently probed file on
-- every refresh. Without an index that is a scan of the whole table plus a
-- sort (~30 ms at 40k rows), run under the probe store's lock, which every
-- probe upsert also takes. With it, the newest row is a single index lookup.

CREATE INDEX IF NOT EXISTS idx_media_probe_probed_at ON media_probe(probed_at);
