-- 句段历史记录表
-- 记录每次句段译文的修改历史，用于追溯和对比

CREATE TABLE IF NOT EXISTS segment_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- 关联字段
    segment_id UUID NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    sentence_id VARCHAR(20) NOT NULL,
    
    -- 快照字段
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    
    -- 操作信息
    status VARCHAR(20) NOT NULL,           -- 流程状态：翻译、审校等
    source VARCHAR(20) NOT NULL,           -- 来源：tm, manual, llm
    confirm_type VARCHAR(30),              -- 确认类型：人工输入、TM填充、AI修正
    
    -- 操作人和时间
    operator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT NOW() NOT NULL
);

-- 索引
CREATE INDEX IF NOT EXISTS ix_segment_history_segment_id ON segment_history(segment_id);
CREATE INDEX IF NOT EXISTS ix_segment_history_file_record_id ON segment_history(file_record_id);
CREATE INDEX IF NOT EXISTS ix_segment_history_sentence_id ON segment_history(sentence_id);
CREATE INDEX IF NOT EXISTS ix_segment_history_created_at ON segment_history(created_at DESC);
