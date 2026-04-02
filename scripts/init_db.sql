CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS translation_memory (
    id BIGSERIAL PRIMARY KEY,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    source_hash VARCHAR(64),
    source_normalized TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

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
