BEGIN;

DO $$
BEGIN
    IF to_regclass(current_schema() || '.documents') IS NULL THEN
        RETURN;
    END IF;

    IF to_regclass(current_schema() || '.file_records') IS NULL THEN
        ALTER TABLE documents RENAME TO file_records;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'segments'
          AND column_name = 'document_id'
    ) THEN
        ALTER TABLE segments RENAME COLUMN document_id TO file_record_id;
    END IF;
END $$;

DO $$
BEGIN
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
END $$;

ALTER INDEX IF EXISTS documents_pkey RENAME TO file_records_pkey;
ALTER INDEX IF EXISTS ix_segments_document_id RENAME TO ix_segments_file_record_id;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'update_documents_updated_at'
    ) THEN
        ALTER TRIGGER update_documents_updated_at ON file_records RENAME TO update_file_records_updated_at;
    END IF;
END $$;

COMMIT;
