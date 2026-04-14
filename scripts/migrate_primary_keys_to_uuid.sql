BEGIN;

DO $$
DECLARE
    uuid_expr CONSTANT text := $uuid$(
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid$uuid$;
    translation_memory_id_type text;
    translation_memory_pk_name text;
BEGIN
    SELECT data_type
    INTO translation_memory_id_type
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'translation_memory'
      AND column_name = 'id';

    IF translation_memory_id_type IS NULL OR translation_memory_id_type = 'uuid' THEN
        RETURN;
    END IF;

    EXECUTE 'ALTER TABLE translation_memory ADD COLUMN IF NOT EXISTS id_uuid UUID';
    EXECUTE format(
        'UPDATE translation_memory SET id_uuid = %s WHERE id_uuid IS NULL',
        uuid_expr
    );
    EXECUTE format(
        'ALTER TABLE translation_memory ALTER COLUMN id_uuid SET DEFAULT %s',
        uuid_expr
    );
    EXECUTE 'ALTER TABLE translation_memory ALTER COLUMN id_uuid SET NOT NULL';

    SELECT conname
    INTO translation_memory_pk_name
    FROM pg_constraint
    WHERE conrelid = 'translation_memory'::regclass
      AND contype = 'p';

    IF translation_memory_pk_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE translation_memory DROP CONSTRAINT %I',
            translation_memory_pk_name
        );
    END IF;

    EXECUTE 'ALTER TABLE translation_memory DROP COLUMN id';
    EXECUTE 'ALTER TABLE translation_memory RENAME COLUMN id_uuid TO id';
    EXECUTE 'ALTER TABLE translation_memory ADD CONSTRAINT translation_memory_pkey PRIMARY KEY (id)';
END $$;

