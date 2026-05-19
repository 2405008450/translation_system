BEGIN;

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION _rename_index_if_exists(old_name text, new_name text)
RETURNS void AS $$
BEGIN
    IF to_regclass(current_schema() || '.' || old_name) IS NOT NULL
       AND to_regclass(current_schema() || '.' || new_name) IS NULL THEN
        EXECUTE format('ALTER INDEX %I RENAME TO %I', old_name, new_name);
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION _rename_constraint_if_exists(
    target_table regclass,
    old_name text,
    new_name text
)
RETURNS void AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = old_name
          AND conrelid = target_table
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = new_name
          AND conrelid = target_table
    ) THEN
        EXECUTE format(
            'ALTER TABLE %s RENAME CONSTRAINT %I TO %I',
            target_table,
            old_name,
            new_name
        );
    END IF;
END;
$$ LANGUAGE plpgsql;

DROP TABLE IF EXISTS terms CASCADE;
DROP TABLE IF EXISTS termbase_collections CASCADE;

DO $$
BEGIN
    IF to_regclass(current_schema() || '.memory_bases') IS NULL THEN
        IF to_regclass(current_schema() || '.translation_memory_collections') IS NOT NULL THEN
            EXECUTE 'ALTER TABLE translation_memory_collections RENAME TO memory_bases';
        ELSIF to_regclass(current_schema() || '.tm_collections') IS NOT NULL THEN
            EXECUTE 'ALTER TABLE tm_collections RENAME TO memory_bases';
        END IF;
    END IF;

    IF to_regclass(current_schema() || '.memory_entries') IS NULL THEN
        IF to_regclass(current_schema() || '.translation_memory_entries') IS NOT NULL THEN
            EXECUTE 'ALTER TABLE translation_memory_entries RENAME TO memory_entries';
        ELSIF to_regclass(current_schema() || '.translation_memory') IS NOT NULL THEN
            EXECUTE 'ALTER TABLE translation_memory RENAME TO memory_entries';
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF to_regclass(current_schema() || '.memory_bases') IS NULL THEN
        RAISE EXCEPTION 'memory_bases not found; run scripts/init_db.sql first';
    END IF;
    IF to_regclass(current_schema() || '.memory_entries') IS NULL THEN
        RAISE EXCEPTION 'memory_entries not found; run scripts/init_db.sql first';
    END IF;
END $$;

DO $$
BEGIN
    PERFORM _rename_constraint_if_exists('memory_bases'::regclass, 'tm_collections_pkey', 'memory_bases_pkey');
    PERFORM _rename_constraint_if_exists(
        'memory_bases'::regclass,
        'translation_memory_collections_pkey',
        'memory_bases_pkey'
    );
    PERFORM _rename_constraint_if_exists('memory_entries'::regclass, 'translation_memory_pkey', 'memory_entries_pkey');
    PERFORM _rename_constraint_if_exists(
        'memory_entries'::regclass,
        'translation_memory_entries_pkey',
        'memory_entries_pkey'
    );

    PERFORM _rename_index_if_exists('uq_tm_collections_name', 'uq_memory_bases_name');
    PERFORM _rename_index_if_exists('uq_translation_memory_collections_name', 'uq_memory_bases_name');
    PERFORM _rename_index_if_exists('ix_tm_collections_language_pair', 'ix_memory_bases_language_pair');
    PERFORM _rename_index_if_exists('ix_translation_memory_collections_language_pair', 'ix_memory_bases_language_pair');

    PERFORM _rename_index_if_exists('ix_translation_memory_collection_id', 'ix_memory_entries_collection_id');
    PERFORM _rename_index_if_exists('ix_translation_memory_entries_collection_id', 'ix_memory_entries_collection_id');
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_collection_source_hash',
        'ix_memory_entries_collection_source_hash'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_collection_source_hash',
        'ix_memory_entries_collection_source_hash'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_collection_source_normalized',
        'ix_memory_entries_collection_source_normalized'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_collection_source_normalized',
        'ix_memory_entries_collection_source_normalized'
    );
    PERFORM _rename_index_if_exists('ix_translation_memory_source_hash', 'ix_memory_entries_source_hash');
    PERFORM _rename_index_if_exists('ix_translation_memory_entries_source_hash', 'ix_memory_entries_source_hash');
    PERFORM _rename_index_if_exists('ix_translation_memory_source_text', 'ix_memory_entries_source_text');
    PERFORM _rename_index_if_exists('ix_translation_memory_entries_source_text', 'ix_memory_entries_source_text');
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_source_normalized',
        'ix_memory_entries_source_normalized'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_source_normalized',
        'ix_memory_entries_source_normalized'
    );
    PERFORM _rename_index_if_exists('ix_translation_memory_language_pair', 'ix_memory_entries_language_pair');
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_language_pair',
        'ix_memory_entries_language_pair'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_collection_language_pair',
        'ix_memory_entries_collection_language_pair'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_collection_language_pair',
        'ix_memory_entries_collection_language_pair'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_source_text_trgm',
        'ix_memory_entries_source_text_trgm'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_source_text_trgm',
        'ix_memory_entries_source_text_trgm'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_source_normalized_trgm',
        'ix_memory_entries_source_normalized_trgm'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_source_normalized_trgm',
        'ix_memory_entries_source_normalized_trgm'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_source_normalized_gist_trgm',
        'ix_memory_entries_source_normalized_gist_trgm'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_source_normalized_gist_trgm',
        'ix_memory_entries_source_normalized_gist_trgm'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_source_embedding_version',
        'ix_memory_entries_source_embedding_version'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_source_embedding_version',
        'ix_memory_entries_source_embedding_version'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_source_embedding_ivfflat',
        'ix_memory_entries_source_embedding_ivfflat'
    );
    PERFORM _rename_index_if_exists(
        'ix_translation_memory_entries_source_embedding_ivfflat',
        'ix_memory_entries_source_embedding_ivfflat'
    );
