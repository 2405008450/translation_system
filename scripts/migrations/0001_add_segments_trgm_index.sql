-- 为句段检索与筛选增加索引，缓解大文档下的慢查询。
-- 运行方式: psql -U <user> -d <database> -f scripts/add_segments_trgm_index.sql

-- 1) 文本检索：ilike('%kw%') 双向通配无法走 B-tree，改用 pg_trgm GIN 索引加速。
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS ix_segments_source_text_trgm
    ON segments USING gin (source_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_segments_display_text_trgm
    ON segments USING gin (display_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_segments_target_text_trgm
    ON segments USING gin (target_text gin_trgm_ops);

-- 2) 高频过滤/聚合列索引：scope / status_filters / match_filters 频繁按这些列过滤。
CREATE INDEX IF NOT EXISTS ix_segments_file_record_status
    ON segments (file_record_id, status);

CREATE INDEX IF NOT EXISTS ix_segments_file_record_source
    ON segments (file_record_id, source);

-- 3) 增量游标端点 /segments/changes 按 updated_at 过滤并排序。
CREATE INDEX IF NOT EXISTS ix_segments_updated_at
    ON segments (updated_at);

ANALYZE segments;
