-- Schema version 17: completed-download import state for torrent clients (#81).
-- Keyed by (client, item_id) -- e.g. ('qbittorrent', '<info-hash>') -- so a
-- torrent hash can never collide with a SABnzbd nzo_id in sab_import_state.
-- Purely additive (new table only); the restore point is the pre-upgrade
-- backup of audiarr.db (see app/backup_service.py).

CREATE TABLE IF NOT EXISTS download_client_import_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client TEXT NOT NULL,
    item_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    folder_path TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'skipped'
      CHECK (status IN ('imported', 'failed', 'skipped')),
    reason TEXT NOT NULL DEFAULT '',
    book_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (client, item_id)
);