DO $$
DECLARE
    uuid_expr CONSTANT text := $uuid$(
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid$uuid$;
    parent_table text;
    parent_pk_name text;
    parent_pk_target_name text;
    parent_id_type text;
    segment_fk_column text;
    segment_fk_uuid_column text;
    segment_fk_constraint_name text;
    segment_fk_type text;
    segment_id_type text;
    segment_pk_name text;
BEGIN
    IF to_regclass(current_schema() || '.segments') IS NULL THEN
        RETURN;
    END IF;

    IF to_regclass(current_schema() || '.file_records') IS NOT NULL THEN
        parent_table := 'file_records';
        segment_fk_column := 'file_record_id';
        segment_fk_uuid_column := 'file_record_id_uuid';
        parent_pk_target_name := 'file_records_pkey';
        segment_fk_constraint_name := 'segments_file_record_id_fkey';
    ELSIF to_regclass(current_schema() || '.documents') IS NOT NULL THEN
        parent_table := 'documents';
        segment_fk_column := 'document_id';
        segment_fk_uuid_column := 'document_id_uuid';
        parent_pk_target_name := 'documents_pkey';
        segment_fk_constraint_name := 'segments_document_id_fkey';
    ELSE
        RETURN;
    END IF;

    SELECT data_type
    INTO parent_id_type
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = parent_table
      AND column_name = 'id';

    SELECT data_type
    INTO segment_id_type
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'segments'
      AND column_name = 'id';

    SELECT data_type
    INTO segment_fk_type
    FROM information_schema.columns
    WHERE table_schema = current_schema()
      AND table_name = 'segments'
      AND column_name = segment_fk_column;

    IF parent_id_type IS NULL OR segment_id_type IS NULL OR segment_fk_type IS NULL THEN
        RETURN;
    END IF;

    IF parent_id_type = 'uuid'
       AND segment_id_type = 'uuid'
       AND segment_fk_type = 'uuid' THEN
        RETURN;
    END IF;

    EXECUTE format(
        'ALTER TABLE %I ADD COLUMN IF NOT EXISTS id_uuid UUID',
        parent_table
    );
    EXECUTE 'ALTER TABLE segments ADD COLUMN IF NOT EXISTS id_uuid UUID';
    EXECUTE format(
        'ALTER TABLE segments ADD COLUMN IF NOT EXISTS %I UUID',
        segment_fk_uuid_column
    );

    EXECUTE format(
        'UPDATE %I SET id_uuid = %s WHERE id_uuid IS NULL',
        parent_table,
        uuid_expr
    );
    EXECUTE format(
        'UPDATE segments SET id_uuid = %s WHERE id_uuid IS NULL',
        uuid_expr
    );
    EXECUTE format(
        'UPDATE segments AS s
         SET %1$I = p.id_uuid
         FROM %3$I AS p
         WHERE s.%2$I = p.id
           AND s.%1$I IS NULL',
        segment_fk_uuid_column,
        segment_fk_column,
        parent_table
    );

    EXECUTE format(
        'ALTER TABLE %I ALTER COLUMN id_uuid SET DEFAULT %s',
        parent_table,
        uuid_expr
    );
    EXECUTE format(
        'ALTER TABLE %I ALTER COLUMN id_uuid SET NOT NULL',
        parent_table
    );
    EXECUTE format(
        'ALTER TABLE segments ALTER COLUMN id_uuid SET DEFAULT %s',
        uuid_expr
    );
    EXECUTE 'ALTER TABLE segments ALTER COLUMN id_uuid SET NOT NULL';
    EXECUTE format(
        'ALTER TABLE segments ALTER COLUMN %I SET NOT NULL',
        segment_fk_uuid_column
    );

    SELECT conname
    INTO segment_pk_name
    FROM pg_constraint
    WHERE conrelid = 'segments'::regclass
      AND contype = 'p';

    IF segment_pk_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE segments DROP CONSTRAINT %I',
            segment_pk_name
        );
    END IF;

    FOR parent_pk_name IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'segments'::regclass
          AND contype = 'f'
          AND confrelid = to_regclass(current_schema() || '.' || parent_table)
    LOOP
        EXECUTE format(
            'ALTER TABLE segments DROP CONSTRAINT %I',
            parent_pk_name
        );
    END LOOP;

    SELECT conname
    INTO parent_pk_name
    FROM pg_constraint
    WHERE conrelid = to_regclass(current_schema() || '.' || parent_table)
      AND contype = 'p';

    IF parent_pk_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE %I DROP CONSTRAINT %I',
            parent_table,
            parent_pk_name
        );
    END IF;

    EXECUTE 'DROP INDEX IF EXISTS ix_segments_document_id';
    EXECUTE 'DROP INDEX IF EXISTS ix_segments_file_record_id';

    EXECUTE format(
        'ALTER TABLE segments DROP COLUMN %I',
        segment_fk_column
    );
    EXECUTE 'ALTER TABLE segments DROP COLUMN id';
    EXECUTE format(
        'ALTER TABLE %I DROP COLUMN id',
        parent_table
    );

    EXECUTE format(
        'ALTER TABLE %I RENAME COLUMN id_uuid TO id',
        parent_table
    );
    EXECUTE 'ALTER TABLE segments RENAME COLUMN id_uuid TO id';
    EXECUTE format(
        'ALTER TABLE segments RENAME COLUMN %I TO %I',
        segment_fk_uuid_column,
        segment_fk_column
    );

    EXECUTE format(
        'ALTER TABLE %I ADD CONSTRAINT %I PRIMARY KEY (id)',
        parent_table,
        parent_pk_target_name
    );
    EXECUTE 'ALTER TABLE segments ADD CONSTRAINT segments_pkey PRIMARY KEY (id)';
    EXECUTE format(
        'ALTER TABLE segments
         ADD CONSTRAINT %I
         FOREIGN KEY (%I) REFERENCES %I(id) ON DELETE CASCADE',
        segment_fk_constraint_name,
        segment_fk_column,
        parent_table
    );
END $$;

DO $$
BEGIN
    IF to_regclass(current_schema() || '.documents') IS NOT NULL
       AND to_regclass(current_schema() || '.file_records') IS NULL THEN
        ALTER TABLE documents RENAME TO file_records;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'segments'
          AND column_name = 'document_id'
    ) THEN
        ALTER TABLE segments RENAME COLUMN document_id TO file_record_id;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'documents_pkey'
          AND connamespace = current_schema()::regnamespace
    ) THEN
        ALTER TABLE file_records RENAME CONSTRAINT documents_pkey TO file_records_pkey;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'segments_document_id_fkey'
          AND connamespace = current_schema()::regnamespace
    ) THEN
        ALTER TABLE segments RENAME CONSTRAINT segments_document_id_fkey TO segments_file_record_id_fkey;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'update_documents_updated_at'
    ) THEN
        ALTER TRIGGER update_documents_updated_at ON file_records RENAME TO update_file_records_updated_at;
    END IF;
END $$;

ALTER INDEX IF EXISTS documents_pkey RENAME TO file_records_pkey;
ALTER INDEX IF EXISTS ix_segments_document_id RENAME TO ix_segments_file_record_id;

CREATE INDEX IF NOT EXISTS ix_segments_file_record_id
    ON segments (file_record_id);

DROP SEQUENCE IF EXISTS translation_memory_id_seq;
DROP SEQUENCE IF EXISTS documents_id_seq;
DROP SEQUENCE IF EXISTS file_records_id_seq;
DROP SEQUENCE IF EXISTS segments_id_seq;

COMMIT;
