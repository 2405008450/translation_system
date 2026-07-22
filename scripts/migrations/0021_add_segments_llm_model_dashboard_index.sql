-- 数据看板按时间和模型聚合当前 MT 句段。
-- 使用部分索引避免扩大普通句段的写入与存储开销。

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_segments_llm_model_updated_at
    ON segments (updated_at, llm_model)
    WHERE source = 'llm';
