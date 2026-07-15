-- Lightweight TM search projection.
-- Keep memory_entries as the source of truth, and use this narrow partitioned
-- table for expensive trigram fuzzy lookup.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

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

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_000_016_entry_id
    ON memory_entry_search_len_000_016 (entry_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_017_040_entry_id
    ON memory_entry_search_len_017_040 (entry_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_041_080_entry_id
    ON memory_entry_search_len_041_080 (entry_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_081_160_entry_id
    ON memory_entry_search_len_081_160 (entry_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_161_320_entry_id
    ON memory_entry_search_len_161_320 (entry_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_321_plus_entry_id
    ON memory_entry_search_len_321_plus (entry_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_000_016_scope_trgm
    ON memory_entry_search_len_000_016
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_017_040_scope_trgm
    ON memory_entry_search_len_017_040
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_041_080_scope_trgm
    ON memory_entry_search_len_041_080
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_081_160_scope_trgm
    ON memory_entry_search_len_081_160
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_161_320_scope_trgm
    ON memory_entry_search_len_161_320
    USING GIN (source_language, target_language, collection_id, source_normalized gin_trgm_ops);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_mes_321_plus_scope_trgm
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
