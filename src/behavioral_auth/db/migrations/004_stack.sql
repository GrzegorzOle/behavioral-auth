-- The hardware stack a window of behaviour came from.
--
-- raw_events already carried dev_path and dev_name, but the path renumbers
-- between boots and re-plugs, so neither identifies a device across a dock
-- cycle. dev_id holds evdev's vendor:product instead.
--
-- stack_fp records, per feature window, which (keyboard, mouse) pair actually
-- produced its events. Scoring compares a live window only against stacks the
-- pattern was trained on: a pattern learned across a mixture of stacks has a
-- wider spread and therefore a higher threshold, which makes it more permissive
-- than one learned on either alone.
--
-- Both are nullable: rows written before this migration have no device identity
-- to recover, and a window that predates it is treated as an unknown stack.

ALTER TABLE raw_events ADD COLUMN IF NOT EXISTS dev_id VARCHAR;

ALTER TABLE feature_windows ADD COLUMN IF NOT EXISTS stack_fp VARCHAR;

-- Carried onto the sequence as well, so scoring can gate without rejoining to
-- the windows on every tick. Every window in a sequence shares one stack — a
-- sequence spanning a change is never built.
ALTER TABLE fused_sequences ADD COLUMN IF NOT EXISTS stack_fp VARCHAR;
