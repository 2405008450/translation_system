"""修订同步增量结构，与运行时补齐和部署脚本共用。"""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask


DEFAULT_ENABLED_MIGRATION = """
DO $$
BEGIN
    LOCK TABLE projects IN ACCESS EXCLUSIVE MODE;
    IF COALESCE((
        SELECT pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = 'projects'::regclass AND a.attname = 'review_sync_enabled'
    ), '') <> 'true' THEN
        ALTER TABLE projects ALTER COLUMN review_sync_enabled SET DEFAULT TRUE;
        UPDATE projects SET review_sync_enabled = TRUE WHERE review_sync_enabled = FALSE;
    END IF;
END $$
"""


def default_needs_upgrade(inspector) -> bool:
    if not inspector.has_table("projects"):
        return False
    for column in inspector.get_columns("projects"):
        if column["name"] == "review_sync_enabled":
            return str(column.get("default") or "").strip().lower() != "true"
    return False


def schema_statements() -> list[str]:
    dialect = postgresql.dialect()
    result = ["ALTER TABLE projects ADD COLUMN IF NOT EXISTS review_sync_enabled BOOLEAN NOT NULL DEFAULT TRUE",
              DEFAULT_ENABLED_MIGRATION]
    for model in (ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask):
        result.append(str(CreateTable(model.__table__, if_not_exists=True).compile(dialect=dialect)))
        result.extend(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect))
                      for index in sorted(model.__table__.indexes, key=lambda item: item.name))
    return result
