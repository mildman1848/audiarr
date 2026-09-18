-- Schema version 8: per-book quality profile override (#20).
-- Quality profiles remain settings data (settings.json), not DB rows, so
-- this stores the profile NAME rather than a foreign key. Empty string
-- means "inherit the default (first configured) quality profile".

ALTER TABLE books ADD COLUMN quality_profile TEXT NOT NULL DEFAULT '';
