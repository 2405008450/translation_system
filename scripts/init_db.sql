-- =============================================================================
-- AI Translation System - 数据库初始化脚本 (one-shot)
-- -----------------------------------------------------------------------------
-- 使用方式：
--   1. 以超级用户创建数据库和业务账号（如果还没有）：
--        CREATE USER tm_user WITH PASSWORD 'tm123456';
--        CREATE DATABASE tm_demo OWNER tm_user;
--        GRANT ALL PRIVILEGES ON DATABASE tm_demo TO tm_user;
--
--   2. 连接到 tm_demo 后，用 **超级用户** 执行本脚本（CREATE EXTENSION 需要）：
--        psql -U postgres -d tm_demo -f scripts/init_db.sql
--
--   3. 该脚本是幂等的，可以在已有库上重复执行；只新建缺失对象。
--
-- 包含的对象：
--   扩展：pg_trgm, vector
--   表：memory_bases / memory_entries / projects / file_records / segments
--       users / segment_comments / issue_markers / segment_revisions
--   触发器：统一维护 updated_at 字段
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- -----------------------------------------------------------------------------
-- 公共函数：自动维护 updated_at
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- 1. TM 记忆库分组
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_bases (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);

CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_bases_name
    ON memory_bases (name);
CREATE INDEX IF NOT EXISTS ix_memory_bases_language_pair
    ON memory_bases (source_language, target_language);

