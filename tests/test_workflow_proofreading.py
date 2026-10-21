from __future__ import annotations

import json
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (DocumentAlignmentPair, DocumentAlignmentUnit, FileAssignment, FileRecord, Project, ProjectAssignment, ProjectWorkflowStep,
    ProofreadingBatch, ProofreadingColumnBinding, ProofreadingSegmentBaseline, Segment, SegmentRevision,
    TranslationReviewReport, TranslationReviewReportItem, User)
from app.routers.proofreading import get_file_proofreading_baselines
from app.services.proofreading import generate_batch


class WorkflowTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.storage = TemporaryDirectory()
        self.engine = create_engine("sqlite:///:memory:")
        tables = [User.__table__, Project.__table__, ProjectWorkflowStep.__table__, ProjectAssignment.__table__,
                  FileRecord.__table__, FileAssignment.__table__, Segment.__table__, SegmentRevision.__table__,
                  ProofreadingBatch.__table__, ProofreadingColumnBinding.__table__, ProofreadingSegmentBaseline.__table__,
                  TranslationReviewReport.__table__, TranslationReviewReportItem.__table__,
                  DocumentAlignmentPair.__table__, DocumentAlignmentUnit.__table__]
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
        self.user = User(username="admin", hashed_password="x", role="admin")
        self.project = Project(name="工作流校对", workflow_template_id="proofread")
        self.db.add_all([self.user, self.project])
        self.db.flush()
        self.db.add(ProjectWorkflowStep(project_id=self.project.id, step_key="proofread", name="校对", step_type="proofread", sort_order=0))
        self.db.commit()

    async def asyncTearDown(self):
        self.db.close()
        self.engine.dispose()
        self.storage.cleanup()


