-- Schema version 7: ASIN column for calendar/backfill metadata.
-- Prod books were imported before the metadata pipeline existed, so
-- release_date is NULL for all of them and the Calendar view has nothing
-- to show. Adds a nullable "asin" column (indexed, for the future
-- provider-id lookups the backfill task and calendar rely on) so a
-- best-effort startup/manual backfill can fill in asin + release_date via
-- provider lookup. DDL only -- no network calls happen in migrations.

ALTER TABLE books ADD COLUMN asin TEXT;
CREATE INDEX IF NOT EXISTS idx_books_asin ON books (asin);
