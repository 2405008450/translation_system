from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError

from app.database import engine


UUID_SQL_DEFAULT = """
(
    lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
    lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
    '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
    substr('89ab', floor(random() * 4)::int + 1, 1) ||
    substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
    lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
)::uuid
"""

REQUIRED_EXISTING_TABLES = ("memory_bases", "memory_entries")
LEGACY_REQUIRED_EXISTING_TABLES = (
    "tm_collections",
    "translation_memory",
    "translation_memory_collections",
    "translation_memory_entries",
)
REQUIRED_SCHEMA = {
    "memory_bases": {"source_language", "target_language"},
    "memory_entries": {"source_language", "target_language"},
    "term_bases": {
        "id",
        "name",
        "source_language",
        "target_language",
    },
    "term_entries": {
        "id",
        "term_base_id",
        "source_text",
        "target_text",
        "source_language",
        "target_language",
    },
    "glossary_bases": {
        "id",
        "name",
        "source_language",
        "target_language",
    },
    "glossary_entries": {
        "id",
        "glossary_base_id",
        "source_text",
        "target_text",
        "note",
        "source_language",
        "target_language",
    },
    "users": {"nickname", "translator_type"},
    "projects": {
        "id",
        "name",
        "status",
        "document_parse_mode",
        "source_language",
        "target_language",
        "creator_id",
        "deadline",
        "access_level",
        "translation_guidelines",
        "created_at",
        "updated_at",
    },
    "file_records": {
        "project_id",
        "document_parse_mode",
        "document_parse_options",
        "document_statistics",
        "active_operation",
        "active_operation_token",
        "active_operation_updated_at",
        "active_operation_user_id",
        "assignee_id",
        "assigned_by_id",
        "assigned_at",
        "source_language",
        "target_language",
        "creator_id",
        "collection_id",
        "collection_ids_json",
        "term_base_id",
        "term_base_ids",
        "term_base_write_ids",
        "qa_term_base_ids",
        "glossary_base_ids",
        "deadline",
        "access_level",
    },
    "document_statistics_reports": {
        "id",
        "project_id",
        "created_by_id",
        "file_ids",
        "total_files",
        "available_files",
        "totals",
        "status",
        "created_at",
    },
    "document_statistics_report_items": {
        "id",
        "report_id",
        "project_id",
        "file_record_id",
        "file_name",
        "source_language",
        "target_language",
        "file_size_bytes",
        "statistics",
        "created_at",
    },
    "term_qa_reports": {
        "id",
        "project_id",
        "file_record_id",
        "created_by_id",
        "scope",
        "file_ids",
        "term_base_ids",
        "language_pairs",
        "total_files",
        "total_segments",
        "checked_segments",
        "issue_count",
        "status",
        "created_at",
    },
    "term_qa_report_items": {
        "id",
        "report_id",
        "project_id",
        "file_record_id",
        "segment_id",
        "term_base_id",
        "sentence_id",
        "file_name",
        "term_base_name",
        "source_term",
        "expected_target_term",
        "source_text",
        "target_text",
        "block_index",
        "row_index",
        "cell_index",
        "ignored_by_id",
        "ignored_at",
        "created_at",
    },
    "project_assignments": {
        "id",
        "project_id",
        "assignee_id",
        "assigned_by_id",
        "assigned_at",
        "revoked_by_id",
        "revoked_at",
        "status",
    },
    "file_assignments": {
        "id",
        "project_id",
        "file_record_id",
        "assignee_id",
        "assigned_by_id",
        "assigned_at",
        "revoked_by_id",
        "revoked_at",
        "status",
    },
    "assignment_events": {
        "id",
        "project_id",
        "file_record_id",
        "assignee_id",
        "actor_id",
        "action",
        "before_payload",
        "after_payload",
        "created_at",
    },
    "notifications": {
        "id",
        "user_id",
        "type",
        "title",
        "body",
        "project_id",
        "file_record_id",
        "related_event_id",
        "read_at",
        "created_at",
    },
    "segments": {
        "source_word_count",
        "llm_provider",
        "llm_model",
        "version",
        "source_html",
        "target_html",
    },
    "translation_metric_events": {
        "id",
        "event_key",
        "project_id",
        "file_record_id",
        "segment_id",
        "user_id",
        "source",
        "source_language",
        "target_language",
        "source_word_count",
        "target_was_empty",
        "created_at",
    },
    "user_activity_daily": {
        "id",
        "user_id",
        "activity_date",
        "request_count",
        "first_seen_at",
        "last_seen_at",
    },
    "segment_revisions": {
        "id",
        "file_record_id",
        "segment_id",
        "sentence_id",
        "before_text",
        "after_text",
        "source",
        "status",
        "author_id",
        "resolved_by_id",
        "created_at",
        "resolved_at",
    },
    "issue_markers": {
        "id",
        "project_id",
        "file_record_id",
        "title",
        "description",
        "category",
        "severity",
        "status",
        "page_url",
        "user_agent",
        "reporter_id",
        "resolved_by_id",
        "created_at",
        "updated_at",
        "resolved_at",
    },
    "auto_tm_outbox": {
        "id",
        "file_record_id",
        "segment_id",
        "sentence_id",
        "collection_id",
        "source_text",
        "target_text",
        "source_language",
        "target_language",
        "creator_id",
        "status",
        "attempt_count",
        "error_message",
        "last_enqueued_at",
        "processed_at",
        "created_at",
        "updated_at",
    },
    "auto_tm_rematch_queue": {
        "id",
        "file_record_id",
        "collection_id",
        "pending_entry_count",
        "status",
        "first_pending_at",
        "last_pending_at",
        "last_processed_at",
        "error_message",
        "created_at",
        "updated_at",
    },
}

