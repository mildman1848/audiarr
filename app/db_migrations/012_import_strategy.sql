-- Schema version 12: hardlink/copy/move import strategies per root folder (#30).
-- Default 'copy' is the conservative choice: it never risks losing the
-- original file (unlike 'move') and works across filesystems/volumes
-- (unlike 'hardlink', which requires source and destination to share one
-- filesystem). Existing rows pick up this safe default automatically.
--
-- SQLite's ALTER TABLE ADD COLUMN supports a CHECK constraint as long as it
-- only inspects the new column and the DEFAULT satisfies it (true here), so
-- the allowed-values check lives in the schema, not just in the app/API
-- layer (which still validates too, for a clean 422 instead of a raw
-- IntegrityError -- see ImportStrategyLiteral in app/api/routes_library.py).
ALTER TABLE root_folders ADD COLUMN import_strategy TEXT NOT NULL DEFAULT 'copy'
    CHECK (import_strategy IN ('move', 'copy', 'hardlink'));
