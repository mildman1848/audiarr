-- Schema version 9: SABnzbd completed-download auto-import state (#25).
-- Tracks which SABnzbd history entries the auto-import poller has already
-- processed, keyed by nzo_id (or a stable fallback key when SABnzbd
-- reports none), so a later poll never re-imports the same download.

CREATE TABLE IF NOT EXISTS sab_import_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nzo_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    folder_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'skipped'
      CHECK (status IN ('imported', 'failed', 'skipped')),
    reason TEXT NOT NULL DEFAULT '',
    book_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
