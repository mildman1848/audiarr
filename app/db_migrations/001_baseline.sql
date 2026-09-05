-- Schema version 1 baseline: settings-free bootstrap table.
-- This is what schema v1 looked like before the library model landed
-- (see app/db.py history); kept as an explicit migration so v1 databases
-- upgrade cleanly and fresh databases record the full version history.

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER NOT NULL
);
