ALTER TABLE IF EXISTS segments
    ALTER COLUMN sentence_id TYPE VARCHAR(100);

ALTER TABLE IF EXISTS segment_revisions
    ALTER COLUMN sentence_id TYPE VARCHAR(100);

ALTER TABLE IF EXISTS segment_qa_issues
    ALTER COLUMN sentence_id TYPE VARCHAR(100);

ALTER TABLE IF EXISTS term_qa_report_items
    ALTER COLUMN sentence_id TYPE VARCHAR(100);

ALTER TABLE IF EXISTS number_check_report_items
    ALTER COLUMN sentence_id TYPE VARCHAR(100);

ALTER TABLE IF EXISTS auto_tm_outbox
    ALTER COLUMN sentence_id TYPE VARCHAR(100);
