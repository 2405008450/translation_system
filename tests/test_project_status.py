from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from app.routers.api import (
    _build_project_detail_payload,
    _build_project_file_payload,
    _build_project_summary_payload,
    _resolve_workflow_status,
    _serialize_assignment_event,
    _process_project_source_import,
    _resolve_project_upload_language_pairs,
    _resolve_upload_language_pair,
    _run_import_task,
    _validate_project_upload_generation_count,
    _validate_project_upload_resources,
)
from app.services.file_record_service import calculate_file_record_progress, resolve_file_record_status
from app.services.task_file_service import UploadLimitError


class ProjectStatusTests(unittest.TestCase):
    def _build_project(self, status: str) -> SimpleNamespace:
        now = datetime(2026, 4, 22, 12, 0, 0)
        creator = SimpleNamespace(username="tester", nickname=None)
        return SimpleNamespace(
            id=uuid4(),
            name="demo.docx",
            status=status,
            source_language="zh",
            target_language="en",
            creator=creator,
            deadline=None,
            access_level="team",
            translation_guidelines="",
            created_at=now,
            updated_at=now,
        )

    def _build_file_record(self, project_id, status: str = "in_progress") -> SimpleNamespace:
        now = datetime(2026, 4, 22, 12, 0, 0)
        return SimpleNamespace(
            id=uuid4(),
            project_id=project_id,
            filename="demo.docx",
            status=status,
            document_parse_mode="full",
            source_language="zh",
            target_language="en",
            creator=None,
            deadline=None,
            access_level="team",
            collection_id=None,
            term_base_id=None,
            created_at=now,
            updated_at=now,
        )

    def test_calculate_file_record_progress_returns_100_for_fully_translated_project(self):
        self.assertEqual(calculate_file_record_progress(5, 5), 100)

    def test_calculate_file_record_progress_does_not_round_partial_progress_to_100(self):
        self.assertEqual(calculate_file_record_progress(200, 199), 99)

    def test_resolve_file_record_status_marks_full_progress_as_completed(self):
        self.assertEqual(resolve_file_record_status("in_progress", 5, 5), "completed")

    def test_build_project_summary_payload_uses_effective_completed_status(self):
        payload = _build_project_summary_payload(
            project=self._build_project(status="in_progress"),
            total_segments=3,
            translated_segments=3,
            file_count=1,
            creator_name="tester",
        )

        self.assertEqual(payload["progress"], 100)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["creator"], "tester")

    def test_build_project_summary_payload_keeps_nearly_done_project_in_progress(self):
        payload = _build_project_summary_payload(
            project=self._build_project(status="in_progress"),
            total_segments=200,
            translated_segments=199,
            file_count=1,
            creator_name="tester",
        )

        self.assertEqual(payload["progress"], 99)
        self.assertEqual(payload["status"], "in_progress")

    def test_build_project_summary_payload_rolls_up_workflow_stage_progress(self):
        payload = _build_project_summary_payload(
            project=self._build_project(status="in_progress"),
            total_segments=10,
            translated_segments=0,
            file_count=1,
            creator_name="tester",
            workflow_progress=[
                {"total_segments": 10, "completed_segments": 5, "progress": 50},
                {"total_segments": 10, "completed_segments": 0, "progress": 0},
            ],
        )

        self.assertEqual(payload["progress"], 25)
        self.assertEqual(payload["workflow_progress"][-1]["progress"], 0)

    def test_build_project_file_payload_rolls_up_workflow_stage_progress(self):
        project = self._build_project(status="in_progress")
        file_record = self._build_file_record(project.id)

        with patch("app.routers.api.get_file_record_source_size", return_value=4):
            payload = _build_project_file_payload(
                file_record=file_record,
                total_segments=10,
                translated_segments=0,
                workflow_progress=[
                    {"total_segments": 10, "completed_segments": 10, "progress": 100},
                    {"total_segments": 10, "completed_segments": 0, "progress": 0},
                ],
            )

        self.assertEqual(payload["progress"], 50)
        self.assertEqual(payload["workflow_progress"][-1]["progress"], 0)

    def test_confirmed_translation_does_not_complete_unfinished_review(self):
        workflow = [
            {"total_segments": 36, "completed_segments": 36, "progress": 100},
            {"total_segments": 36, "completed_segments": 22, "progress": 61},
        ]
        project = self._build_project(status="completed")
        summary = _build_project_summary_payload(project, 36, 36, 1, workflow_progress=workflow)
        with patch("app.routers.api.get_file_record_source_size", return_value=4):
            file_payload = _build_project_file_payload(
                self._build_file_record(project.id, status="completed"),
                36, 36, workflow_progress=workflow,
            )
        self.assertEqual(summary["status"], "in_progress")
        self.assertEqual(file_payload["status"], "in_progress")
        self.assertLess(summary["progress"], 100)

    def test_workflow_status_requires_every_stage_and_preserves_errors(self):
        complete = [{"total_segments": 5, "completed_segments": 5, "progress": 100}]
        self.assertEqual(_resolve_workflow_status("in_progress", 5, 5, complete), "completed")
        self.assertEqual(_resolve_workflow_status("error", 5, 5, complete), "error")
        self.assertEqual(_resolve_workflow_status("draft", 0, 0, complete), "draft")
        self.assertEqual(_resolve_workflow_status("completed", 5, 4, []), "in_progress")

    def test_assignment_history_exposes_stage_without_losing_range(self):
        event = SimpleNamespace(
            id=uuid4(), project_id=uuid4(), project=None, file_record_id=uuid4(),
            file_record=None, assignee_id=uuid4(), assignee=None, actor_id=None,
            actor=None, action="file_permission_granted", before_payload="{}",
            after_payload='{"workflow_step_id":"review", "range_start":1, "range_end":20}',
            created_at=datetime(2026, 11, 3, 10, 0, 1),
        )
        payload = _serialize_assignment_event(event, {"review": "校对"})
        self.assertEqual(payload["workflow_step_name"], "校对")
        self.assertEqual(payload["after_payload"]["range_end"], 20)
        self.assertEqual(payload["id"], str(event.id))

    def test_build_project_detail_payload_uses_effective_completed_status(self):
        project = self._build_project(status="in_progress")
        file_record = self._build_file_record(project.id)
        file_stats = {file_record.id: {"total": 2, "filled": 2}}

        with patch("app.routers.api.get_file_record_source_size", return_value=4):
            payload = _build_project_detail_payload(project, [file_record], file_stats)

        self.assertEqual(payload["progress"], 100)
        self.assertEqual(payload["status"], "completed")
        self.assertTrue(payload["has_source_document"])
        self.assertEqual(payload["file_size_bytes"], 4)


