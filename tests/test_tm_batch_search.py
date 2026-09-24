from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.services.matcher import (
    _build_fuzzy_match_chunk_statement,
    _find_fuzzy_matches,
    _prepare_sentences,
)
from app.services.tm_batch_search import batch_search_snapshot


def test_duplicate_queries_preserve_positions_and_independent_results():
    prepared = _prepare_sentences(["Repeated text", "---", "Repeated text", "Other text"])
    first, second = SimpleNamespace(values=[1]), SimpleNamespace(values=[2])
    with patch("app.services.matcher._find_fuzzy_matches_chunk", return_value=([first, second], 2)) as query:
        result, count = _find_fuzzy_matches(None, prepared, 0.75)
    assert len(query.call_args.kwargs["prepared_sentences"]) == 2
    assert result == [first, None, first, second]
    assert count == 2
    result[0].values.append(3)
    assert result[2].values == [1]


def test_auxiliary_and_original_text_differences_are_not_collapsed():
    original = _prepare_sentences(["Repeated text"])[0]
    prepared = [original, replace(original, auxiliary_match_text="Other context"),
                replace(original, source_sentence="Repeated  text")]
    with patch("app.services.matcher._find_fuzzy_matches_chunk", return_value=([None] * 3, 0)) as query:
        result, _ = _find_fuzzy_matches(None, prepared, 0.75)
    assert len(query.call_args.kwargs["prepared_sentences"]) == 3
    assert result == [None] * 3


def snapshot_db(*, estimated=300000, actual=300000, readonly="off", fail_index=False):
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    def execute(statement, *args):
        sql = str(statement)
        value = MagicMock()
        value.rowcount = actual
        if "transaction_read_only" in sql:
            value.scalar_one.return_value = readonly
        elif "sum(entry_count)" in sql:
            value.scalar_one.return_value = estimated
        elif sql == "SHOW statement_timeout":
            value.scalar_one.return_value = "8s"
        elif "CREATE INDEX" in sql and fail_index:
            raise OperationalError(sql, {}, Exception("statement timeout"))
        return value
    db.execute.side_effect = execute
    return db


def snapshot(db, query_count=100):
    return batch_search_snapshot(db, query_count=query_count, collection_ids=[uuid4()],
                                 source_language="en-US", target_language="zh-CN")


def test_small_interactive_request_does_not_build_snapshot():
    db = snapshot_db()
    with snapshot(db, 5) as relation:
        assert relation is None
    db.execute.assert_not_called()


def test_chinese_source_does_not_pay_snapshot_build_cost():
    db = snapshot_db()
    with batch_search_snapshot(db, query_count=1000, collection_ids=[uuid4()],
                               source_language="zh-CN", target_language="en-US") as relation:
        assert relation is None
    db.execute.assert_not_called()


@pytest.mark.parametrize("kwargs", [{"readonly": "on"}, {"estimated": 10000000},
                                   {"estimated": 1}])
def test_unsupported_snapshot_scope_keeps_original_search(kwargs):
    db = snapshot_db(**kwargs)
    with snapshot(db) as relation:
        assert relation is None
    assert not any("CREATE TEMP" in str(call.args[0]) for call in db.execute.call_args_list)


def test_truncated_snapshot_never_used_for_matching():
    db = snapshot_db(actual=10000000)
    with snapshot(db) as relation:
        assert relation is None
    statements = [str(call.args[0]) for call in db.execute.call_args_list]
    assert any("DROP TABLE" in sql for sql in statements)
    assert not any("CREATE INDEX" in sql for sql in statements)


def test_index_build_failure_falls_back_inside_savepoint():
    db = snapshot_db(fail_index=True)
    with snapshot(db) as relation:
        assert relation is None
    db.begin_nested.assert_called_once()


def test_successful_snapshot_restores_timeout_and_cleans_up():
    db = snapshot_db()
    with snapshot(db) as relation:
        assert relation.startswith("pg_temp.tm_fuzzy_")
        assert db.execute.call_args.args[1] == {"value": "8s"}
    assert str(db.execute.call_args.args[0]) == f"DROP TABLE {relation}"


def test_query_error_keeps_original_exception_without_cleanup_sql():
    db = snapshot_db()
    with pytest.raises(RuntimeError, match="query failure"):
        with snapshot(db):
            count = db.execute.call_count
            raise RuntimeError("query failure")
    assert db.execute.call_count == count


def test_snapshot_sql_preserves_filters_score_and_tie_order():
    kwargs = dict(use_projection=True, value_rows=["(0, 'source', :q, :lo, :hi)"],
                  collection_ids=[uuid4()], source_language="en-US", target_language="zh-CN")
    original = str(_build_fuzzy_match_chunk_statement({}, **kwargs))
    optimized = str(_build_fuzzy_match_chunk_statement({}, **kwargs, search_table="pg_temp.tm_fuzzy_test"))
    assert "FROM pg_temp.tm_fuzzy_test AS ts" in optimized
    assert "ts.updated_at DESC, ts.entry_id ASC" in optimized
    assert "int4range(ts.source_length, ts.source_length, '[]') <@ int4range(input.min_length, input.max_length, '[]')" in optimized
    assert "source_normalized <-> input.query_text, similarity(ts.source_normalized, input.query_text) DESC, ts.updated_at DESC" in optimized
    for condition in ["ts.source_normalized % input.query_text", "ts.source_length BETWEEN input.min_length AND input.max_length", "ts.collection_id IN", "ts.source_language =", "ts.target_language =", "LIMIT :candidate_limit"]:
        assert condition in original and condition in optimized
