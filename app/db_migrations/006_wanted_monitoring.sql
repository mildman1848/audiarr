-- Schema version 6: Wanted/Missing monitoring state.
-- Adds a "monitored" flag to books so a Wanted/Missing view can tell
-- entities the user actively tracks apart from ones they don't care to
-- see chased down. Existing imported books default to monitored=1
-- (matches Sonarr/Radarr/Lidarr/Readarr: anything already in the library
-- is assumed wanted unless explicitly unmonitored).

ALTER TABLE books ADD COLUMN monitored INTEGER NOT NULL DEFAULT 1;
