-- Schema version 5: import ignore list.
-- Folders explicitly marked as "not a book" (or otherwise not worth
-- retrying) are recorded here and excluded from future import runs and
-- from the unmatched-folders list.

CREATE TABLE IF NOT EXISTS import_ignores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
