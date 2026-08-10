-- Store a keyboard zone and a pairing id instead of the key that was pressed.
--
-- raw_events used to carry the evdev key code of every keystroke, which is a
-- keylog. The feature pipeline never needed one: it uses the code only to pair
-- a press with its release (equality, not identity) and to spot backspace. See
-- collector/zones.py for the reasoning and for what a zone stream still leaks.
--
-- Both columns are nullable and nothing is rewritten. Rows captured before this
-- migration keep their real ev_code, and features/keystroke.py falls back to
-- reading them that way -- so upgrading neither invalidates a pattern in
-- progress nor breaks `rebuild-features` over older data. The pseudonymised
-- shape is recognised by kb_zone being present, not by guessing from ev_code.
--
-- Existing key codes are NOT deleted here. Purging history is a decision with
-- consequences (it is the material rebuild-features exists to recompute from),
-- so it belongs to an explicit command, not to a silent migration.

ALTER TABLE raw_events ADD COLUMN IF NOT EXISTS kb_zone USMALLINT;
ALTER TABLE raw_events ADD COLUMN IF NOT EXISTS kb_pair USMALLINT;