REQUIRED_INDEXES = {
    "segments": {
        "ix_segments_source_word_count",
        "ix_segments_translated_source_word_count",
        "ix_segments_source_word_backfill",
        "ix_segments_translated_backfill",
    },
    "translation_metric_events": {
        "ix_translation_metric_events_source_created_at",
    },
    "memory_entries": {
        "uq_memory_entries_collection_source_hash_language_pair",
    },
    "auto_tm_outbox": {
        "uq_auto_tm_outbox_file_segment_collection",
        "ix_auto_tm_outbox_status_created_at",
    },
    "auto_tm_rematch_queue": {
        "uq_auto_tm_rematch_queue_file_record",
        "ix_auto_tm_rematch_queue_status",
    },
    "document_statistics_reports": {
        "ix_document_statistics_reports_project_id",
        "ix_document_statistics_reports_created_by_id",
        "ix_document_statistics_reports_created_at",
    },
    "document_statistics_report_items": {
        "ix_document_statistics_report_items_report_id",
        "ix_document_statistics_report_items_project_id",
        "ix_document_statistics_report_items_file_record_id",
    },
}


def ensure_runtime_schema() -> None:
    with engine.connect() as connection:
        inspector = inspect(connection)
        missing_existing_tables = [
            table_name
            for table_name in REQUIRED_EXISTING_TABLES
            if not inspector.has_table(table_name)
        ]
        if missing_existing_tables:
            missing_text = ", ".join(missing_existing_tables)
            legacy_tables = [
                table_name
                for table_name in LEGACY_REQUIRED_EXISTING_TABLES
                if inspector.has_table(table_name)
            ]
            if legacy_tables:
                legacy_text = ", ".join(legacy_tables)
                raise RuntimeError(
                    "检测到旧版记忆库表名，请先执行 scripts/rename_translation_memory_tables.sql "
                    "完成表重命名，再启动服务。"
                    f" 旧表: {legacy_text}; 缺失新表: {missing_text}"
                )
            raise RuntimeError(
                "数据库缺少基础业务表，请先执行 scripts/init_db.sql 完成初始化。"
                f" 缺失表: {missing_text}"
            )

        missing_items = _collect_missing_schema(inspector)
        if not missing_items:
            return

        statements = _build_schema_statements(
            create_update_function=not _update_trigger_function_exists(connection),
        )

    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    except ProgrammingError as exc:
        missing_text = ", ".join(missing_items)
        raise RuntimeError(
            "数据库账号缺少结构升级权限，请使用有权限的账号执行 scripts/init_db.sql "
            "或 scripts/rename_translation_memory_tables.sql 后再启动服务。"
            f" 当前缺失项: {missing_text}"
        ) from exc


