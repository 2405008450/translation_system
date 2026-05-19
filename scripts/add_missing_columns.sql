-- =============================================================================
-- 补丁：同步 models.py 与数据库之间缺失的字段
-- 运行方式: psql -U <user> -d <database> -f scripts/add_missing_columns.sql
-- 该脚本是幂等的，可在已有库上重复执行。
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- projects 表：缺少 document_parse_mode
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS projects
    ADD COLUMN IF NOT EXISTS document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full';

-- -----------------------------------------------------------------------------
-- segments 表：缺少 matched_collection_name / matched_creator_name /
--              matched_created_at / matched_updated_at / LLM 模型信息
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS matched_collection_name VARCHAR(120);
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS matched_creator_name VARCHAR(100);
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS matched_created_at TIMESTAMP;
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS matched_updated_at TIMESTAMP;
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(40);
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS llm_model VARCHAR(200);

-- -----------------------------------------------------------------------------
-- memory_entries 表：缺少 creator_id
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_memory_entries_creator_id
    ON memory_entries (creator_id);

-- -----------------------------------------------------------------------------
-- term_entries 表：缺少 creator_id（models.py 中有此字段）
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_term_entries_creator_id
    ON term_entries (creator_id);

COMMIT;
