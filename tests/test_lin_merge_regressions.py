from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.routers import api
from app.services import file_export_queue as exports
from app.services import file_record_service as records
from app.services.adapters.models import BlockNode, DocumentAST, NodeType
from app.services.adapters.segment_extractor import SegmentExtractor


@pytest.mark.parametrize(
    "metadata,count",
    [
        ({"entity_type": "MTEXT", "is_merged": True}, 1),
        ({"entity_type": "MTEXT", "handle": "A1"}, 2),
        ({"preserve_as_single_segment": True}, 1),
        ({}, 2),
    ],
)
def test_cad_merged_units_and_existing_sentence_modes(metadata, count):
    ast = DocumentAST(nodes=[BlockNode(
        NodeType.TEXT, text_content="First sentence! Second sentence!", metadata=metadata,
    )])
    segments = SegmentExtractor().extract(ast)
    assert len(segments) == count
    if metadata.get("handle"):
        assert all(segment.metadata["cad_sentence_split"] for segment in segments)


def test_async_export_route_preserves_revision_option():
    import asyncio
    from unittest.mock import AsyncMock

    task = {"task_id": str(uuid4())}
    with patch.object(api, "_queue_file_record_export_for_current_user", new_callable=AsyncMock) as queue:
        queue.return_value = task
        response = asyncio.run(api.create_file_record_export_task(
            uuid4(), type="original",
            payload=api.FileRecordExportPayload(include_revision_marks=True),
            db=MagicMock(), current_user=SimpleNamespace(id=uuid4()),
        ))
    assert response.status_code == 202
    assert queue.call_args.kwargs["include_revision_marks"] is True


def test_source_copy_uses_path_without_materializing_large_file(tmp_path):
    source = tmp_path / "source.ai"
    source.write_bytes(b"test source")
    db = MagicMock()
    record = SimpleNamespace(id=uuid4(), filename="source.ai")
    duplicate = SimpleNamespace(id=uuid4(), filename="copy.ai")
    with patch.object(records, "save_source_file_from_path") as copy, \
         patch.object(records, "save_source_file") as copy_bytes, \
         patch.object(records, "_remember_pending_source_file"), \
         patch.object(records, "_build_duplicate_source_filename", return_value="copy.ai"):
        records.copy_file_record_source(db, record, duplicate, source)
    copy.assert_called_once_with(duplicate.id, "copy.ai", source)
    copy_bytes.assert_not_called()


@pytest.mark.parametrize("path_based", [False, True])
def test_export_worker_handles_content_and_paths_preserving_revision_and_notes(tmp_path, path_based):
    task_id = uuid4()
    task = SimpleNamespace(id=task_id, file_record_id=uuid4(), export_type="original", created_by_id=uuid4())
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = task
    source_path = tmp_path / f"{task_id}.pdf"
    source_path.write_bytes(b"export content")
    exported = exports._GenericExportedFile(
        filename="translated.pdf", media_type="application/pdf", notes=["Some pages rasterized."],
        **({"path": source_path} if path_based else {"content": b"export content"}),
    )
    with patch.object(exports, "SessionLocal") as sessions, \
         patch.object(exports, "get_file_export_task", return_value=task), \
         patch.object(exports, "get_file_record_model", return_value=SimpleNamespace(filename="source.ai")), \
         patch.object(exports, "_set_file_export_task_status") as status, \
         patch.object(exports, "_ensure_export_dir", return_value=tmp_path), \
         patch.object(exports, "_cleanup_expired_export_files"), \
         patch.object(exports, "build_file_record_exported_file", return_value=exported) as build:
        sessions.return_value.__enter__.return_value = db
        exports._store_revision_mark_option(task_id, True)
        exports._run_file_export_task(task_id)
    assert build.call_args.kwargs["include_revision_marks"] is True
    assert build.call_args.kwargs["release_transaction_before_render"] is True
    assert Path(task.result_path).read_bytes() == b"export content"
    assert task.size_bytes == len(b"export content")
    assert status.call_args.args[2] == "completed"
    assert "Some pages rasterized." in status.call_args.kwargs["message"]
