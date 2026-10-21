"""合并视图流程流转的权限、范围和事务回归。"""
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (User, Project, ProjectMergeView, ProjectWorkflowStep, ProjectAssignment,
                        FileRecord, FileAssignment, FileSegmentStats, Segment, SegmentRevision, ProofreadingBatch,
                        ProofreadingColumnBinding, ProofreadingSegmentBaseline,
                        TranslationReviewReport, TranslationReviewReportItem)
from app.routers import api


class MergeWorkflowTransitionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        tables = [User.__table__, Project.__table__, ProjectMergeView.__table__,
                  ProjectWorkflowStep.__table__, ProjectAssignment.__table__, FileRecord.__table__,
                  FileAssignment.__table__, FileSegmentStats.__table__, Segment.__table__, SegmentRevision.__table__,
                  ProofreadingBatch.__table__, ProofreadingColumnBinding.__table__,
                  ProofreadingSegmentBaseline.__table__, TranslationReviewReport.__table__,
                  TranslationReviewReportItem.__table__]
        defaults = []
        for table in tables:
            for column in table.columns:
                if column.server_default is not None and "::uuid" in str(column.server_default.arg):
                    defaults.append((column, column.server_default))
                    column.server_default = None
        try:
            for table in tables:
                table.create(self.engine)
        finally:
            for column, default in defaults:
                column.server_default = default
        self.db = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.user = User(username="admin", hashed_password="x", role="admin", is_active=True)
        self.project = Project(name="合并流转", workflow_template_id="translate_review_proofread")
        self.db.add_all([self.user, self.project])
        self.db.flush()
        self.source = ProjectWorkflowStep(project_id=self.project.id, step_key="review",
                                          step_type="review", name="审校", sort_order=1)
        self.target = ProjectWorkflowStep(project_id=self.project.id, step_key="proofread",
                                          step_type="proofread", name="校对", sort_order=2)
        self.db.add_all([self.source, self.target])
        self.db.flush()
        self.files, self.segments = [], []
        for file_index in range(2):
            file = FileRecord(project_id=self.project.id, filename=f"文件{file_index}.txt",
                              source_language="en-US", target_language="fr-FR")
            self.db.add(file)
            self.db.flush()
            self.files.append(file)
            for index in range(2):
                segment = Segment(file_record_id=file.id, workflow_step_id=self.source.id,
                                  sentence_id=f"s{index}", source_text=f"Source {index}", display_text=f"Source {index}",
                                  target_text=f"Traduction {index}", display_index=index,
                                  sequence_index=index, status="none", source="manual", version=1)
                self.db.add(segment)
                self.segments.append(segment)
        self.view = ProjectMergeView(project_id=self.project.id, name="合并视图",
                                     file_ids=json.dumps([str(file.id) for file in self.files]))
        self.db.add(self.view)
        self.db.commit()

    def payload(self, **kwargs):
        return api.WorkflowTransitionRequest(from_step_id=self.source.id, target_step_id=self.target.id,
                                             **kwargs)

    def preview(self, payload=None, user=None):
        return api.preview_merge_view_workflow_transition(self.view.id, payload or self.payload(),
                                                         self.db, user or self.user)

    def submit(self, payload=None, user=None):
        return api.transition_merge_view_workflow(self.view.id, payload or self.payload(),
                                                  BackgroundTasks(), self.db, user or self.user)

    def lock(self, file):
        file.active_operation = "pre_translate"
        file.active_operation_updated_at = datetime.now()
        self.db.commit()

    def test_all_files_transition_and_proofreading_baselines(self):
        self.assertEqual(self.preview()["matched_count"], 4)
        result = self.submit()
        self.assertEqual(result["updated_count"], 4)
        self.assertEqual([item["updated_count"] for item in result["files"]], [2, 2])
        self.assertTrue(all(segment.workflow_step_id == self.target.id for segment in self.segments))
        self.assertTrue(all(segment.version == 2 for segment in self.segments))
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 4)

    def test_global_range_crosses_file_boundary_and_status_filter(self):
        payload = self.payload(all_segments=False, range_start=2, range_end=3, source_statuses=["none"])
        self.assertEqual(self.preview(payload)["matched_count"], 2)
        self.assertEqual(self.submit(payload)["updated_count"], 2)
        self.assertEqual([s.workflow_step_id == self.target.id for s in self.segments],
                         [False, True, True, False])

    def test_locked_file_skipped_and_submit_rechecks_after_preview(self):
        self.assertEqual(self.preview()["matched_count"], 4)
        self.lock(self.files[1])
        result = self.submit()
        self.assertEqual(result["updated_count"], 2)
        self.assertIn("锁定", result["files"][1]["skipped_reason"])
        self.assertEqual(self.segments[2].workflow_step_id, self.source.id)

    def test_all_locked_zero_matches(self):
        for file in self.files:
            self.lock(file)
        self.assertEqual(self.preview()["matched_count"], 0)
        self.assertEqual(self.submit()["updated_count"], 0)

    def test_assignment_ranges_and_wrong_source_stage_skipped(self):
        user = User(username="worker", hashed_password="x", role="user", translator_type="external")
        self.db.add(user)
        self.db.flush()
        self.db.add(ProjectAssignment(project_id=self.project.id, assignee_id=user.id, status="active"))
        for index, file in enumerate(self.files):
            self.db.add(FileAssignment(project_id=self.project.id, file_record_id=file.id,
                workflow_step_id=self.source.id if index == 0 else self.target.id,
                assignee_id=user.id, segment_range_start=2, segment_range_end=2, status="active"))
        self.db.commit()
        preview = self.preview(user=user)
        self.assertEqual(preview["matched_count"], 1)
        self.assertIn("权限", preview["files"][1]["skipped_reason"])
        self.assertEqual(self.submit(user=user)["updated_count"], 1)
        self.assertEqual(self.segments[0].workflow_step_id, self.source.id)
        self.assertEqual(self.segments[1].workflow_step_id, self.target.id)

    def test_unexpected_error_rolls_back_first_file_and_baselines(self):
        from app.services.workflow_proofreading import prepare_workflow_proofreading
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("模拟第二个文件失败")
            return prepare_workflow_proofreading(*args, **kwargs)
        with patch("app.services.workflow_proofreading.prepare_workflow_proofreading", side_effect=fail_second):
            with self.assertRaises(RuntimeError):
                self.submit()
        self.assertTrue(all(segment.workflow_step_id == self.source.id for segment in self.segments))
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 0)
        self.assertTrue(all(segment.version == 1 for segment in self.segments))

    def test_invalid_range_or_foreign_step_rejected(self):
        for payload in [self.payload(all_segments=False, range_start=3, range_end=2),
                        self.payload().model_copy(update={"target_step_id": uuid4()}),
                        self.payload().model_copy(update={"target_step_id": self.source.id})]:
            with self.assertRaises(HTTPException) as raised:
                self.preview(payload)
            self.assertEqual(raised.exception.status_code, 400)

    def test_backward_and_no_matching_status(self):
        self.assertEqual(self.preview(self.payload(source_statuses=["confirmed"]))["matched_count"], 0)
        self.submit()
        payload = api.WorkflowTransitionRequest(from_step_id=self.target.id, target_step_id=self.source.id)
        self.assertEqual(self.preview(payload)["matched_count"], 4)
        self.assertEqual(self.submit(payload)["updated_count"], 4)
        self.assertTrue(all(segment.workflow_step_id == self.source.id for segment in self.segments))

    def test_confirmed_target_uses_auto_tm_pipeline(self):
        with patch.object(api, "enqueue_confirmed_segments_for_auto_tm", return_value=api._empty_auto_tm_summary()) as enqueue:
            self.assertEqual(self.submit(self.payload(target_status="confirmed"))["updated_count"], 4)
        self.assertEqual(enqueue.call_count, 2)
        self.assertTrue(all(segment.status == "confirmed" for segment in self.segments))

    def test_single_file_endpoint_preserves_range_and_side_effects(self):
        payload = self.payload(all_segments=False, range_start=2, range_end=2)
        preview = api.preview_file_record_workflow_transition(self.files[0].id, payload, self.db, self.user)
        self.assertEqual(preview["matched_count"], 1)
        with patch.object(api, "_get_segment_status_stats", return_value={}):
            result = api.transition_file_record_workflow(self.files[0].id, payload, BackgroundTasks(),
                                                        self.db, self.user, None)
        self.assertEqual(result["updated_count"], 1)
        self.assertEqual(self.segments[0].workflow_step_id, self.source.id)
        self.assertEqual(self.segments[1].workflow_step_id, self.target.id)
        self.assertEqual(self.segments[2].workflow_step_id, self.source.id)
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 1)

    def test_invisible_view_denied(self):
        user = User(username="outsider", hashed_password="x", role="user", translator_type="external")
        self.db.add(user)
        self.db.commit()
        with self.assertRaises(HTTPException):
            self.submit(user=user)
        self.assertTrue(all(segment.workflow_step_id == self.source.id for segment in self.segments))

    def test_detail_exposes_project_workflow_steps(self):
        # 状态统计包含 PostgreSQL 正则运算，SQLite 中仅替换这一无关查询。
        with patch("app.services.merge_view_service._file_status_stats", return_value={"total": 2, "confirmed": 0}):
            result = api.get_merge_view_detail(self.view.id, self.db, self.user)
        self.assertEqual([step["id"] for step in result["workflow_steps"]], [str(self.source.id), str(self.target.id)])
        self.assertEqual(result["workflow_active_step_ids"], [str(self.source.id)])


if __name__ == "__main__":
    unittest.main()
