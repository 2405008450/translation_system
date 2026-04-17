CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS tm_collections (
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
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tm_collections_name
    ON tm_collections (name);

CREATE TABLE IF NOT EXISTS translation_memory (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    collection_id UUID REFERENCES tm_collections(id) ON DELETE SET NULL,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    source_hash VARCHAR(64),
    source_normalized TEXT,
    source_embedding vector(128),
    source_embedding_version INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_translation_memory_collection_id
    ON translation_memory (collection_id);

CREATE INDEX IF NOT EXISTS ix_translation_memory_collection_source_hash
    ON translation_memory (collection_id, source_hash);

CREATE INDEX IF NOT EXISTS ix_translation_memory_collection_source_normalized
    ON translation_memory (collection_id, source_normalized);

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_hash
    ON translation_memory (source_hash);

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_text
    ON translation_memory (source_text);

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_normalized
    ON translation_memory (source_normalized);

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_text_trgm
    ON translation_memory
    USING GIN (source_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_normalized_trgm
    ON translation_memory
    USING GIN (source_normalized gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_embedding_version
    ON translation_memory (source_embedding_version);

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_embedding_ivfflat
    ON translation_memory
    USING ivfflat (source_embedding vector_cosine_ops)
    WITH (lists = 100);

INSERT INTO tm_collections (name, description)
SELECT '默认记忆库', '迁移前已有的 TM 记录'
WHERE EXISTS (
    SELECT 1
    FROM translation_memory
    WHERE collection_id IS NULL
)
ON CONFLICT (name) DO NOTHING;

UPDATE translation_memory
SET collection_id = (
    SELECT id
    FROM tm_collections
    WHERE name = '默认记忆库'
    LIMIT 1
)
WHERE collection_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM tm_collections
      WHERE name = '默认记忆库'
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
    filename VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

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
    display_text TEXT NOT NULL,
    target_text TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'none',
    score FLOAT NOT NULL DEFAULT 0.0,
    matched_source_text TEXT,
    source VARCHAR(20) NOT NULL DEFAULT 'tm',
    block_type VARCHAR(20) NOT NULL DEFAULT 'paragraph',
    block_index INTEGER NOT NULL DEFAULT 0,
    row_index INTEGER,
    cell_index INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_segments_file_record_id
    ON segments (file_record_id);

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_file_records_updated_at ON file_records;
CREATE TRIGGER update_file_records_updated_at
    BEFORE UPDATE ON file_records
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_tm_collections_updated_at ON tm_collections;
CREATE TRIGGER update_tm_collections_updated_at
    BEFORE UPDATE ON tm_collections
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_translation_memory_updated_at ON translation_memory;
CREATE TRIGGER update_translation_memory_updated_at
    BEFORE UPDATE ON translation_memory
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_segments_updated_at ON segments;
CREATE TRIGGER update_segments_updated_at
    BEFORE UPDATE ON segments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
