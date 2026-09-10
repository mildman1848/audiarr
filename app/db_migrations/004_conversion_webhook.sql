-- Schema version 4: webhook-driven conversion completion.
-- conversion_jobs gains the converted file location reported by the
-- m4b-convertarr POST_CONVERT hook, plus counts of originals deleted
-- after successful import (audit trail for delete_originals).

ALTER TABLE conversion_jobs ADD COLUMN completed_path TEXT NOT NULL DEFAULT '';
ALTER TABLE conversion_jobs ADD COLUMN originals_deleted INTEGER NOT NULL DEFAULT 0;
