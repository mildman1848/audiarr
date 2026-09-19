-- Schema version 11: audiobook-pragmatic tags (#27).
-- Plain labels with an optional color, attachable to books and root
-- folders. No Radarr-shaped per-tag notification routing or delay
-- profiles here -- tags are just a shared vocabulary for organizing
-- and filtering the library.

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS book_tags (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, tag_id)
);

CREATE TABLE IF NOT EXISTS root_folder_tags (
    root_folder_id INTEGER NOT NULL REFERENCES root_folders(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (root_folder_id, tag_id)
);
