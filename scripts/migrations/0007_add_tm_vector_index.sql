CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE memory_entries
    ADD COLUMN IF NOT EXISTS source_embedding vector(128),
    ADD COLUMN IF NOT EXISTS source_embedding_version INTEGER;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_memory_entries_source_embedding_version
    ON memory_entries (source_embedding_version);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_memory_entries_source_embedding_ivfflat
    ON memory_entries
    USING ivfflat (source_embedding vector_cosine_ops)
    WITH (lists = 100);

ANALYZE memory_entries;
