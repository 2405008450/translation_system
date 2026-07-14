-- 句段位置必须使用独立的顺序字段，不能依赖 UUID、哈希或 sentence_id 的字典序。
-- 历史数据保留 -1，由 DOCX 导出器按源文件文本兼容对齐；新导入数据会写入真实顺序。
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS sequence_index INTEGER NOT NULL DEFAULT -1;

CREATE INDEX IF NOT EXISTS ix_segments_file_record_sequence_order
    ON segments (file_record_id, block_index, row_index, cell_index, sequence_index, sentence_id);

COMMENT ON COLUMN segments.sequence_index IS
    '句段在源文件中的零基顺序；-1 表示待按源文件兼容对齐的历史数据';
