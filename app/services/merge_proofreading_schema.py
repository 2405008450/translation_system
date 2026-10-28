"""与 0031 迁移一致的幂等 DDL，供本地启动补齐结构。"""
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import postgresql


def schema_statements():
    from app.models import MergeProofreadingRun, MergeProofreadingItem
    statements = [
        "ALTER TABLE proofreading_segment_baselines ADD COLUMN IF NOT EXISTS reviewed_fingerprint VARCHAR(64)",
        "ALTER TABLE proofreading_segment_baselines ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP",
    ]
    dialect = postgresql.dialect()
    for model in (MergeProofreadingRun, MergeProofreadingItem):
        statements.append(str(CreateTable(model.__table__, if_not_exists=True).compile(dialect=dialect)))
        statements.extend(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect))
                          for index in model.__table__.indexes)
    return statements