DROP TRIGGER IF EXISTS update_memory_bases_updated_at ON memory_bases;
CREATE TRIGGER update_memory_bases_updated_at
    BEFORE UPDATE ON memory_bases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 2. 翻译记忆条目（支持 trigram + pgvector 混合检索）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_entries (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    collection_id UUID REFERENCES memory_bases(id) ON DELETE SET NULL,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    source_hash VARCHAR(64),
    source_normalized TEXT,
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    source_embedding vector(128),
    source_embedding_version INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_memory_entries_creator_id
    ON memory_entries (creator_id);

CREATE INDEX IF NOT EXISTS ix_memory_entries_collection_id
    ON memory_entries (collection_id);
CREATE INDEX IF NOT EXISTS ix_memory_entries_collection_source_hash
    ON memory_entries (collection_id, source_hash);
CREATE INDEX IF NOT EXISTS ix_memory_entries_collection_source_normalized
    ON memory_entries (collection_id, source_normalized);
CREATE INDEX IF NOT EXISTS ix_memory_entries_source_hash
    ON memory_entries (source_hash);
CREATE INDEX IF NOT EXISTS ix_memory_entries_source_text
    ON memory_entries (source_text);
CREATE INDEX IF NOT EXISTS ix_memory_entries_source_normalized
    ON memory_entries (source_normalized);
CREATE INDEX IF NOT EXISTS ix_memory_entries_language_pair
    ON memory_entries (source_language, target_language);
CREATE INDEX IF NOT EXISTS ix_memory_entries_collection_language_pair
    ON memory_entries (collection_id, source_language, target_language);
CREATE INDEX IF NOT EXISTS ix_memory_entries_source_text_trgm
    ON memory_entries
    USING GIN (source_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_memory_entries_source_normalized_trgm
    ON memory_entries
    USING GIN (source_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_memory_entries_source_embedding_version
    ON memory_entries (source_embedding_version);
CREATE INDEX IF NOT EXISTS ix_memory_entries_source_embedding_ivfflat
    ON memory_entries
    USING ivfflat (source_embedding vector_cosine_ops)
    WITH (lists = 100);

DROP TRIGGER IF EXISTS update_memory_entries_updated_at ON memory_entries;
CREATE TRIGGER update_memory_entries_updated_at
    BEFORE UPDATE ON memory_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 兼容旧库：把 collection_id 仍为 NULL 的历史记录迁入「默认记忆库」
INSERT INTO memory_bases (name, description)
SELECT '默认记忆库', '迁移前已有的 TM 记录'
WHERE EXISTS (
    SELECT 1
    FROM memory_entries
    WHERE collection_id IS NULL
)
ON CONFLICT (name) DO NOTHING;

UPDATE memory_entries
SET collection_id = (
    SELECT id
    FROM memory_bases
    WHERE name = '默认记忆库'
    LIMIT 1
)
WHERE collection_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM memory_bases
      WHERE name = '默认记忆库'
  );

-- -----------------------------------------------------------------------------
-- 3. 用户账号
-- -----------------------------------------------------------------------------
UPDATE memory_entries AS tm
SET source_language = COALESCE(tm.source_language, collection.source_language),
    target_language = COALESCE(tm.target_language, collection.target_language)
FROM memory_bases AS collection
WHERE tm.collection_id = collection.id
  AND (
      tm.source_language IS DISTINCT FROM collection.source_language
      OR tm.target_language IS DISTINCT FROM collection.target_language
  );

CREATE TABLE IF NOT EXISTS term_bases (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS term_bases
    ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE IF EXISTS term_bases
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS term_bases
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS term_bases
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS term_bases
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_term_bases_name
    ON term_bases (name);
CREATE INDEX IF NOT EXISTS ix_term_bases_language_pair
    ON term_bases (source_language, target_language);

DROP TRIGGER IF EXISTS update_term_bases_updated_at ON term_bases;
CREATE TRIGGER update_term_bases_updated_at
    BEFORE UPDATE ON term_bases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS term_entries (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    term_base_id UUID NOT NULL REFERENCES term_bases(id) ON DELETE CASCADE,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    source_normalized TEXT,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS source_normalized TEXT;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_term_entries_creator_id
    ON term_entries (creator_id);

CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_id
    ON term_entries (term_base_id);
CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_source_text
    ON term_entries (term_base_id, source_text);
CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_source_normalized
    ON term_entries (term_base_id, source_normalized);
CREATE INDEX IF NOT EXISTS ix_term_entries_language_pair
    ON term_entries (source_language, target_language);

DROP TRIGGER IF EXISTS update_term_entries_updated_at ON term_entries;
CREATE TRIGGER update_term_entries_updated_at
    BEFORE UPDATE ON term_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    username VARCHAR(50) NOT NULL UNIQUE,
    nickname VARCHAR(50),
    hashed_password VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    translator_type VARCHAR(20) NOT NULL DEFAULT 'internal',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS users
    ADD COLUMN IF NOT EXISTS nickname VARCHAR(50);
ALTER TABLE IF EXISTS users
    ADD COLUMN IF NOT EXISTS translator_type VARCHAR(20) NOT NULL DEFAULT 'internal';

UPDATE users
SET nickname = username
WHERE nickname IS NULL OR BTRIM(nickname) = '';

UPDATE users
SET translator_type = 'internal'
WHERE translator_type IS NULL
   OR translator_type NOT IN ('internal', 'external');

CREATE INDEX IF NOT EXISTS ix_users_username
    ON users (username);

-- -----------------------------------------------------------------------------
-- 3.1 预翻译词汇表（独立于译后 QA 术语库）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS glossary_bases (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_glossary_bases_name
    ON glossary_bases (name);
CREATE INDEX IF NOT EXISTS ix_glossary_bases_language_pair
    ON glossary_bases (source_language, target_language);

DROP TRIGGER IF EXISTS update_glossary_bases_updated_at ON glossary_bases;
CREATE TRIGGER update_glossary_bases_updated_at
    BEFORE UPDATE ON glossary_bases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS glossary_entries (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    glossary_base_id UUID NOT NULL REFERENCES glossary_bases(id) ON DELETE CASCADE,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    note TEXT,
    source_normalized TEXT,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS note TEXT;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS source_normalized TEXT;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_glossary_entries_creator_id
    ON glossary_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_glossary_base_id
    ON glossary_entries (glossary_base_id);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_base_source_text
    ON glossary_entries (glossary_base_id, source_text);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_base_source_normalized
    ON glossary_entries (glossary_base_id, source_normalized);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_language_pair
    ON glossary_entries (source_language, target_language);

DROP TRIGGER IF EXISTS update_glossary_entries_updated_at ON glossary_entries;
CREATE TRIGGER update_glossary_entries_updated_at
    BEFORE UPDATE ON glossary_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 4. 项目父级
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    name VARCHAR(200) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full',
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    deadline TIMESTAMP,
    access_level VARCHAR(20) NOT NULL DEFAULT 'team',
    translation_guidelines TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS projects
    ADD COLUMN IF NOT EXISTS document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full';
ALTER TABLE IF EXISTS projects
    ADD COLUMN IF NOT EXISTS translation_guidelines TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_projects_creator_id
    ON projects (creator_id);
CREATE INDEX IF NOT EXISTS ix_projects_status
    ON projects (status);

DROP TRIGGER IF EXISTS update_projects_updated_at ON projects;
CREATE TRIGGER update_projects_updated_at
    BEFORE UPDATE ON projects
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 5. 翻译工作台：文件记录
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS file_records (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full',
    document_parse_options TEXT NOT NULL DEFAULT '{}',
    document_statistics TEXT NOT NULL DEFAULT '{}',
    active_operation VARCHAR(40),
    active_operation_token VARCHAR(64),
    active_operation_updated_at TIMESTAMP,
    active_operation_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assignee_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMP,
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    collection_id UUID REFERENCES memory_bases(id) ON DELETE SET NULL,
    collection_ids_json TEXT NOT NULL DEFAULT '[]',
    tm_match_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    term_base_id UUID REFERENCES term_bases(id) ON DELETE SET NULL,
    glossary_base_ids TEXT NOT NULL DEFAULT '[]',
    deadline TIMESTAMP,
    access_level VARCHAR(20) NOT NULL DEFAULT 'team',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full';
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS document_parse_options TEXT NOT NULL DEFAULT '{}';
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS document_statistics TEXT NOT NULL DEFAULT '{}';
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS active_operation VARCHAR(40);
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS active_operation_token VARCHAR(64);
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS active_operation_updated_at TIMESTAMP;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS active_operation_user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS assignee_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS collection_id UUID REFERENCES memory_bases(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS collection_ids_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS tm_match_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.8;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS term_base_id UUID REFERENCES term_bases(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS glossary_base_ids TEXT NOT NULL DEFAULT '[]';
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS deadline TIMESTAMP;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS access_level VARCHAR(20) NOT NULL DEFAULT 'team';

CREATE INDEX IF NOT EXISTS ix_file_records_project_id
    ON file_records (project_id);
CREATE INDEX IF NOT EXISTS ix_file_records_creator_id
    ON file_records (creator_id);
CREATE INDEX IF NOT EXISTS ix_file_records_source_language
    ON file_records (source_language);
CREATE INDEX IF NOT EXISTS ix_file_records_status
    ON file_records (status);
CREATE INDEX IF NOT EXISTS ix_file_records_active_operation
    ON file_records (active_operation);
CREATE INDEX IF NOT EXISTS ix_file_records_assignee_id
    ON file_records (assignee_id);

-- -----------------------------------------------------------------------------
-- 5.1 文档字数统计报告快照
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_statistics_reports (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    file_ids TEXT NOT NULL DEFAULT '[]',
    total_files INTEGER NOT NULL DEFAULT 0,
    available_files INTEGER NOT NULL DEFAULT 0,
    totals TEXT NOT NULL DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_statistics_report_items (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    report_id UUID NOT NULL REFERENCES document_statistics_reports(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL,
    file_name VARCHAR(255) NOT NULL,
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    file_size_bytes INTEGER,
    statistics TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_project_id
    ON document_statistics_reports (project_id);
CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_created_by_id
    ON document_statistics_reports (created_by_id);
CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_created_at
    ON document_statistics_reports (created_at);
CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_report_id
    ON document_statistics_report_items (report_id);
CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_project_id
    ON document_statistics_report_items (project_id);
CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_file_record_id
    ON document_statistics_report_items (file_record_id);

INSERT INTO projects (
    id,
    name,
    status,
    source_language,
    target_language,
    creator_id,
    deadline,
    access_level,
    created_at,
    updated_at
)
SELECT
    fr.id,
    fr.filename,
    fr.status,
    fr.source_language,
    fr.target_language,
    fr.creator_id,
    fr.deadline,
    COALESCE(NULLIF(fr.access_level, ''), 'team'),
    fr.created_at,
    fr.updated_at
FROM file_records AS fr
WHERE fr.project_id IS NULL
ON CONFLICT (id) DO NOTHING;

UPDATE file_records AS fr
SET project_id = fr.id
WHERE fr.project_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM projects AS p
      WHERE p.id = fr.id
  );

CREATE TABLE IF NOT EXISTS project_assignments (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    revoked_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    revoked_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS file_assignments (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    revoked_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    revoked_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS assignment_events (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
    assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(40) NOT NULL,
    before_payload TEXT NOT NULL DEFAULT '{}',
    after_payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(40) NOT NULL,
    title VARCHAR(200) NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
    related_event_id UUID REFERENCES assignment_events(id) ON DELETE SET NULL,
    read_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_project_assignments_project_id
    ON project_assignments (project_id);
CREATE INDEX IF NOT EXISTS ix_project_assignments_assignee_id
    ON project_assignments (assignee_id);
CREATE INDEX IF NOT EXISTS ix_project_assignments_status
    ON project_assignments (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_assignments_active_project_user
    ON project_assignments (project_id, assignee_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_file_assignments_project_id
    ON file_assignments (project_id);
CREATE INDEX IF NOT EXISTS ix_file_assignments_file_record_id
    ON file_assignments (file_record_id);
CREATE INDEX IF NOT EXISTS ix_file_assignments_assignee_id
    ON file_assignments (assignee_id);
CREATE INDEX IF NOT EXISTS ix_file_assignments_status
    ON file_assignments (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_file_assignments_active_file_user
    ON file_assignments (file_record_id, assignee_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_assignment_events_project_id
    ON assignment_events (project_id);
CREATE INDEX IF NOT EXISTS ix_assignment_events_file_record_id
    ON assignment_events (file_record_id);
CREATE INDEX IF NOT EXISTS ix_assignment_events_assignee_id
    ON assignment_events (assignee_id);
CREATE INDEX IF NOT EXISTS ix_assignment_events_actor_id
    ON assignment_events (actor_id);
CREATE INDEX IF NOT EXISTS ix_assignment_events_created_at
    ON assignment_events (created_at);

CREATE INDEX IF NOT EXISTS ix_notifications_user_id
    ON notifications (user_id);
CREATE INDEX IF NOT EXISTS ix_notifications_read_at
    ON notifications (read_at);
CREATE INDEX IF NOT EXISTS ix_notifications_created_at
    ON notifications (created_at);

INSERT INTO project_assignments (
    project_id,
    assignee_id,
    assigned_by_id,
    assigned_at,
    status
)
SELECT DISTINCT
    fr.project_id,
    fr.assignee_id,
    fr.assigned_by_id,
    COALESCE(fr.assigned_at, NOW()),
    'active'
FROM file_records AS fr
WHERE fr.project_id IS NOT NULL
  AND fr.assignee_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM project_assignments AS pa
      WHERE pa.project_id = fr.project_id
        AND pa.assignee_id = fr.assignee_id
        AND pa.status = 'active'
  );

INSERT INTO file_assignments (
    project_id,
    file_record_id,
    assignee_id,
    assigned_by_id,
    assigned_at,
    status
)
SELECT
    fr.project_id,
    fr.id,
    fr.assignee_id,
    fr.assigned_by_id,
    COALESCE(fr.assigned_at, NOW()),
    'active'
FROM file_records AS fr
WHERE fr.project_id IS NOT NULL
  AND fr.assignee_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM file_assignments AS fa
      WHERE fa.file_record_id = fr.id
        AND fa.assignee_id = fr.assignee_id
        AND fa.status = 'active'
  );

DROP TRIGGER IF EXISTS update_file_records_updated_at ON file_records;
CREATE TRIGGER update_file_records_updated_at
    BEFORE UPDATE ON file_records
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 6. 翻译工作台：句段
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS segments (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    sentence_id VARCHAR(20) NOT NULL,
    source_text TEXT NOT NULL,
    source_hash VARCHAR(64),
    display_text TEXT NOT NULL,
    source_html TEXT,
    target_text TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'none',
    project_sync_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    score FLOAT NOT NULL DEFAULT 0.0,
    matched_source_text TEXT,
    matched_collection_name VARCHAR(120),
    matched_creator_name VARCHAR(100),
    matched_created_at TIMESTAMP,
    matched_updated_at TIMESTAMP,
    source VARCHAR(20) NOT NULL DEFAULT 'tm',
    llm_provider VARCHAR(40),
    llm_model VARCHAR(200),
    block_type VARCHAR(20) NOT NULL DEFAULT 'paragraph',
    block_index INTEGER NOT NULL DEFAULT 0,
    row_index INTEGER,
    cell_index INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64);
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS source_html TEXT;
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS project_sync_disabled BOOLEAN NOT NULL DEFAULT FALSE;
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

CREATE INDEX IF NOT EXISTS ix_segments_file_record_id
    ON segments (file_record_id);
CREATE INDEX IF NOT EXISTS ix_segments_source_hash
    ON segments (source_hash);
CREATE INDEX IF NOT EXISTS ix_segments_file_source_hash
    ON segments (file_record_id, source_hash);
CREATE INDEX IF NOT EXISTS ix_segments_file_record_order
    ON segments (file_record_id, block_index, row_index, cell_index, sentence_id);

DROP TRIGGER IF EXISTS update_segments_updated_at ON segments;
CREATE TRIGGER update_segments_updated_at
    BEFORE UPDATE ON segments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 7. 句段批注（含嵌套回复）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS segment_comments (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    segment_id UUID REFERENCES segments(id) ON DELETE SET NULL,
    anchor_mode VARCHAR(20) NOT NULL DEFAULT 'sentence',
    range_start_offset INTEGER,
    range_end_offset INTEGER,
    anchor_text TEXT,
    body TEXT NOT NULL,
    author_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES segment_comments(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_segment_comments_file_record_id
    ON segment_comments (file_record_id);
CREATE INDEX IF NOT EXISTS ix_segment_comments_segment_id
    ON segment_comments (segment_id);
CREATE INDEX IF NOT EXISTS ix_segment_comments_parent_id
    ON segment_comments (parent_id);

DROP TRIGGER IF EXISTS update_segment_comments_updated_at ON segment_comments;
CREATE TRIGGER update_segment_comments_updated_at
    BEFORE UPDATE ON segment_comments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 8. 灰度问题标记
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS issue_markers (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL,
    title VARCHAR(160) NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    category VARCHAR(30) NOT NULL DEFAULT 'other',
    severity VARCHAR(20) NOT NULL DEFAULT 'medium',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    page_url TEXT,
    user_agent TEXT,
    reporter_id UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_issue_markers_project_id
    ON issue_markers (project_id);
CREATE INDEX IF NOT EXISTS ix_issue_markers_file_record_id
    ON issue_markers (file_record_id);
CREATE INDEX IF NOT EXISTS ix_issue_markers_status
    ON issue_markers (status);
CREATE INDEX IF NOT EXISTS ix_issue_markers_reporter_id
    ON issue_markers (reporter_id);

DROP TRIGGER IF EXISTS update_issue_markers_updated_at ON issue_markers;
CREATE TRIGGER update_issue_markers_updated_at
    BEFORE UPDATE ON issue_markers
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 9. Segment revisions
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS segment_revisions (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    segment_id UUID NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    sentence_id VARCHAR(20) NOT NULL,
    before_text TEXT NOT NULL DEFAULT '',
    after_text TEXT NOT NULL DEFAULT '',
    source VARCHAR(20) NOT NULL DEFAULT 'manual',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    author_id UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_segment_revisions_file_record_id
    ON segment_revisions (file_record_id);
CREATE INDEX IF NOT EXISTS ix_segment_revisions_segment_id
    ON segment_revisions (segment_id);
CREATE INDEX IF NOT EXISTS ix_segment_revisions_sentence_id
    ON segment_revisions (sentence_id);
CREATE INDEX IF NOT EXISTS ix_segment_revisions_status
    ON segment_revisions (status);

-- =============================================================================
-- 完成。首次运行后请通过前端 "/login" 页面使用首次初始化接口创建管理员账号：
--   POST /api/auth/init  { "username": "admin", "password": "..." }
-- =============================================================================
