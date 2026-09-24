from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.routers.api import ProjectDocumentStatisticsPayload, compute_project_document_statistics
from app.services.matcher import (
    _apply_match_statement_timeout,
    document_statistics_match_timeout,
)


def session():
    db = MagicMock()
    db.info = {}
    db.execute.return_value.scalar_one.return_value = "8s"
    return db


def test_statistics_timeout_is_session_scoped_and_restores_transaction():
    statistics_db, editor_db = session(), session()
    settings = SimpleNamespace(
        tm_match_statement_timeout_ms=8000,
        document_statistics_tm_timeout_ms=120000,
    )
    with patch("app.services.matcher.get_settings", return_value=settings):
        with document_statistics_match_timeout(statistics_db):
            _apply_match_statement_timeout(statistics_db)
            assert statistics_db.execute.call_args.args[1] == {"timeout_value": "120000ms"}
            _apply_match_statement_timeout(editor_db)
            assert editor_db.execute.call_args.args[1] == {"timeout_value": "8000ms"}
        assert statistics_db.execute.call_args.args[1] == {"timeout_value": "8s"}
        assert statistics_db.info == {}
        _apply_match_statement_timeout(statistics_db)
        assert statistics_db.execute.call_args.args[1] == {"timeout_value": "8000ms"}


def test_failed_query_preserves_error_without_querying_aborted_transaction():
    db = session()
    with pytest.raises(RuntimeError, match="statement timeout"):
        with document_statistics_match_timeout(db):
            raise RuntimeError("statement timeout")
    assert db.execute.call_count == 1
    assert db.info == {}


def test_nested_statistics_scope_restores_outer_override():
    db = session()
    with document_statistics_match_timeout(db):
        outer = dict(db.info)
        with document_statistics_match_timeout(db):
            pass
        assert db.info == outer
    assert db.info == {}


def test_statistics_timeout_rolls_back_incomplete_report():
    db = session()
    project_id, file_id = uuid4(), uuid4()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(id=project_id)
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        SimpleNamespace(id=file_id)
    ]
    failure = OperationalError("SELECT", {}, Exception("canceling statement due to statement timeout"))
    with patch("app.routers.api._load_document_repetition_statistics_for_files", return_value={}), patch(
        "app.routers.api._load_document_match_analysis_for_files", side_effect=failure
    ), pytest.raises(HTTPException) as caught:
        compute_project_document_statistics(
            project_id,
            ProjectDocumentStatisticsPayload(file_ids=[file_id]),
            db=db,
            current_user=SimpleNamespace(id=uuid4()),
        )
    assert caught.value.status_code == 504
    assert "未生成不完整报告" in caught.value.detail
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    assert db.info == {}
