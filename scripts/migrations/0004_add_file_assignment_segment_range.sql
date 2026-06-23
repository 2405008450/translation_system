ALTER TABLE file_assignments
    ADD COLUMN IF NOT EXISTS segment_range_start INTEGER;

ALTER TABLE file_assignments
    ADD COLUMN IF NOT EXISTS segment_range_end INTEGER;