def _collect_missing_schema(inspector) -> list[str]:
    missing_items: list[str] = []
    for table_name, required_columns in REQUIRED_SCHEMA.items():
        if not inspector.has_table(table_name):
            missing_items.append(table_name)
            continue

        existing_columns = {
            column["name"]
            for column in inspector.get_columns(table_name)
        }
        for column_name in sorted(required_columns - existing_columns):
            missing_items.append(f"{table_name}.{column_name}")

    for table_name, required_indexes in REQUIRED_INDEXES.items():
        if not inspector.has_table(table_name):
            continue
        existing_indexes = {
            index["name"]
            for index in inspector.get_indexes(table_name)
        }
        for index_name in sorted(required_indexes - existing_indexes):
            missing_items.append(f"{table_name}.{index_name}")
    return missing_items


def _update_trigger_function_exists(connection) -> bool:
    result = connection.execute(
        text(
            """
            SELECT 1
            FROM pg_proc
            WHERE proname = 'update_updated_at_column'
            LIMIT 1
            """
        )
    ).scalar()
    return bool(result)


def _build_schema_statements(*, create_update_function: bool) -> list[str]:
    statements: list[str] = []
    if create_update_function:
        statements.append(
            """
            CREATE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )

    statements.extend(
        [
            """
            ALTER TABLE IF EXISTS memory_bases
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS memory_bases
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_memory_bases_language_pair
            ON memory_bases (source_language, target_language)
            """,
            """
            ALTER TABLE IF EXISTS memory_entries
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS memory_entries
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_memory_entries_language_pair
            ON memory_entries (source_language, target_language)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_memory_entries_collection_language_pair
            ON memory_entries (collection_id, source_language, target_language)
            """,
            """
            UPDATE memory_entries AS tm
            SET source_language = COALESCE(tm.source_language, collection.source_language),
                target_language = COALESCE(tm.target_language, collection.target_language)
            FROM memory_bases AS collection
            WHERE tm.collection_id = collection.id
              AND (
                  tm.source_language IS DISTINCT FROM collection.source_language
                  OR tm.target_language IS DISTINCT FROM collection.target_language
              )
            """,
            """
            WITH ranked_entries AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY collection_id, source_hash, source_language, target_language
                        ORDER BY updated_at DESC, created_at DESC, id DESC
                    ) AS row_number
                FROM memory_entries
                WHERE collection_id IS NOT NULL
                  AND source_hash IS NOT NULL
                  AND source_language IS NOT NULL
                  AND target_language IS NOT NULL
            )
            DELETE FROM memory_entries
            WHERE id IN (
                SELECT id
                FROM ranked_entries
                WHERE row_number > 1
            )
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_entries_collection_source_hash_language_pair
            ON memory_entries (collection_id, source_hash, source_language, target_language)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS term_bases (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                name VARCHAR(120) NOT NULL,
                description TEXT,
                source_language VARCHAR(20) NOT NULL,
                target_language VARCHAR(20) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS term_bases
            ADD COLUMN IF NOT EXISTS description TEXT
            """,
            """
            ALTER TABLE IF EXISTS term_bases
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS term_bases
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS term_bases
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS term_bases
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_term_bases_name
            ON term_bases (name)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_bases_language_pair
            ON term_bases (source_language, target_language)
            """,
            """
            DROP TRIGGER IF EXISTS update_term_bases_updated_at ON term_bases
            """,
            """
            CREATE TRIGGER update_term_bases_updated_at
            BEFORE UPDATE ON term_bases
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
            """,
            f"""
            CREATE TABLE IF NOT EXISTS term_entries (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                term_base_id UUID NOT NULL REFERENCES term_bases(id) ON DELETE CASCADE,
                source_text TEXT NOT NULL,
                target_text TEXT NOT NULL,
                source_normalized TEXT,
                source_language VARCHAR(20) NOT NULL,
                target_language VARCHAR(20) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS term_entries
            ADD COLUMN IF NOT EXISTS source_normalized TEXT
            """,
            """
            ALTER TABLE IF EXISTS term_entries
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS term_entries
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS term_entries
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS term_entries
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_id
            ON term_entries (term_base_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_source_text
            ON term_entries (term_base_id, source_text)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_entries_term_base_source_normalized
            ON term_entries (term_base_id, source_normalized)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_entries_language_pair
            ON term_entries (source_language, target_language)
            """,
            """
            DROP TRIGGER IF EXISTS update_term_entries_updated_at ON term_entries
            """,
            """
            CREATE TRIGGER update_term_entries_updated_at
            BEFORE UPDATE ON term_entries
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
            """,
            f"""
            CREATE TABLE IF NOT EXISTS glossary_bases (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                name VARCHAR(120) NOT NULL,
                description TEXT,
                source_language VARCHAR(20) NOT NULL,
                target_language VARCHAR(20) NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS glossary_bases
            ADD COLUMN IF NOT EXISTS description TEXT
            """,
            """
            ALTER TABLE IF EXISTS glossary_bases
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS glossary_bases
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS glossary_bases
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS glossary_bases
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_glossary_bases_name
            ON glossary_bases (name)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_glossary_bases_language_pair
            ON glossary_bases (source_language, target_language)
            """,
            """
            DROP TRIGGER IF EXISTS update_glossary_bases_updated_at ON glossary_bases
            """,
            """
            CREATE TRIGGER update_glossary_bases_updated_at
            BEFORE UPDATE ON glossary_bases
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
            """,
            f"""
            CREATE TABLE IF NOT EXISTS glossary_entries (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                glossary_base_id UUID NOT NULL REFERENCES glossary_bases(id) ON DELETE CASCADE,
                source_text TEXT NOT NULL,
                target_text TEXT NOT NULL,
                note TEXT,
                source_normalized TEXT,
                source_language VARCHAR(20) NOT NULL,
                target_language VARCHAR(20) NOT NULL,
                creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS glossary_entries
            ADD COLUMN IF NOT EXISTS note TEXT
            """,
            """
            ALTER TABLE IF EXISTS glossary_entries
            ADD COLUMN IF NOT EXISTS source_normalized TEXT
            """,
            """
            ALTER TABLE IF EXISTS glossary_entries
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS glossary_entries
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS glossary_entries
            ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS glossary_entries
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS glossary_entries
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_glossary_entries_creator_id
            ON glossary_entries (creator_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_glossary_entries_glossary_base_id
            ON glossary_entries (glossary_base_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_glossary_entries_base_source_text
            ON glossary_entries (glossary_base_id, source_text)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_glossary_entries_base_source_normalized
            ON glossary_entries (glossary_base_id, source_normalized)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_glossary_entries_language_pair
            ON glossary_entries (source_language, target_language)
            """,
            """
            DROP TRIGGER IF EXISTS update_glossary_entries_updated_at ON glossary_entries
            """,
            """
            CREATE TRIGGER update_glossary_entries_updated_at
            BEFORE UPDATE ON glossary_entries
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
            """,
            """
            ALTER TABLE IF EXISTS users
            ADD COLUMN IF NOT EXISTS nickname VARCHAR(50)
            """,
            """
            ALTER TABLE IF EXISTS users
            ADD COLUMN IF NOT EXISTS translator_type VARCHAR(20) NOT NULL DEFAULT 'internal'
            """,
            """
            UPDATE users
            SET translator_type = 'internal'
            WHERE translator_type IS NULL
               OR translator_type NOT IN ('internal', 'external')
            """,
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'users'
                ) THEN
                    UPDATE users
                    SET nickname = username
                    WHERE nickname IS NULL OR btrim(nickname) = '';
                END IF;
            END
            $$;
            """,
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'users'
                ) THEN
                    UPDATE users
                    SET role = 'super_admin'
                    WHERE id = (
                        SELECT id
                        FROM users
                        WHERE role = 'admin'
                        ORDER BY created_at ASC NULLS LAST, username ASC
                        LIMIT 1
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM users WHERE role = 'super_admin'
                    );
                END IF;
            END
            $$;
            """,
            f"""
            CREATE TABLE IF NOT EXISTS projects (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                name VARCHAR(200) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'draft',
                document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full',
                source_language VARCHAR(20),
                target_language VARCHAR(20),
                creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
                deadline TIMESTAMP,
                access_level VARCHAR(20) NOT NULL DEFAULT 'team',
                translation_guidelines TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full'
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS deadline TIMESTAMP
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS access_level VARCHAR(20) NOT NULL DEFAULT 'team'
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS translation_guidelines TEXT NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS projects
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_projects_creator_id
            ON projects (creator_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_projects_status
            ON projects (status)
            """,
            """
            DROP TRIGGER IF EXISTS update_projects_updated_at ON projects
            """,
            """
            CREATE TRIGGER update_projects_updated_at
            BEFORE UPDATE ON projects
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full'
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS document_parse_options TEXT NOT NULL DEFAULT '{}'
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS document_statistics TEXT NOT NULL DEFAULT '{}'
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS active_operation VARCHAR(40)
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS active_operation_token VARCHAR(64)
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS active_operation_updated_at TIMESTAMP
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS active_operation_user_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS assignee_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS collection_id UUID REFERENCES memory_bases(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS collection_ids_json TEXT NOT NULL DEFAULT '[]'
            """,
            """
            UPDATE file_records
            SET collection_ids_json = json_build_array(collection_id)::text
            WHERE collection_id IS NOT NULL
              AND (collection_ids_json IS NULL OR collection_ids_json = '[]')
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS term_base_id UUID REFERENCES term_bases(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS term_base_ids TEXT NOT NULL DEFAULT '[]'
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS term_base_write_ids TEXT NOT NULL DEFAULT '[]'
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS qa_term_base_ids TEXT NOT NULL DEFAULT '[]'
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS glossary_base_ids TEXT NOT NULL DEFAULT '[]'
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS deadline TIMESTAMP
            """,
            """
            ALTER TABLE IF EXISTS file_records
            ADD COLUMN IF NOT EXISTS access_level VARCHAR(20) NOT NULL DEFAULT 'team'
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_records_project_id
            ON file_records (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_records_creator_id
            ON file_records (creator_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_records_status
            ON file_records (status)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_records_active_operation
            ON file_records (active_operation)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_records_assignee_id
            ON file_records (assignee_id)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS document_statistics_reports (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
                file_ids TEXT NOT NULL DEFAULT '[]',
                total_files INTEGER NOT NULL DEFAULT 0,
                available_files INTEGER NOT NULL DEFAULT 0,
                totals TEXT NOT NULL DEFAULT '{{}}',
                status VARCHAR(20) NOT NULL DEFAULT 'completed',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS document_statistics_report_items (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                report_id UUID NOT NULL REFERENCES document_statistics_reports(id) ON DELETE CASCADE,
                project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL,
                file_name VARCHAR(255) NOT NULL,
                source_language VARCHAR(20),
                target_language VARCHAR(20),
                file_size_bytes INTEGER,
                statistics TEXT NOT NULL DEFAULT '{{}}',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_project_id
            ON document_statistics_reports (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_created_by_id
            ON document_statistics_reports (created_by_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_created_at
            ON document_statistics_reports (created_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_report_id
            ON document_statistics_report_items (report_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_project_id
            ON document_statistics_report_items (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_file_record_id
            ON document_statistics_report_items (file_record_id)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS project_assignments (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
                assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
                revoked_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
                revoked_at TIMESTAMP,
                status VARCHAR(20) NOT NULL DEFAULT 'active'
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS file_assignments (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
                assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
                assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
                revoked_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
                revoked_at TIMESTAMP,
                status VARCHAR(20) NOT NULL DEFAULT 'active'
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS assignment_events (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
                assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
                action VARCHAR(40) NOT NULL,
                before_payload TEXT NOT NULL DEFAULT '{{}}',
                after_payload TEXT NOT NULL DEFAULT '{{}}',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS notifications (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                type VARCHAR(40) NOT NULL,
                title VARCHAR(200) NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
                file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
                related_event_id UUID REFERENCES assignment_events(id) ON DELETE SET NULL,
                read_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_project_assignments_project_id
            ON project_assignments (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_project_assignments_assignee_id
            ON project_assignments (assignee_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_project_assignments_status
            ON project_assignments (status)
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_project_assignments_active_project_user
            ON project_assignments (project_id, assignee_id)
            WHERE status = 'active'
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_assignments_project_id
            ON file_assignments (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_assignments_file_record_id
            ON file_assignments (file_record_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_assignments_assignee_id
            ON file_assignments (assignee_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_assignments_status
            ON file_assignments (status)
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_file_assignments_active_file_user
            ON file_assignments (file_record_id, assignee_id)
            WHERE status = 'active'
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_assignment_events_project_id
            ON assignment_events (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_assignment_events_file_record_id
            ON assignment_events (file_record_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_assignment_events_assignee_id
            ON assignment_events (assignee_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_assignment_events_actor_id
            ON assignment_events (actor_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_assignment_events_created_at
            ON assignment_events (created_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_notifications_user_id
            ON notifications (user_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_notifications_read_at
            ON notifications (read_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_notifications_created_at
            ON notifications (created_at)
            """,
            """
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
              )
            """,
            """
            INSERT INTO file_assignments (
                project_id,
                file_record_id,
                assignee_id,
                assigned_by_id,
                assigned_at,
                status
            )
            SELECT
                fr.project_id,
                fr.id,
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
              )
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_file_records_source_language
            ON file_records (source_language)
            """,
            """
            ALTER TABLE IF EXISTS segments
            ADD COLUMN IF NOT EXISTS source_word_count INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS segments
            ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1
            """,
            """
            ALTER TABLE IF EXISTS segments
            ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(40)
            """,
            """
            ALTER TABLE IF EXISTS segments
            ADD COLUMN IF NOT EXISTS llm_model VARCHAR(200)
            """,
            """
            ALTER TABLE IF EXISTS segments
            ADD COLUMN IF NOT EXISTS source_html TEXT
            """,
            """
            ALTER TABLE IF EXISTS segments
            ADD COLUMN IF NOT EXISTS target_html TEXT
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segments_file_record_order
            ON segments (file_record_id, block_index, row_index, cell_index, sentence_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segments_source_word_count
            ON segments (source_word_count)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segments_translated_source_word_count
            ON segments (file_record_id, source_word_count)
            WHERE target_text <> '' AND source_word_count > 0
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segments_source_word_backfill
            ON segments (id)
            WHERE source_word_count = 0 AND source_text <> ''
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segments_translated_backfill
            ON segments (updated_at, id)
            WHERE target_text <> '' AND source_word_count > 0
            """,
            f"""
            CREATE TABLE IF NOT EXISTS translation_metric_events (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                event_key VARCHAR(140),
                project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
                file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL,
                segment_id UUID REFERENCES segments(id) ON DELETE SET NULL,
                user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                source VARCHAR(20) NOT NULL DEFAULT 'manual',
                source_language VARCHAR(20),
                target_language VARCHAR(20),
                source_word_count INTEGER NOT NULL DEFAULT 0,
                target_was_empty BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS event_key VARCHAR(140)
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS segment_id UUID REFERENCES segments(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'manual'
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS source_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS target_language VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS source_word_count INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS target_was_empty BOOLEAN NOT NULL DEFAULT TRUE
            """,
            """
            ALTER TABLE IF EXISTS translation_metric_events
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_translation_metric_events_event_key
            ON translation_metric_events (event_key)
            WHERE event_key IS NOT NULL
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_translation_metric_events_created_at
            ON translation_metric_events (created_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_translation_metric_events_source
            ON translation_metric_events (source)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_translation_metric_events_language_pair
            ON translation_metric_events (source_language, target_language)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_translation_metric_events_file_record_id
            ON translation_metric_events (file_record_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_translation_metric_events_segment_id
            ON translation_metric_events (segment_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_translation_metric_events_source_created_at
            ON translation_metric_events (source, created_at)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS user_activity_daily (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                activity_date DATE NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
                last_seen_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS user_activity_daily
            ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE
            """,
            """
            ALTER TABLE IF EXISTS user_activity_daily
            ADD COLUMN IF NOT EXISTS activity_date DATE
            """,
            """
            ALTER TABLE IF EXISTS user_activity_daily
            ADD COLUMN IF NOT EXISTS request_count INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS user_activity_daily
            ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS user_activity_daily
            ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP DEFAULT NOW()
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_user_activity_daily_user_date
            ON user_activity_daily (user_id, activity_date)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_user_activity_daily_activity_date
            ON user_activity_daily (activity_date)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_user_activity_daily_user_id
            ON user_activity_daily (user_id)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS issue_markers (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
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
            )
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS title VARCHAR(160) NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS description TEXT
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS category VARCHAR(30) NOT NULL DEFAULT 'other'
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS severity VARCHAR(20) NOT NULL DEFAULT 'medium'
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'open'
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS page_url TEXT
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS user_agent TEXT
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS reporter_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS resolved_by_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS issue_markers
            ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_issue_markers_project_id
            ON issue_markers (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_issue_markers_file_record_id
            ON issue_markers (file_record_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_issue_markers_status
            ON issue_markers (status)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_issue_markers_reporter_id
            ON issue_markers (reporter_id)
            """,
            """
            DROP TRIGGER IF EXISTS update_issue_markers_updated_at ON issue_markers
            """,
            """
            CREATE TRIGGER update_issue_markers_updated_at
            BEFORE UPDATE ON issue_markers
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column()
            """,
            f"""
            CREATE TABLE IF NOT EXISTS term_qa_reports (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
                file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
                created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
                scope VARCHAR(20) NOT NULL DEFAULT 'project',
                file_ids TEXT NOT NULL DEFAULT '[]',
                term_base_ids TEXT NOT NULL DEFAULT '[]',
                language_pairs TEXT NOT NULL DEFAULT '[]',
                total_files INTEGER NOT NULL DEFAULT 0,
                total_segments INTEGER NOT NULL DEFAULT 0,
                checked_segments INTEGER NOT NULL DEFAULT 0,
                issue_count INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'completed',
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS created_by_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS scope VARCHAR(20) NOT NULL DEFAULT 'project'
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS file_ids TEXT NOT NULL DEFAULT '[]'
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS term_base_ids TEXT NOT NULL DEFAULT '[]'
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS language_pairs TEXT NOT NULL DEFAULT '[]'
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS total_files INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS total_segments INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS checked_segments INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS issue_count INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'completed'
            """,
            """
            ALTER TABLE IF EXISTS term_qa_reports
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW()
            """,
            f"""
            CREATE TABLE IF NOT EXISTS term_qa_report_items (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                report_id UUID NOT NULL REFERENCES term_qa_reports(id) ON DELETE CASCADE,
                project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
                file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
                segment_id UUID REFERENCES segments(id) ON DELETE SET NULL,
                term_base_id UUID REFERENCES term_bases(id) ON DELETE SET NULL,
                sentence_id VARCHAR(40) NOT NULL DEFAULT '',
                file_name VARCHAR(255) NOT NULL DEFAULT '',
                term_base_name VARCHAR(200) NOT NULL DEFAULT '',
                source_term TEXT NOT NULL,
                expected_target_term TEXT NOT NULL,
                source_text TEXT NOT NULL,
                target_text TEXT NOT NULL DEFAULT '',
                block_index INTEGER NOT NULL DEFAULT 0,
                row_index INTEGER,
                cell_index INTEGER,
                ignored_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
                ignored_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS report_id UUID REFERENCES term_qa_reports(id) ON DELETE CASCADE
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS segment_id UUID REFERENCES segments(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS term_base_id UUID REFERENCES term_bases(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS sentence_id VARCHAR(40) NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS file_name VARCHAR(255) NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS term_base_name VARCHAR(200) NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS source_term TEXT
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS expected_target_term TEXT
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS source_text TEXT
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS target_text TEXT NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS block_index INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS row_index INTEGER
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS cell_index INTEGER
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS ignored_by_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS ignored_at TIMESTAMP
            """,
            """
            ALTER TABLE IF EXISTS term_qa_report_items
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW()
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_reports_project_id
            ON term_qa_reports (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_reports_file_record_id
            ON term_qa_reports (file_record_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_reports_created_by_id
            ON term_qa_reports (created_by_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_reports_created_at
            ON term_qa_reports (created_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_report_id
            ON term_qa_report_items (report_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_project_id
            ON term_qa_report_items (project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_file_record_id
            ON term_qa_report_items (file_record_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_segment_id
            ON term_qa_report_items (segment_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_term_base_id
            ON term_qa_report_items (term_base_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_ignored_at
            ON term_qa_report_items (ignored_at)
            """,
            """
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
            ON CONFLICT (id) DO NOTHING
            """,
            """
            UPDATE file_records AS fr
            SET project_id = fr.id
            WHERE fr.project_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM projects AS p
                  WHERE p.id = fr.id
              )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS segment_revisions (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
                segment_id UUID NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
                sentence_id VARCHAR(20) NOT NULL,
                before_text TEXT NOT NULL DEFAULT '',
                after_text TEXT NOT NULL DEFAULT '',
                source VARCHAR(20) NOT NULL DEFAULT 'manual',
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                author_id UUID REFERENCES users(id) ON DELETE SET NULL,
                resolved_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMP
            )
            """,
            """
            ALTER TABLE IF EXISTS segment_revisions
            ADD COLUMN IF NOT EXISTS before_text TEXT
            """,
            """
            ALTER TABLE IF EXISTS segment_revisions
            ADD COLUMN IF NOT EXISTS after_text TEXT
            """,
            """
            ALTER TABLE IF EXISTS segment_revisions
            ADD COLUMN IF NOT EXISTS source VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS segment_revisions
            ADD COLUMN IF NOT EXISTS status VARCHAR(20)
            """,
            """
            ALTER TABLE IF EXISTS segment_revisions
            ADD COLUMN IF NOT EXISTS author_id UUID
            """,
            """
            ALTER TABLE IF EXISTS segment_revisions
            ADD COLUMN IF NOT EXISTS resolved_by_id UUID
            """,
            """
            ALTER TABLE IF EXISTS segment_revisions
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS segment_revisions
            ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMP
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segment_revisions_file_record_id
            ON segment_revisions (file_record_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segment_revisions_segment_id
            ON segment_revisions (segment_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segment_revisions_sentence_id
            ON segment_revisions (sentence_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_segment_revisions_status
            ON segment_revisions (status)
            """,
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'segment_revisions'
                ) THEN
                    ALTER TABLE segment_revisions
                        ALTER COLUMN before_text SET DEFAULT '';
                    ALTER TABLE segment_revisions
                        ALTER COLUMN after_text SET DEFAULT '';
                    ALTER TABLE segment_revisions
                        ALTER COLUMN source SET DEFAULT 'manual';
                    ALTER TABLE segment_revisions
                        ALTER COLUMN status SET DEFAULT 'pending';
                    UPDATE segment_revisions
                    SET before_text = COALESCE(before_text, ''),
                        after_text = COALESCE(after_text, ''),
                        source = COALESCE(NULLIF(source, ''), 'manual'),
                        status = COALESCE(NULLIF(status, ''), 'pending')
                    WHERE before_text IS NULL
                       OR after_text IS NULL
                       OR source IS NULL
                       OR btrim(source) = ''
                       OR status IS NULL
                       OR btrim(status) = '';
                END IF;
            END
            $$;
            """,
            f"""
            CREATE TABLE IF NOT EXISTS auto_tm_outbox (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
                segment_id UUID NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
                sentence_id VARCHAR(20) NOT NULL,
                collection_id UUID NOT NULL REFERENCES memory_bases(id) ON DELETE CASCADE,
                source_text TEXT NOT NULL,
                target_text TEXT NOT NULL,
                source_language VARCHAR(20) NOT NULL,
                target_language VARCHAR(20) NOT NULL,
                creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                last_enqueued_at TIMESTAMP NOT NULL DEFAULT NOW(),
                processed_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_outbox
            ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_outbox
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'pending'
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_outbox
            ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_outbox
            ADD COLUMN IF NOT EXISTS error_message TEXT NOT NULL DEFAULT ''
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_outbox
            ADD COLUMN IF NOT EXISTS last_enqueued_at TIMESTAMP NOT NULL DEFAULT NOW()
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_outbox
            ADD COLUMN IF NOT EXISTS processed_at TIMESTAMP
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_auto_tm_outbox_file_segment_collection
            ON auto_tm_outbox (file_record_id, segment_id, collection_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_auto_tm_outbox_status_created_at
            ON auto_tm_outbox (status, created_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_auto_tm_outbox_file_record_id
            ON auto_tm_outbox (file_record_id)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS auto_tm_rematch_queue (
                id UUID PRIMARY KEY DEFAULT {UUID_SQL_DEFAULT},
                file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
                collection_id UUID NOT NULL REFERENCES memory_bases(id) ON DELETE CASCADE,
                pending_entry_count INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                first_pending_at TIMESTAMP,
                last_pending_at TIMESTAMP,
                last_processed_at TIMESTAMP,
                error_message TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_rematch_queue
            ADD COLUMN IF NOT EXISTS pending_entry_count INTEGER NOT NULL DEFAULT 0
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_rematch_queue
            ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'pending'
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_rematch_queue
            ADD COLUMN IF NOT EXISTS first_pending_at TIMESTAMP
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_rematch_queue
            ADD COLUMN IF NOT EXISTS last_pending_at TIMESTAMP
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_rematch_queue
            ADD COLUMN IF NOT EXISTS last_processed_at TIMESTAMP
            """,
            """
            ALTER TABLE IF EXISTS auto_tm_rematch_queue
            ADD COLUMN IF NOT EXISTS error_message TEXT NOT NULL DEFAULT ''
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_auto_tm_rematch_queue_file_record
            ON auto_tm_rematch_queue (file_record_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_auto_tm_rematch_queue_status
            ON auto_tm_rematch_queue (status)
            """,
            """
            CREATE INDEX IF NOT EXISTS ix_auto_tm_rematch_queue_first_pending_at
            ON auto_tm_rematch_queue (first_pending_at)
            """,
        ]
    )
    return statements


