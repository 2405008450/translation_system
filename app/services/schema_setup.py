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
    "users": {"nickname"},
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
            """
            ALTER TABLE IF EXISTS users
            ADD COLUMN IF NOT EXISTS nickname VARCHAR(50)
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
        ]
    )
    return statements