class ProjectLanguagePairBindingTests(unittest.TestCase):
    def test_project_binding_supplies_upload_language_pair(self):
        project = SimpleNamespace(source_language="zh-CN", target_language="en-US")

        pair = _resolve_upload_language_pair(None, None, project=project)

        self.assertEqual(pair, ("zh-CN", "en-US"))

    def test_project_binding_accepts_matching_submitted_pair(self):
        project = SimpleNamespace(source_language="zh-CN", target_language="en-US")

        pair = _resolve_upload_language_pair("zh", "en", project=project)

        self.assertEqual(pair, ("zh-CN", "en-US"))

    def test_project_binding_rejects_conflicting_submitted_pair(self):
        project = SimpleNamespace(source_language="zh-CN", target_language="en-US")

        with self.assertRaises(HTTPException) as context:
            _resolve_upload_language_pair("ja-JP", "en-US", project=project)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("项目已绑定语言对", str(context.exception.detail))

    def test_project_binding_rejects_conflicting_collection_pair(self):
        project = SimpleNamespace(source_language="zh-CN", target_language="en-US")
        collection = SimpleNamespace(source_language="ja-JP", target_language="en-US")

        with self.assertRaises(HTTPException) as context:
            _resolve_upload_language_pair(None, None, collection, project=project)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("记忆库", str(context.exception.detail))

    def test_legacy_single_target_upload_remains_supported(self):
        source, targets = _resolve_project_upload_language_pairs(
            "zh",
            "en",
            None,
        )

        self.assertEqual(source, "zh-CN")
        self.assertEqual(targets, ["en-US"])

    def test_multi_target_upload_normalizes_and_deduplicates_in_order(self):
        source, targets = _resolve_project_upload_language_pairs(
            "zh",
            "ignored-by-new-field",
            ["en", "ja", "en-US", "fr"],
        )

        self.assertEqual(source, "zh-CN")
        self.assertEqual(targets, ["en-US", "ja-JP", "fr-FR"])

    def test_multi_target_upload_rejects_source_as_target(self):
        with self.assertRaises(HTTPException) as context:
            _resolve_project_upload_language_pairs("zh-CN", None, ["en-US", "zh"])

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("不能相同", str(context.exception.detail))

    def test_multi_target_upload_rejects_empty_or_unknown_target(self):
        with self.assertRaises(HTTPException) as empty_context:
            _resolve_project_upload_language_pairs("zh-CN", None, [])
        self.assertIn("至少选择一个", str(empty_context.exception.detail))

        with self.assertRaises(HTTPException) as unknown_context:
            _resolve_project_upload_language_pairs("zh-CN", None, ["xx-UNKNOWN"])
        self.assertIn("不在当前支持的语言列表", str(unknown_context.exception.detail))

    def test_bound_project_rejects_multiple_targets(self):
        project = SimpleNamespace(source_language="zh-CN", target_language="en-US")

        with self.assertRaises(HTTPException) as context:
            _resolve_project_upload_language_pairs(
                "zh-CN",
                None,
                ["en-US", "ja-JP"],
                project=project,
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("已绑定语言对", str(context.exception.detail))

    def test_multi_target_upload_rejects_single_pair_resources(self):
        with self.assertRaises(HTTPException) as context:
            _validate_project_upload_resources(
                ["en-US", "ja-JP"],
                [uuid4()],
                None,
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("记忆库或术语库", str(context.exception.detail))

    def test_generated_task_count_uses_file_language_cartesian_product(self):
        self.assertEqual(_validate_project_upload_generation_count(3, 4, 20), 12)

        with self.assertRaises(UploadLimitError) as context:
            _validate_project_upload_generation_count(3, 4, 10)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("12 个任务", context.exception.detail)


class ProjectMultiTargetImportTests(unittest.TestCase):
    def test_import_creates_file_language_cartesian_product(self):
        project = SimpleNamespace(
            id=uuid4(),
            name="multi-target",
            status="draft",
            source_language=None,
            target_language=None,
            creator_id=uuid4(),
            deadline=None,
            access_level="team",
        )
        db = unittest.mock.MagicMock()
        db.query.return_value.filter.return_value.first.return_value = project
        created_records: list[SimpleNamespace] = []

        def create_record(**_kwargs):
            record = SimpleNamespace(
                id=uuid4(),
                filename="source.txt",
                status="in_progress",
                source_language=None,
                target_language=None,
                collection_id=None,
                collection_ids_json="[]",
            )
            created_records.append(record)
            return record

        payload = {
            "project_id": str(project.id),
            "source_language": "zh-CN",
            "target_language": "en-US",
            "target_languages": ["en-US", "ja-JP"],
            "files": [
                {"filename": "first.txt", "content": b"first"},
                {"filename": "second.txt", "content": b"second"},
            ],
        }

        with (
            patch("app.routers.api._get_collection_or_404", return_value=None),
            patch("app.routers.api._expand_archive_payloads", side_effect=lambda files: files),
            patch("app.routers.api.validate_expanded_upload_batch"),
            patch("app.routers.api.get_settings", return_value=SimpleNamespace(upload_max_expanded_files=100)),
            patch("app.routers.api.read_import_file_bytes", side_effect=lambda item: item["content"]),
            patch("app.routers.api.build_task_workspace", return_value={}) as build_workspace,
            patch("app.routers.api.create_file_record_with_segments", side_effect=create_record),
            patch("app.routers.api.attach_project_reference_bases_to_file"),
            patch("app.routers.api._assign_file_segments_to_first_workflow_step"),
            patch("app.routers.api.sync_project_repeated_segments_from_file"),
            patch("app.routers.api.raise_if_import_task_canceled"),
            patch("app.routers.api._set_import_task_status"),
            patch("app.routers.api._get_file_segment_stats", return_value={}),
            patch(
                "app.routers.api._build_project_file_payload",
                side_effect=lambda file_record, **_kwargs: {
                    "id": str(file_record.id),
                    "source_language": file_record.source_language,
                    "target_language": file_record.target_language,
                },
            ),
        ):
            result = _process_project_source_import(db, "task-1", payload)

        self.assertEqual(result["uploaded_count"], 4)
        self.assertEqual(result["target_languages"], ["en-US", "ja-JP"])
        self.assertEqual(
            [(record.source_language, record.target_language) for record in created_records],
            [
                ("zh-CN", "en-US"),
                ("zh-CN", "ja-JP"),
                ("zh-CN", "en-US"),
                ("zh-CN", "ja-JP"),
            ],
        )
        self.assertEqual(build_workspace.call_count, 4)
        db.commit.assert_called_once()

    def test_import_runner_rolls_back_entire_batch_when_one_combination_fails(self):
        db = unittest.mock.MagicMock()
        session_context = unittest.mock.MagicMock()
        session_context.__enter__.return_value = db
        session_context.__exit__.return_value = False
        payload = {
            "kind": "project_source_document",
            "staging_task_id": "task-rollback",
        }

        with (
            patch("app.routers.api.SessionLocal", return_value=session_context),
            patch(
                "app.routers.api._process_project_source_import",
                side_effect=RuntimeError("second target failed"),
            ),
            patch("app.routers.api._set_import_task_status") as set_status,
            patch("app.routers.api.cleanup_import_task_staging") as cleanup,
        ):
            _run_import_task("task-rollback", payload)

        db.rollback.assert_called_once()
        failed_status = [call for call in set_status.call_args_list if call.args[1] == "failed"]
        self.assertEqual(len(failed_status), 1)
        self.assertIn("second target failed", failed_status[0].kwargs["error"])
        cleanup.assert_called_once_with("task-rollback")


if __name__ == "__main__":
    unittest.main()