END $$;

ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);

ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);

CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_bases_name
    ON memory_bases (name);
CREATE INDEX IF NOT EXISTS ix_memory_bases_language_pair
    ON memory_bases (source_language, target_language);

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

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_extension
        WHERE extname = 'pg_trgm'
    ) THEN
        EXECUTE '
            CREATE INDEX IF NOT EXISTS ix_memory_entries_source_text_trgm
            ON memory_entries
            USING GIN (source_text gin_trgm_ops)
        ';
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'memory_entries'
              AND column_name = 'source_normalized'
        ) THEN
            EXECUTE '
                CREATE INDEX IF NOT EXISTS ix_memory_entries_source_normalized_trgm
                ON memory_entries
                USING GIN (source_normalized gin_trgm_ops)
            ';
        END IF;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'memory_entries'
          AND column_name = 'source_embedding_version'
    ) THEN
        EXECUTE '
            CREATE INDEX IF NOT EXISTS ix_memory_entries_source_embedding_version
            ON memory_entries (source_embedding_version)
        ';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_extension
        WHERE extname = 'vector'
    )
    AND EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'memory_entries'
          AND column_name = 'source_embedding'
    ) THEN
        EXECUTE '
            CREATE INDEX IF NOT EXISTS ix_memory_entries_source_embedding_ivfflat
            ON memory_entries
            USING ivfflat (source_embedding vector_cosine_ops)
            WITH (lists = 100)
        ';
    END IF;
END $$;

DROP TRIGGER IF EXISTS update_tm_collections_updated_at ON memory_bases;
DROP TRIGGER IF EXISTS update_translation_memory_collections_updated_at ON memory_bases;
DROP TRIGGER IF EXISTS update_memory_bases_updated_at ON memory_bases;
CREATE TRIGGER update_memory_bases_updated_at
    BEFORE UPDATE ON memory_bases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_translation_memory_updated_at ON memory_entries;
DROP TRIGGER IF EXISTS update_translation_memory_entries_updated_at ON memory_entries;
DROP TRIGGER IF EXISTS update_memory_entries_updated_at ON memory_entries;
CREATE TRIGGER update_memory_entries_updated_at
    BEFORE UPDATE ON memory_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

UPDATE memory_entries AS tm
SET source_language = COALESCE(tm.source_language, collection.source_language),
    target_language = COALESCE(tm.target_language, collection.target_language)
FROM memory_bases AS collection
WHERE tm.collection_id = collection.id
  AND (
      tm.source_language IS DISTINCT FROM collection.source_language
      OR tm.target_language IS DISTINCT FROM collection.target_language
  );

DROP FUNCTION IF EXISTS _rename_index_if_exists(text, text);
DROP FUNCTION IF EXISTS _rename_constraint_if_exists(regclass, text, text);

COMMIT;
