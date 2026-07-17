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
CREATE EXTENSION IF NOT EXISTS btree_gin;
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
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_bases_name
    ON memory_bases (name);
CREATE INDEX IF NOT EXISTS ix_memory_bases_language_pair
    ON memory_bases (source_language, target_language);
CREATE INDEX IF NOT EXISTS ix_memory_bases_creator_id
    ON memory_bases (creator_id);

DROP TRIGGER IF EXISTS update_memory_bases_updated_at ON memory_bases;
CREATE TRIGGER update_memory_bases_updated_at
    BEFORE UPDATE ON memory_bases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS resource_import_batches (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    resource_type VARCHAR(20) NOT NULL,
    resource_id UUID,
    filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    file_format VARCHAR(20) NOT NULL DEFAULT '',
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    tmx_header_metadata JSONB,
    created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_resource_import_batches_resource
    ON resource_import_batches (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS ix_resource_import_batches_created_at
    ON resource_import_batches (created_at);

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
    last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    external_tuid TEXT,
    tmx_metadata JSONB,
    import_batch_id UUID REFERENCES resource_import_batches(id) ON DELETE SET NULL,
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
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS external_tuid TEXT;
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS tmx_metadata JSONB;
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS import_batch_id UUID REFERENCES resource_import_batches(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_memory_entries_creator_id
    ON memory_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_memory_entries_last_modified_by_id
    ON memory_entries (last_modified_by_id);
CREATE INDEX IF NOT EXISTS ix_memory_entries_external_tuid
    ON memory_entries (external_tuid);
CREATE INDEX IF NOT EXISTS ix_memory_entries_import_batch_id
    ON memory_entries (import_batch_id);

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
CREATE INDEX IF NOT EXISTS ix_memory_entries_lang_collection_source_normalized_trgm
    ON memory_entries
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops)
    WHERE source_normalized IS NOT NULL;
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

CREATE TABLE IF NOT EXISTS memory_entry_search (
    entry_id UUID NOT NULL,
    collection_id UUID,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    source_hash VARCHAR(64),
    source_normalized TEXT NOT NULL,
    source_length INTEGER NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (source_length);

CREATE TABLE IF NOT EXISTS memory_entry_search_len_000_016
    PARTITION OF memory_entry_search FOR VALUES FROM (0) TO (17);
CREATE TABLE IF NOT EXISTS memory_entry_search_len_017_040
    PARTITION OF memory_entry_search FOR VALUES FROM (17) TO (41);
CREATE TABLE IF NOT EXISTS memory_entry_search_len_041_080
    PARTITION OF memory_entry_search FOR VALUES FROM (41) TO (81);
CREATE TABLE IF NOT EXISTS memory_entry_search_len_081_160
    PARTITION OF memory_entry_search FOR VALUES FROM (81) TO (161);
CREATE TABLE IF NOT EXISTS memory_entry_search_len_161_320
    PARTITION OF memory_entry_search FOR VALUES FROM (161) TO (321);
CREATE TABLE IF NOT EXISTS memory_entry_search_len_321_plus
    PARTITION OF memory_entry_search FOR VALUES FROM (321) TO (MAXVALUE);

CREATE INDEX IF NOT EXISTS ix_mes_000_016_entry_id
    ON memory_entry_search_len_000_016 (entry_id);
CREATE INDEX IF NOT EXISTS ix_mes_017_040_entry_id
    ON memory_entry_search_len_017_040 (entry_id);
CREATE INDEX IF NOT EXISTS ix_mes_041_080_entry_id
    ON memory_entry_search_len_041_080 (entry_id);
CREATE INDEX IF NOT EXISTS ix_mes_081_160_entry_id
    ON memory_entry_search_len_081_160 (entry_id);
CREATE INDEX IF NOT EXISTS ix_mes_161_320_entry_id
    ON memory_entry_search_len_161_320 (entry_id);
CREATE INDEX IF NOT EXISTS ix_mes_321_plus_entry_id
    ON memory_entry_search_len_321_plus (entry_id);

CREATE INDEX IF NOT EXISTS ix_mes_000_016_scope_trgm
    ON memory_entry_search_len_000_016
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_mes_017_040_scope_trgm
    ON memory_entry_search_len_017_040
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_mes_041_080_scope_trgm
    ON memory_entry_search_len_041_080
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_mes_081_160_scope_trgm
    ON memory_entry_search_len_081_160
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_mes_161_320_scope_trgm
    ON memory_entry_search_len_161_320
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_mes_321_plus_scope_trgm
    ON memory_entry_search_len_321_plus
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);

CREATE OR REPLACE FUNCTION sync_memory_entry_search_projection()
RETURNS TRIGGER AS $$
DECLARE
    normalized_length INTEGER;
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM memory_entry_search WHERE entry_id = OLD.id;
        RETURN OLD;
    END IF;

    DELETE FROM memory_entry_search WHERE entry_id = NEW.id;

    IF NEW.source_normalized IS NULL
       OR NEW.source_language IS NULL
       OR NEW.target_language IS NULL THEN
        RETURN NEW;
    END IF;

    normalized_length := char_length(NEW.source_normalized);
    IF normalized_length <= 0 THEN
        RETURN NEW;
    END IF;

    INSERT INTO memory_entry_search (
        entry_id,
        collection_id,
        source_language,
        target_language,
        source_hash,
        source_normalized,
        source_length,
        updated_at
    )
    VALUES (
        NEW.id,
        NEW.collection_id,
        NEW.source_language,
        NEW.target_language,
        NEW.source_hash,
        NEW.source_normalized,
        normalized_length,
        COALESCE(NEW.updated_at, NOW())
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sync_memory_entry_search_projection_trg ON memory_entries;
CREATE TRIGGER sync_memory_entry_search_projection_trg
    AFTER INSERT OR UPDATE OR DELETE ON memory_entries
    FOR EACH ROW
    EXECUTE FUNCTION sync_memory_entry_search_projection();

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
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
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
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS term_bases
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS term_bases
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_term_bases_name
    ON term_bases (name);
CREATE INDEX IF NOT EXISTS ix_term_bases_language_pair
    ON term_bases (source_language, target_language);
CREATE INDEX IF NOT EXISTS ix_term_bases_creator_id
    ON term_bases (creator_id);

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
    last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    external_tuid TEXT,
    tmx_metadata JSONB,
    import_batch_id UUID REFERENCES resource_import_batches(id) ON DELETE SET NULL,
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
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS external_tuid TEXT;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS tmx_metadata JSONB;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS import_batch_id UUID REFERENCES resource_import_batches(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_term_entries_creator_id
    ON term_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_term_entries_last_modified_by_id
    ON term_entries (last_modified_by_id);
CREATE INDEX IF NOT EXISTS ix_term_entries_external_tuid
    ON term_entries (external_tuid);
CREATE INDEX IF NOT EXISTS ix_term_entries_import_batch_id
    ON term_entries (import_batch_id);

CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_id
    ON term_entries (term_base_id);
CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_source_text
    ON term_entries (term_base_id, source_text);
CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_source_normalized
    ON term_entries (term_base_id, source_normalized);
CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_updated_created
    ON term_entries (term_base_id, updated_at DESC, created_at DESC);
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
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
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
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_glossary_bases_name
    ON glossary_bases (name);
CREATE INDEX IF NOT EXISTS ix_glossary_bases_language_pair
    ON glossary_bases (source_language, target_language);
CREATE INDEX IF NOT EXISTS ix_glossary_bases_creator_id
    ON glossary_bases (creator_id);

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
    last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
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
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_glossary_entries_creator_id
    ON glossary_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_last_modified_by_id
    ON glossary_entries (last_modified_by_id);
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
    quality_qa_settings TEXT NOT NULL DEFAULT '{}',
    auto_tm_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS projects
    ADD COLUMN IF NOT EXISTS document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full';
ALTER TABLE IF EXISTS projects
    ADD COLUMN IF NOT EXISTS translation_guidelines TEXT NOT NULL DEFAULT '';
ALTER TABLE IF EXISTS projects
    ADD COLUMN IF NOT EXISTS quality_qa_settings TEXT NOT NULL DEFAULT '{}';
ALTER TABLE IF EXISTS projects
    ADD COLUMN IF NOT EXISTS auto_tm_enabled BOOLEAN NOT NULL DEFAULT TRUE;

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
CREATE TABLE IF NOT EXISTS project_workflow_steps (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step_key VARCHAR(40) NOT NULL,
    name VARCHAR(80) NOT NULL,
    step_type VARCHAR(20) NOT NULL DEFAULT 'custom',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS step_key VARCHAR(40) NOT NULL DEFAULT 'translate';
ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS name VARCHAR(80) NOT NULL DEFAULT '翻译';
ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS step_type VARCHAR(20) NOT NULL DEFAULT 'custom';
ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_project_workflow_steps_project_id
    ON project_workflow_steps (project_id);
CREATE INDEX IF NOT EXISTS ix_project_workflow_steps_project_order
    ON project_workflow_steps (project_id, sort_order);

INSERT INTO project_workflow_steps (project_id, step_key, name, step_type, sort_order)
SELECT p.id, 'translate', '翻译', 'translation', 0
FROM projects AS p
WHERE NOT EXISTS (
    SELECT 1
    FROM project_workflow_steps AS pws
    WHERE pws.project_id = p.id
);

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
    tm_match_signature VARCHAR(64),
    tm_last_matched_at TIMESTAMP,
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
    ADD COLUMN IF NOT EXISTS tm_match_signature VARCHAR(64);
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS tm_last_matched_at TIMESTAMP;
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
    workflow_step_id UUID REFERENCES project_workflow_steps(id) ON DELETE CASCADE,
    assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    revoked_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    revoked_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    segment_range_start INTEGER,
    segment_range_end INTEGER
);

ALTER TABLE IF EXISTS file_assignments
    ADD COLUMN IF NOT EXISTS workflow_step_id UUID REFERENCES project_workflow_steps(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS file_assignments
    ADD COLUMN IF NOT EXISTS segment_range_start INTEGER;
ALTER TABLE IF EXISTS file_assignments
    ADD COLUMN IF NOT EXISTS segment_range_end INTEGER;

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
DROP INDEX IF EXISTS uq_file_assignments_active_file_user;
CREATE UNIQUE INDEX IF NOT EXISTS uq_file_assignments_active_file_step_user
    ON file_assignments (file_record_id, workflow_step_id, assignee_id)
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
    workflow_step_id,
    assignee_id,
    assigned_by_id,
    assigned_at,
    status
)
SELECT
    fr.project_id,
    fr.id,
    (
        SELECT pws.id
        FROM project_workflow_steps AS pws
        WHERE pws.project_id = fr.project_id
        ORDER BY pws.sort_order ASC, pws.id ASC
        LIMIT 1
    ),
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

UPDATE file_assignments AS fa
SET workflow_step_id = first_step.id
FROM (
    SELECT DISTINCT ON (project_id)
        id,
        project_id
    FROM project_workflow_steps
    ORDER BY project_id, sort_order ASC, id ASC
) AS first_step
WHERE fa.workflow_step_id IS NULL
  AND fa.project_id = first_step.project_id;

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
    workflow_step_id UUID REFERENCES project_workflow_steps(id) ON DELETE SET NULL,
    sentence_id VARCHAR(100) NOT NULL,
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
    source VARCHAR(40) NOT NULL DEFAULT 'tm',
    llm_provider VARCHAR(40),
    llm_model VARCHAR(200),
    block_type VARCHAR(20) NOT NULL DEFAULT 'paragraph',
    block_index INTEGER NOT NULL DEFAULT 0,
    row_index INTEGER,
    cell_index INTEGER,
    sequence_index INTEGER NOT NULL DEFAULT -1,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS workflow_step_id UUID REFERENCES project_workflow_steps(id) ON DELETE SET NULL;
UPDATE segments AS s
SET workflow_step_id = first_step.id
FROM file_records AS fr
JOIN (
    SELECT DISTINCT ON (project_id)
        id,
        project_id
    FROM project_workflow_steps
    ORDER BY project_id, sort_order ASC, id ASC
) AS first_step ON first_step.project_id = fr.project_id
WHERE s.workflow_step_id IS NULL
  AND s.file_record_id = fr.id;

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
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS segment_metadata TEXT NOT NULL DEFAULT '{}';
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS sequence_index INTEGER NOT NULL DEFAULT -1;

CREATE INDEX IF NOT EXISTS ix_segments_file_record_id
    ON segments (file_record_id);
CREATE INDEX IF NOT EXISTS ix_segments_source_hash
    ON segments (source_hash);
CREATE INDEX IF NOT EXISTS ix_segments_file_source_hash
    ON segments (file_record_id, source_hash);
CREATE INDEX IF NOT EXISTS ix_segments_file_record_order
    ON segments (file_record_id, block_index, row_index, cell_index, sentence_id);
CREATE INDEX IF NOT EXISTS ix_segments_file_record_sequence_order
    ON segments (file_record_id, block_index, row_index, cell_index, sequence_index, sentence_id);
CREATE INDEX IF NOT EXISTS ix_segments_workflow_step_id
    ON segments (workflow_step_id);
-- 检索/筛选加速：scope、status_filters、match_filters 频繁按这些列过滤。
CREATE INDEX IF NOT EXISTS ix_segments_file_record_status
    ON segments (file_record_id, status);
CREATE INDEX IF NOT EXISTS ix_segments_file_record_source
    ON segments (file_record_id, source);
-- 增量游标端点 /segments/changes 按 updated_at 过滤并排序。
CREATE INDEX IF NOT EXISTS ix_segments_updated_at
    ON segments (updated_at);
-- 文本检索：ilike('%kw%') 双向通配无法走 B-tree，改用 pg_trgm GIN 索引加速。
CREATE INDEX IF NOT EXISTS ix_segments_source_text_trgm
    ON segments USING GIN (source_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_segments_display_text_trgm
    ON segments USING GIN (display_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_segments_target_text_trgm
    ON segments USING GIN (target_text gin_trgm_ops);

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
-- 8.1 Segment spelling / grammar QA issues
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS segment_qa_issues (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    segment_id UUID NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    sentence_id VARCHAR(100) NOT NULL DEFAULT '',
    rule_key VARCHAR(40) NOT NULL DEFAULT 'spelling_grammar',
    provider VARCHAR(40) NOT NULL DEFAULT 'languagetool',
    language VARCHAR(20) NOT NULL DEFAULT '',
    severity VARCHAR(20) NOT NULL DEFAULT 'medium',
    message TEXT NOT NULL DEFAULT '',
    short_message TEXT NOT NULL DEFAULT '',
    rule_id VARCHAR(120) NOT NULL DEFAULT '',
    rule_category VARCHAR(120) NOT NULL DEFAULT '',
    issue_type VARCHAR(80) NOT NULL DEFAULT '',
    context_text TEXT NOT NULL DEFAULT '',
    "offset" INTEGER NOT NULL DEFAULT 0,
    length INTEGER NOT NULL DEFAULT 0,
    replacements TEXT NOT NULL DEFAULT '[]',
    target_text_hash VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    ignored_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    ignored_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_project_id
    ON segment_qa_issues (project_id);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_file_record_id
    ON segment_qa_issues (file_record_id);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_segment_id
    ON segment_qa_issues (segment_id);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_segment_rule_status
    ON segment_qa_issues (segment_id, rule_key, status);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_status
    ON segment_qa_issues (status);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_rule_key
    ON segment_qa_issues (rule_key);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_target_hash
    ON segment_qa_issues (target_text_hash);

DROP TRIGGER IF EXISTS update_segment_qa_issues_updated_at ON segment_qa_issues;
CREATE TRIGGER update_segment_qa_issues_updated_at
    BEFORE UPDATE ON segment_qa_issues
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
    sentence_id VARCHAR(100) NOT NULL,
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

-- -----------------------------------------------------------------------------
-- 10. Revision display settings
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS revision_display_settings (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    show_author_time BOOLEAN NOT NULL DEFAULT TRUE,
    show_others_revisions BOOLEAN NOT NULL DEFAULT TRUE,
    default_insert_color VARCHAR(20) NOT NULL DEFAULT '#2563eb',
    default_delete_color VARCHAR(20) NOT NULL DEFAULT '#dc2626',
    author_colors JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_revision_display_settings_file_record_id UNIQUE (file_record_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_revision_display_settings_file_record_id
    ON revision_display_settings (file_record_id);
CREATE INDEX IF NOT EXISTS ix_revision_display_settings_updated_by_id
    ON revision_display_settings (updated_by_id);

-- -----------------------------------------------------------------------------
-- 7. 项目"合并视图"：记录哪些 file_records 组成一个编辑视图（仅持久化分组）
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_merge_views (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    file_ids TEXT NOT NULL DEFAULT '[]',
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_project_merge_views_project_id
    ON project_merge_views (project_id);

CREATE INDEX IF NOT EXISTS ix_project_merge_views_creator_id
    ON project_merge_views (creator_id);

DROP TRIGGER IF EXISTS update_project_merge_views_updated_at ON project_merge_views;

CREATE TRIGGER update_project_merge_views_updated_at
    BEFORE UPDATE ON project_merge_views
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 11. Translation guideline templates
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS guideline_templates (
    id VARCHAR(120) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    source_path VARCHAR(255),
    created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_guideline_templates_updated_at
    ON guideline_templates (updated_at);
CREATE INDEX IF NOT EXISTS ix_guideline_templates_created_by_id
    ON guideline_templates (created_by_id);
CREATE INDEX IF NOT EXISTS ix_guideline_templates_last_modified_by_id
    ON guideline_templates (last_modified_by_id);

DROP TRIGGER IF EXISTS update_guideline_templates_updated_at ON guideline_templates;
CREATE TRIGGER update_guideline_templates_updated_at
    BEFORE UPDATE ON guideline_templates
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Lin 分支资源同步与 CAD 句段合并功能所需的兼容字段。
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'manual';
CREATE INDEX IF NOT EXISTS ix_memory_bases_project_id
    ON memory_bases (project_id);
CREATE INDEX IF NOT EXISTS ix_memory_bases_origin
    ON memory_bases (origin);

ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'manual';
CREATE INDEX IF NOT EXISTS ix_glossary_bases_project_id
    ON glossary_bases (project_id);
CREATE INDEX IF NOT EXISTS ix_glossary_bases_origin
    ON glossary_bases (origin);

-- =============================================================================
-- 完成。首次运行后请通过前端 "/login" 页面使用首次初始化接口创建管理员账号：
--   POST /api/auth/init  { "username": "admin", "password": "..." }
-- =============================================================================
