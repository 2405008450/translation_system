-- 为大文档任务页的句段分页排序增加组合索引。
-- 运行方式: psql -U <user> -d <database> -f scripts/add_segments_pagination_index.sql

CREATE INDEX IF NOT EXISTS ix_segments_file_record_order
ON segments (file_record_id, block_index, row_index, cell_index, sentence_id);

ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS sequence_index INTEGER NOT NULL DEFAULT -1;

CREATE INDEX IF NOT EXISTS ix_segments_file_record_sequence_order
ON segments (file_record_id, block_index, row_index, cell_index, sequence_index, sentence_id);