class TranslationWorkflowProofreadingTests(WorkflowTestCase):

    def setup_workflow(self):
        from app.services.workflow_proofreading import prepare_workflow_proofreading

        self.project.workflow_template_id = "translate_review_proofread"
        self.project.source_language = "en-US"
        self.project.target_language = "fr-FR"
        self.proofread_step = self.db.query(ProjectWorkflowStep).filter_by(project_id=self.project.id).one()
        self.proofread_step.sort_order = 2
        self.review_step = ProjectWorkflowStep(project_id=self.project.id, step_key="review", name="审校", step_type="review", sort_order=1)
        self.translation_step = ProjectWorkflowStep(project_id=self.project.id, step_key="translate", name="翻译", step_type="translation", sort_order=0)
        self.db.add_all([self.review_step, self.translation_step])
        self.file = FileRecord(project_id=self.project.id, filename="source.txt", source_language="en-US", target_language="fr-FR")
        self.db.add(self.file)
        self.db.flush()
        self.segments = []
        for index in range(4):
            segment = Segment(
                file_record_id=self.file.id, workflow_step_id=self.proofread_step.id if index < 3 else self.review_step.id,
                sentence_id=f"s{index}", source_text=f"source {index}", display_text=f"source {index}",
                target_text=f"Traduction {index}", source="manual", status="none", display_index=index, sequence_index=index,
            )
            self.db.add(segment)
            self.segments.append(segment)
        self.db.commit()
        batch = prepare_workflow_proofreading(self.db, self.file, self.user)
        self.db.commit()
        return batch

    def assign(self, username, start, end):
        user = User(username=username, hashed_password="x", role="user", translator_type="external")
        self.db.add(user)
        self.db.flush()
        self.db.add(ProjectAssignment(project_id=self.project.id, assignee_id=user.id, status="active"))
        assignment = FileAssignment(project_id=self.project.id, file_record_id=self.file.id,
                                    workflow_step_id=self.proofread_step.id, assignee_id=user.id,
                                    segment_range_start=start, segment_range_end=end, status="active")
        self.db.add(assignment)
        self.db.commit()
        return user, assignment

    async def run_generation(self, batch, user=None, callback=None):
        async def review(groups, **kwargs):
            self.last_groups = groups
            if callback:
                callback()
            return ({group["sid"]: {"reviewed_target_text": "Texte corrigé", "category": "accuracy",
                                      "reason": "统一术语", "confidence": "high"} for group in groups}, "test", "test-model")
        with patch("app.services.proofreading._review_group_batch", side_effect=review), \
             patch("app.services.proofreading._attach_reference_context"), \
             patch("app.services.proofreading.record_translation_metric_event"), \
             patch("app.services.proofreading.sync_file_record_status"):
            await generate_batch(self.db, batch, current_user=user or self.user)

    def test_templates_have_explicit_alignment_and_pure_proofreading_steps(self):
        from app.routers.api import WORKFLOW_TEMPLATE_BY_ID, _create_project_workflow_steps
        self.assertEqual(len(WORKFLOW_TEMPLATE_BY_ID), 5)
        self.assertEqual(WORKFLOW_TEMPLATE_BY_ID["proofread"]["name"], "对齐-校对")
        self.assertEqual([step["step_type"] for step in WORKFLOW_TEMPLATE_BY_ID["proofread"]["steps"]], ["alignment", "proofread"])
        for template_id, expected in [("proofread_only", ["proofread"]), ("proofread", ["alignment", "proofread"])]:
            project = Project(name=template_id, workflow_template_id=template_id)
            self.db.add(project)
            self.db.flush()
            steps = _create_project_workflow_steps(self.db, project, [], template_id)
            self.assertEqual([step.step_type for step in steps], expected)

    def test_complete_schema_still_runs_legacy_stage_data_upgrade(self):
        from unittest.mock import MagicMock
        from app.services import schema_setup
        engine = MagicMock()
        engine.connect.return_value.__enter__.return_value.execute.return_value.scalar.side_effect = [True, False]
        with patch.object(schema_setup, "engine", engine), \
             patch.object(schema_setup, "inspect") as inspector, \
             patch.object(schema_setup, "_collect_missing_schema", return_value=[]), \
             patch.object(schema_setup, "_collect_column_length_migrations", side_effect=lambda _: ([], [])), \
             patch("app.services.review_sync_schema.default_needs_upgrade", return_value=False):
            inspector.return_value.has_table.return_value = True
            schema_setup._ensure_runtime_schema_once()
        statements = [str(call.args[0]) for call in engine.begin.return_value.__enter__.return_value.execute.call_args_list]
        self.assertTrue(any("pg_advisory_xact_lock" in sql for sql in statements))
        self.assertTrue(any("INSERT INTO project_workflow_steps" in sql for sql in statements))
        self.assertTrue(any("UPDATE file_assignments" in sql for sql in statements))
        self.assertFalse(any("CREATE TABLE" in sql for sql in statements))

    def test_pure_proofreading_import_uses_proofreading_step_without_alignment(self):
        from openpyxl import Workbook
        from app.services.proofreading import create_batch_from_workbook
        self.project.workflow_template_id = "proofread_only"
        self.db.commit()
        workbook = Workbook()
        workbook.active.append(["原文", "译文"])
        workbook.active.append(["Hello", "Bonjour"])
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        with patch("app.services.proofreading.save_source_file"), \
             patch("app.services.proofreading.attach_project_reference_bases_to_file"):
            batch = create_batch_from_workbook(self.db, project=self.project, current_user=self.user,
                raw_bytes=stream.getvalue(), filename="pure.xlsx", source_language="en-US",
                mappings=[{"sheet_index": 0, "header_row": 1, "source_column": 1,
                           "targets": [{"target_column": 2, "target_language": "fr-FR"}]}])
        self.assertEqual(batch.workflow_stage, "proofreading")
        self.assertEqual(batch.batch_kind, "xlsx_columns")
        segment = self.db.query(Segment).one()
        step = self.db.get(ProjectWorkflowStep, segment.workflow_step_id)
        self.assertEqual(step.step_type, "proofread")

    def test_prepare_is_idempotent_and_preserves_baseline_after_manual_edit(self):
        from app.services.workflow_proofreading import prepare_workflow_proofreading
        batch = self.setup_workflow()
        self.segments[0].target_text = "Modification manuelle"
        self.db.commit()
        same = prepare_workflow_proofreading(self.db, self.file, self.user)
        self.assertEqual(same.id, batch.id)
        self.assertEqual(self.db.query(ProofreadingBatch).count(), 1)
        self.assertEqual(self.db.query(FileRecord).count(), 1)
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 3)
        payload = get_file_proofreading_baselines(self.file.id, self.db, self.user)
        self.assertFalse(payload["is_proofreading"])
        self.assertEqual(payload["proofreading_context"]["batch_kind"], "workflow_segments")
        self.assertEqual(payload["items"][0]["original_target_text"], "Traduction 0")
        self.assertNotIn(str(self.segments[3].id), {row["segment_id"] for row in payload["items"]})

    def test_partial_entry_and_reentry_refresh_only_affected_baseline(self):
        from app.services.workflow_proofreading import prepare_workflow_proofreading
        self.setup_workflow()
        self.segments[0].target_text = "Conserver la base"
        self.segments[3].workflow_step_id = self.proofread_step.id
        self.db.flush()
        prepare_workflow_proofreading(self.db, self.file, self.user, refresh_segment_ids={self.segments[3].id})
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 4)
        baseline = self.db.query(ProofreadingSegmentBaseline).filter_by(segment_id=self.segments[0].id).one()
        self.assertEqual(baseline.original_target_text, "Traduction 0")
        prepare_workflow_proofreading(self.db, self.file, self.user, refresh_segment_ids={self.segments[0].id})
        self.assertEqual(baseline.original_target_text, "Conserver la base")

    async def test_generation_uses_current_saved_text_and_only_assigned_unconfirmed_segments(self):
        from app.services.workflow_proofreading import eligible_workflow_segments
        batch = self.setup_workflow()
        user, _ = self.assign("proofreader", 1, 2)
        self.segments[0].target_text = "Texte enregistré"
        self.segments[1].status = "confirmed"
        self.db.commit()
        self.assertEqual([segment.id for segment in eligible_workflow_segments(self.db, batch, user)], [self.segments[0].id])
        await self.run_generation(batch, user)
        self.assertEqual(self.last_groups[0]["variants"], ["Texte enregistré"])
        self.assertEqual(self.segments[0].target_text, "Texte corrigé")
        self.assertEqual([segment.target_text for segment in self.segments[1:]], ["Traduction 1", "Traduction 2", "Traduction 3"])
        self.assertEqual(self.db.query(SegmentRevision).count(), 1)
        from app.services.document_alignment.export import export_workflow_proofreading_xlsx
        content, _ = export_workflow_proofreading_xlsx(self.db, batch)
        workbook = load_workbook(BytesIO(content))
        self.assertEqual(workbook.active["G2"].value, "统一术语")
        workbook.close()

    async def test_llm_return_does_not_overwrite_concurrent_edit(self):
        batch = self.setup_workflow()
        def edit():
            self.segments[0].target_text = "Édition pendant la requête"
            self.segments[0].version += 1
            self.db.commit()
        await self.run_generation(batch, callback=edit)
        self.assertEqual(self.segments[0].target_text, "Édition pendant la requête")
        self.assertEqual(self.db.query(SegmentRevision).filter_by(segment_id=self.segments[0].id).count(), 0)

    async def test_revoking_assignment_while_llm_runs_prevents_writeback(self):
        batch = self.setup_workflow()
        user, assignment = self.assign("revoked", 1, 2)
        def revoke():
            assignment.status = "revoked"
            self.db.commit()
        await self.run_generation(batch, user, callback=revoke)
        self.assertEqual(self.segments[0].target_text, "Traduction 0")
        self.assertEqual(self.db.query(SegmentRevision).count(), 0)

    async def test_returning_to_review_while_llm_runs_prevents_writeback(self):
        batch = self.setup_workflow()
        def move_back():
            self.segments[0].workflow_step_id = self.review_step.id
            self.db.commit()
        await self.run_generation(batch, callback=move_back)
        self.assertEqual(self.segments[0].target_text, "Traduction 0")
        payload = get_file_proofreading_baselines(self.file.id, self.db, self.user)
        self.assertNotIn(str(self.segments[0].id), {item["segment_id"] for item in payload["items"]})

    async def test_every_llm_request_runs_without_transaction_or_orm_payload(self):
        from sqlalchemy import inspect
        from app.services.proofreading import _build_prompt
        batch = self.setup_workflow()
        calls = []
        async def review(groups, **kwargs):
            self.assertFalse(self.db.in_transaction())
            for group in groups:
                for baseline, segment in group["segments"]:
                    self.assertIsNone(inspect(baseline, raiseerr=False))
                    self.assertIsNone(inspect(segment, raiseerr=False))
                    self.assertIsNotNone(segment.id)
            _build_prompt(groups, kwargs["source_language"], kwargs["target_language"], kwargs["rules_text"])
            self.assertFalse(self.db.in_transaction())
            calls.append(groups[0]["sid"])
            return ({group["sid"]: {"reviewed_target_text": "Texte corrigé", "category": "accuracy",
                                     "reason": "统一术语", "confidence": "high"} for group in groups}, "test", "model")
        with patch("app.services.proofreading.PROOFREADING_BATCH_SIZE", 1), \
             patch("app.services.proofreading._review_group_batch", side_effect=review), \
             patch("app.services.proofreading._attach_reference_context"), \
             patch("app.services.proofreading.record_translation_metric_event"), \
             patch("app.services.proofreading.sync_file_record_status"):
            await generate_batch(self.db, batch, current_user=self.user)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.db.query(SegmentRevision).count(), 3)
        self.assertEqual(batch.status, "completed")

    def test_start_endpoint_releases_request_session_before_background_execution(self):
        from fastapi import BackgroundTasks
        from app.routers.proofreading import ProofreadingGenerateRequest, generate_proofreading_batch
        batch = self.setup_workflow()
        batch_id = batch.id
        tasks = BackgroundTasks()
        with patch("app.routers.proofreading.validate_provider_choice"):
            response = generate_proofreading_batch(batch_id, ProofreadingGenerateRequest(), tasks, self.db, self.user)
        self.assertFalse(self.db.in_transaction())
        self.assertEqual(response["batch_id"], str(batch_id))
        self.assertEqual(tasks.tasks[0].args[0], batch_id)

    async def test_database_details_are_hidden_for_new_and_historical_errors(self):
        from app.services.proofreading import DATABASE_RETRY_MESSAGE, serialize_batch
        batch = self.setup_workflow()
        raw_error = "(psycopg.errors.IdleInTransactionSessionTimeout) idle-in-transaction timeout [SQL: SELECT secret FROM segments]"
        with patch("app.services.proofreading._review_group_batch", side_effect=RuntimeError(raw_error)), \
             patch("app.services.proofreading._attach_reference_context"), \
             patch("app.services.proofreading.sync_file_record_status"):
            await generate_batch(self.db, batch, current_user=self.user)
        self.assertEqual(batch.failed_segments, 3)
        self.assertEqual({item.reason for item in self.db.query(TranslationReviewReportItem)}, {DATABASE_RETRY_MESSAGE})
        item = self.db.query(TranslationReviewReportItem).first()
        item.reason = raw_error
        batch.error_message = raw_error
        self.db.commit()
        self.assertEqual(serialize_batch(self.db, batch)["error_message"], DATABASE_RETRY_MESSAGE)
        payload = get_file_proofreading_baselines(self.file.id, self.db, self.user)
        self.assertNotIn("[SQL:", json.dumps(payload, ensure_ascii=False))

    def test_terminal_database_error_finalizes_report_and_keeps_safe_message(self):
        from contextlib import contextmanager
        from unittest.mock import AsyncMock
        from sqlalchemy.exc import SQLAlchemyError
        from app.services.proofreading import DATABASE_RETRY_MESSAGE, run_generate_batch
        batch = self.setup_workflow()
        batch_id, user_id = batch.id, self.user.id
        report = TranslationReviewReport(project_id=self.project.id, proofreading_batch_id=batch.id,
                                         report_mode="proofread_generate", scope="proofreading_batch", status="running")
        self.db.add(report)
        self.db.commit()
        @contextmanager
        def session():
            yield self.db
        with patch("app.services.proofreading.SessionLocal", side_effect=session), \
             patch("app.services.proofreading.generate_batch", new_callable=AsyncMock,
                   side_effect=SQLAlchemyError("connection interrupted [SQL: SELECT ...]")):
            run_generate_batch(batch_id, user_id, "auto", None)
        self.assertEqual(batch.status, "failed")
        self.assertEqual(batch.error_message, DATABASE_RETRY_MESSAGE)
        self.assertEqual(report.status, "failed")
        self.assertIsNotNone(report.finished_at)

    def test_start_is_scoped_busy_is_rejected_and_only_starter_can_cancel(self):
        from fastapi import BackgroundTasks, HTTPException
        from app.routers.proofreading import ProofreadingGenerateRequest, generate_proofreading_batch, cancel_proofreading_batch
        batch = self.setup_workflow()
        user, _ = self.assign("starter", 1, 1)
        other, _ = self.assign("other", 2, 2)
        with patch("app.routers.proofreading.validate_provider_choice"):
            generate_proofreading_batch(batch.id, ProofreadingGenerateRequest(), BackgroundTasks(), self.db, user)
            config = json.loads(batch.config_json)
            self.assertEqual(config["generation"]["segment_ids"], [str(self.segments[0].id)])
            with self.assertRaises(HTTPException) as busy:
                generate_proofreading_batch(batch.id, ProofreadingGenerateRequest(), BackgroundTasks(), self.db, other)
            self.assertEqual(busy.exception.status_code, 409)
        with self.assertRaises(HTTPException) as forbidden:
            cancel_proofreading_batch(batch.id, self.db, other)
        self.assertEqual(forbidden.exception.status_code, 403)
        result = cancel_proofreading_batch(batch.id, self.db, user)
        self.assertEqual(result["status"], "canceled")

    def test_retry_does_not_include_failures_outside_assignment(self):
        from fastapi import BackgroundTasks, HTTPException
        from app.routers.proofreading import ProofreadingGenerateRequest, generate_proofreading_batch
        batch = self.setup_workflow()
        user, _ = self.assign("retry", 1, 1)
        with patch("app.routers.proofreading.get_latest_generation_error_segment_ids", return_value={self.segments[2].id}):
            with self.assertRaises(HTTPException) as forbidden:
                generate_proofreading_batch(batch.id, ProofreadingGenerateRequest(retry_scope="failed_only"), BackgroundTasks(), self.db, user)
        self.assertEqual(forbidden.exception.status_code, 409)

    def test_stale_queued_callback_cannot_claim_restarted_generation(self):
        from contextlib import contextmanager
        from unittest.mock import AsyncMock
        from app.services.proofreading import run_generate_batch
        batch = self.setup_workflow()
        batch.status = "queued"
        config = json.loads(batch.config_json)
        config["generation"] = {"run_id": "new-run"}
        batch.config_json = json.dumps(config)
        self.db.commit()
        @contextmanager
        def session():
            yield self.db
        with patch("app.services.proofreading.SessionLocal", side_effect=session), \
             patch("app.services.proofreading.generate_batch", new_callable=AsyncMock) as generate:
            run_generate_batch(batch.id, self.user.id, "auto", None, run_id="old-run")
        generate.assert_not_awaited()
        self.assertEqual(batch.status, "queued")

    def test_report_and_batch_cleanup_preserve_original_file(self):
        from app.services.document_alignment.export import export_workflow_proofreading_xlsx
        from app.services.file_record_service import delete_proofreading_batch
        batch = self.setup_workflow()
        self.segments[0].source_text = "=SUM(A1)"
        content, filename = export_workflow_proofreading_xlsx(self.db, batch)
        workbook = load_workbook(BytesIO(content))
        self.assertEqual(workbook.active.max_row, 4)
        self.assertEqual(workbook.active["B2"].data_type, "s")
        self.assertEqual(workbook.active["C2"].value, "Traduction 0")
        self.assertTrue(filename.endswith("_校对报告.xlsx"))
        workbook.close()
        with patch("app.services.file_record_service.get_settings", return_value=SimpleNamespace(file_storage_dir=self.storage.name)):
            delete_proofreading_batch(self.db, batch)
        self.assertIsNotNone(self.db.get(FileRecord, self.file.id))
        self.assertEqual(self.db.query(Segment).count(), 4)
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 0)


