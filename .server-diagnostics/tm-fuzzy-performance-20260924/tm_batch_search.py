from __future__ import annotations

import logging
from contextlib import contextmanager
from time import perf_counter
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings

logger = logging.getLogger(__name__)


@contextmanager
def batch_search_snapshot(db, *, query_count, collection_ids, source_language, target_language):
    """大批量匹配复用本事务的限定库快照；失败时仍走原始完整查询。"""
    settings = get_settings()
    source_language = (source_language or "").strip()
    target_language = (target_language or "").strip()
    if (
        db is None
        or not getattr(settings, "tm_batch_snapshot_enabled", True)
        or query_count < getattr(settings, "tm_batch_snapshot_min_queries", 100)
        or not collection_ids
        # 当前性能对照针对英文大库，中文等方向保留原索引，避免额外建索引开销。
        or source_language.lower().split("-")[0] != "en"
    ):
        yield None
        return
    if db.get_bind().dialect.name != "postgresql":
        yield None
        return
    if db.execute(text("SHOW transaction_read_only")).scalar_one() == "on":
        yield None
        return

    maximum = getattr(settings, "tm_batch_snapshot_max_entries", 1000000)
    params = {"ids": list(dict.fromkeys(collection_ids))}
    estimated = db.execute(
        text("SELECT COALESCE(sum(entry_count), 0) FROM memory_bases WHERE id IN :ids")
        .bindparams(bindparam("ids", expanding=True)), params,
    ).scalar_one()
    if estimated < 50000 or estimated > maximum:
        yield None
        return

    # 标识符完全由程序生成，不拼接用户输入。ON COMMIT DROP 覆盖异常回滚和连接池复用。
    table = "tm_fuzzy_" + uuid4().hex
    relation = "pg_temp." + table
    where = "collection_id IN :ids"
    for name, value in (("source_language", source_language), ("target_language", target_language)):
        if value:
            where += f" AND {name} = :{name}"
            params[name] = value
    params["limit"] = maximum + 1
    started = perf_counter()
    ready = False
    try:
        # 保存点保证建表/建索引失败不会破坏调用方的统计报告或预翻译事务。
        with db.begin_nested():
            previous = db.execute(text("SHOW statement_timeout")).scalar_one()
            db.execute(text("SELECT set_config('statement_timeout', '120s', true)"))
            result = db.execute(
                text(f"CREATE TEMP TABLE {table} ON COMMIT DROP AS "
                     f"SELECT entry_id, collection_id, source_language, target_language, "
                     f"source_normalized, source_length, updated_at FROM memory_entry_search "
                     f"WHERE {where} LIMIT :limit")
                .bindparams(bindparam("ids", expanding=True)), params,
            )
            row_count = result.rowcount
            if row_count < 0:
                row_count = db.execute(text(f"SELECT count(*) FROM {relation}")).scalar_one()
            if row_count > maximum:
                # 元数据计数落后时也不能使用截断快照，必须回退完整检索。
                db.execute(text(f"DROP TABLE {relation}"))
            else:
                db.execute(text(f"CREATE INDEX ON {relation} USING gist "
                                "(int4range(source_length, source_length, '[]'), "
                                "source_normalized gist_trgm_ops(siglen=256))"))
                db.execute(text(f"ANALYZE {relation}"))
                ready = True
            db.execute(text("SELECT set_config('statement_timeout', :value, true)"), {"value": previous})
    except SQLAlchemyError:
        ready = False
        logger.warning("TM batch snapshot unavailable; using original search", exc_info=True)
    if not ready:
        yield None
        return
    logger.info("TM batch snapshot ready rows=%s queries=%s build_ms=%.0f",
                row_count, query_count, (perf_counter() - started) * 1000)
    yield relation
    # 若查询中途异常，不对已中止的事务发 SQL；外层回滚会自动清理临时表。
    db.execute(text(f"DROP TABLE {relation}"))
