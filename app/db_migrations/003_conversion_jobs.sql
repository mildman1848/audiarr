-- Schema version 3: conversion job queue for MP3 -> M4B.
-- Tracks jobs enqueued to a conversion backend (m4b-convertarr HTTP
-- backend by default; a local ffmpeg command backend may come later).

CREATE TABLE conversion_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  source_path TEXT NOT NULL,            -- folder or file to convert
  output_path TEXT NOT NULL DEFAULT '', -- expected m4b location
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','running','completed','failed','cancelled')),
  backend TEXT NOT NULL DEFAULT 'm4b-convertarr',
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_conversion_jobs_book ON conversion_jobs (book_id);
CREATE INDEX idx_conversion_jobs_status ON conversion_jobs (status);
