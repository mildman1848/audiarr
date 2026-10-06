-- Schema version 16: author/series follows (#80).
-- A follow is a user-chosen author or series name whose provider results are
-- reviewed as candidates. Follows are future-only by default: only candidates
-- released after "today" become monitored books automatically; back-catalog
-- candidates stay 'backlog' until the user explicitly adds them. "Owned" is
-- derived at read time from provider_ids / books.asin and is not stored.

CREATE TABLE IF NOT EXISTS follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('author', 'series')),
    name TEXT NOT NULL COLLATE NOCASE,
    provider TEXT,
    provider_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_refreshed_at TEXT,
    last_result TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, name)
);

CREATE TABLE IF NOT EXISTS follow_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    follow_id INTEGER NOT NULL REFERENCES follows(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    provider_book_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    series TEXT NOT NULL DEFAULT '',
    series_position INTEGER,
    release_date TEXT NOT NULL DEFAULT '',
    cover_url TEXT,
    status TEXT NOT NULL DEFAULT 'backlog'
        CHECK (status IN ('future', 'backlog', 'excluded', 'added')),
    book_id INTEGER REFERENCES books(id) ON DELETE SET NULL,
    UNIQUE (follow_id, provider, provider_book_id)
);

CREATE INDEX IF NOT EXISTS idx_follow_candidates_follow
    ON follow_candidates (follow_id, status);
