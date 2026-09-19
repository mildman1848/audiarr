-- Schema version 10: periodic wanted-search scheduler state (#26).
-- Tracks the outcome of the most recent scheduler search attempt per
-- monitored, cutoff-unmet book, keyed by book_id, so a later tick never
-- re-grabs a book whose upgrade is already in flight (the old file stays
-- below cutoff until the grabbed release is imported). Rows for books
-- that no longer need an upgrade are pruned by the scheduler itself.

CREATE TABLE IF NOT EXISTS wanted_search_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'no_release'
      CHECK (status IN ('grabbed', 'no_release')),
    nzo_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
