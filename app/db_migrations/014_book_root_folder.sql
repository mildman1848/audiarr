-- Schema version 14: per-book root folder preference, set at add time (#50).
-- Nullable FK; NULL means "no preference", same as every book before this
-- column existed -- organize/import keeps falling back to the first
-- configured root folder in that case (see _resolve_organize_root_and_pattern).
ALTER TABLE books ADD COLUMN root_folder_id INTEGER REFERENCES root_folders(id);
