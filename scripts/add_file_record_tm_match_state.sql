ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS tm_match_signature VARCHAR(64);

ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS tm_last_matched_at TIMESTAMP;
