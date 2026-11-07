"""验证分批导入的顺序、翻译事件和整批回滚行为。"""
import json
from unittest.mock import patch
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import FileRecord, Segment, TranslationMetricEvent
from app.services import file_record_service as service


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.document_storage._get_storage_root", lambda: tmp_path)
    engine = create_engine("sqlite:///:memory:")
    for model in (FileRecord, Segment, TranslationMetricEvent):
        defaults = [(column, column.server_default) for column in model.__table__.columns
                    if column.server_default is not None and "::uuid" in str(column.server_default.arg)]
        try:
            for column, _ in defaults:
                column.server_default = None
            model.__table__.create(engine)
        finally:
            for column, default in defaults:
                column.server_default = default
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


def workspace(count=7):
    return {"segments": [
        {"sentence_id": f"seg-{index:06d}", "source_text": f"测试内容 {index}",
         "display_text": f"测试内容 {index}", "source_html": "<b>测试</b>",
         "target_text": f"Translation {index}" if index % 2 else "",
         "status": "exact" if index % 2 else "none", "score": 1.0 if index % 2 else 0.0,
         "matched_source_text": "", "block_type": "table_cell", "block_index": 0,
         "row_index": index, "cell_index": 0, "segment_metadata": {"cell_ref": f"A{index + 1}"}}
        for index in range(count)
    ]}


def test_batches_preserve_segments_events_and_caller_transaction(db):
    batch_counts = []

    def track_insert(state):
        if state.is_insert and state.bind_mapper.class_ is Segment:
            batch_counts.append(len(state.parameters))

    event.listen(db, "do_orm_execute", track_insert)
    commits = []
    event.listen(db, "after_commit", lambda _: commits.append(True))
    with patch.object(service, "_SEGMENT_INSERT_BATCH_SIZE", 3):
        record = service.create_file_record_with_segments(db, b"", "test.xlsx", workspace_data=workspace())
    db.flush()
    rows = db.query(Segment).order_by(Segment.sequence_index).all()
    assert [row.sequence_index for row in rows] == list(range(7))
    assert [row.display_index for row in rows] == list(range(7))
    assert batch_counts == [3, 3, 1]
    assert commits == []
    assert db.query(TranslationMetricEvent).count() == 3
    for index, row in enumerate(rows):
        assert json.loads(row.segment_metadata)["cell_ref"] == f"A{index + 1}"
        assert row.auto_write_protection == ("protected" if index % 2 else "none")
        assert row.version == (2 if index % 2 else 1)
        assert row.id is not None and row.created_at is not None
    file_id = record.id
    db.rollback()
    assert db.get(FileRecord, file_id) is None
    assert db.query(Segment).count() == 0
    assert db.query(TranslationMetricEvent).count() == 0


def test_later_batch_failure_rolls_back_earlier_batches(db):
    data = workspace()
    data["segments"][4]["source_text"] = None
    with patch.object(service, "_SEGMENT_INSERT_BATCH_SIZE", 3):
        with pytest.raises(IntegrityError):
            service.create_file_record_with_segments(db, b"", "test.xlsx", workspace_data=data)
    db.rollback()
    assert db.query(FileRecord).count() == 0
    assert db.query(Segment).count() == 0
    assert db.query(TranslationMetricEvent).count() == 0


@pytest.mark.parametrize("dialect", ["postgresql", "sqlite"])
def test_import_timeout_is_transaction_local_and_precedes_parsing(dialect):
    from app.routers import api

    session = MagicMock()
    session.get_bind.return_value.dialect.name = dialect
    context = MagicMock()
    context.__enter__.return_value = session

    def parse(*args):
        if dialect == "postgresql":
            statement = session.execute.call_args.args[0]
            assert "set_config" in str(statement)
            assert list(statement.compile().params.values()) == [
                "idle_in_transaction_session_timeout", "3600s", True,
            ]
        else:
            session.execute.assert_not_called()
        return {}

    with (
        patch.object(api, "SessionLocal", return_value=context),
        patch.object(api, "_process_project_source_import", side_effect=parse) as processor,
        patch.object(api, "_set_import_task_status"),
        patch.object(api, "raise_if_import_task_canceled"),
        patch.object(api, "cleanup_import_task_staging"),
        patch.object(api, "get_settings", return_value=MagicMock(import_worker_job_timeout_seconds=3600)),
    ):
        api._run_import_task("timeout-test", {"kind": "project_source_document"})
    processor.assert_called_once()
    session.rollback.assert_not_called()
