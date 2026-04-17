CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE translation_memory
    ADD COLUMN IF NOT EXISTS source_embedding vector(128),
    ADD COLUMN IF NOT EXISTS source_embedding_version INTEGER;

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_embedding_version
    ON translation_memory (source_embedding_version);

CREATE INDEX IF NOT EXISTS ix_translation_memory_source_embedding_ivfflat
    ON translation_memory
    USING ivfflat (source_embedding vector_cosine_ops)
    WITH (lists = 100);

ANALYZE translation_memory;
