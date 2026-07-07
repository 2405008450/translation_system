CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_memory_entries_lang_collection_source_normalized_trgm
ON memory_entries
USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops)
WHERE source_normalized IS NOT NULL;
