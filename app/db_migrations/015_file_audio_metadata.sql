-- Schema version 15: ffprobe-based read-only audio metadata enrichment (#71).
-- Duration/bitrate/codec/container/chapter_count are nullable -- NULL means
-- "not probed yet" (files imported before this migration), distinct from
-- probe_status = 'error'/'unavailable' which means a probe was attempted and
-- did not produce data. This is read-only enrichment recorded alongside the
-- existing library_files row; original audio files are never modified, and
-- ffprobe/ffmpeg dependency stays confined to this optional probing step --
-- conversion remains external via the existing m4b-convertarr/command
-- backend (see app/conversion).
ALTER TABLE library_files ADD COLUMN duration_seconds INTEGER;
ALTER TABLE library_files ADD COLUMN bitrate_kbps INTEGER;
ALTER TABLE library_files ADD COLUMN codec TEXT;
ALTER TABLE library_files ADD COLUMN container TEXT;
ALTER TABLE library_files ADD COLUMN chapter_count INTEGER;
ALTER TABLE library_files ADD COLUMN probe_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (probe_status IN ('pending', 'ok', 'error', 'unavailable'));
ALTER TABLE library_files ADD COLUMN probe_error TEXT;

-- One row per chapter of a probed file; ordered by idx (ffprobe's own
-- chapter order). Deleted automatically when the parent file is removed.
CREATE TABLE library_file_chapters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  library_file_id INTEGER NOT NULL REFERENCES library_files(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  start_seconds REAL NOT NULL,
  end_seconds REAL,
  UNIQUE (library_file_id, idx)
);
