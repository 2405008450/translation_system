"""修订同步增量结构，与运行时补齐和部署脚本共用。"""
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask


def schema_statements() -> list[str]:
    dialect = postgresql.dialect()
    result = ["ALTER TABLE projects ADD COLUMN IF NOT EXISTS review_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE"]
    for model in (ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask):
        result.append(str(CreateTable(model.__table__, if_not_exists=True).compile(dialect=dialect)))
        result.extend(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect))
                      for index in sorted(model.__table__.indexes, key=lambda item: item.name))
    return result
