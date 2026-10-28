"""合并视图校对：真实数据库与校对写回，模型和队列使用可控替身。"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch, AsyncMock
from uuid import UUID
from zipfile import ZipFile

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import test_merge_workflow_transition as fixtures
from app.models import (FileRecord, FileExportTask, Segment, ProofreadingBatch, ProofreadingColumnBinding,
                        ProofreadingSegmentBaseline, MergeProofreadingRun, MergeProofreadingItem)
from app.routers import proofreading as routes
from app.services import merge_proofreading as service
from app.services.workflow_proofreading import proofreading_fingerprint, segment_already_reviewed, prepare_workflow_proofreading


class Queue:
    def __init__(self):
        self.jobs = []
        self.ids = set()

    async def enqueue_job(self, name, *args, **kwargs):
        key = kwargs.get('_job_id')
        if key and key in self.ids:
            return None
        if key:
            self.ids.add(key)
        self.jobs.append((name, args, key))
        return True


class MergeProofreadingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.MergeWorkflowTransitionTests()
        with patch.object(fixtures, 'create_engine', side_effect=lambda *_: create_engine(
            'sqlite:///:memory:', poolclass=StaticPool, connect_args={'check_same_thread': False})):
            self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db, self.engine = self.fixture.db, self.fixture.engine
        for model in (FileExportTask, MergeProofreadingRun, MergeProofreadingItem):
            column = model.__table__.c.id
            default = column.server_default
            column.server_default = None
            try:
                model.__table__.create(self.engine)
            finally:
                column.server_default = default
        self.sessions = sessionmaker(bind=self.engine)
        self.file_ids = [file.id for file in self.fixture.files]
        third = FileRecord(project_id=self.fixture.project.id, filename=self.fixture.files[0].filename,
                           source_language='en-US', target_language='fr-FR')
        self.db.add(third)
        self.db.flush()
        self.file_ids.append(third.id)
        for index in range(2):
            self.db.add(Segment(file_record_id=third.id, workflow_step_id=self.fixture.source.id,
                sentence_id=f's{index}', source_text=f'Source {index}', display_text=f'Source {index}',
                target_text=f'Traduction {index}', display_index=index, sequence_index=index,
                status='none', source='manual', version=1))
        self.fixture.view.file_ids = json.dumps([str(value) for value in self.file_ids])
        self.db.commit()
        self.fixture.submit()
        self.view_id, self.user_id = self.fixture.view.id, self.fixture.user.id
        self.queue = Queue()
        self.ctx = {'redis': self.queue}
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patches = [
            patch.object(service, 'SessionLocal', self.sessions),
            patch('app.services.proofreading.SessionLocal', self.sessions),
            patch('app.services.proofreading._review_group_batch', side_effect=self.review),
            patch('app.services.proofreading._attach_reference_context'),
            patch('app.services.proofreading.record_translation_metric_event'),
            patch('app.services.llm_service.validate_provider_choice'),
            patch.object(routes, 'validate_provider_choice'),
            patch.object(service, 'get_settings', return_value=SimpleNamespace(export_task_dir=self.directory.name)),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.model_calls = []
        self.unchanged = False
        self.fail_file = None

    async def review(self, groups, **kwargs):
        self.model_calls.append([group['sid'] for group in groups])
        if self.fail_file and any(group['file_record_id'] == self.fail_file for group in groups):
            raise RuntimeError('模拟模型失败')
        return ({group['sid']: {'reviewed_target_text': group['variants'][0] if self.unchanged else 'Texte corrige',
                'category': 'accuracy', 'reason': 'Test reason', 'confidence': 'high'} for group in groups}, 'test', 'test-model')

    def options(self, **kwargs):
        return {'provider': 'deepseek', 'model': 'deepseek-chat', 'user_instructions': 'test',
                'retry_scope': 'unreviewed', **kwargs}

    def create(self, options=None, kind='proofreading'):
        self.db.expire_all()
        return service.create_run(self.db, self.view_id, self.fixture.user, options or self.options(), kind)

    async def drain(self, run_id):
        await service.merge_proofreading_tick(self.ctx, str(run_id))
        for _ in range(20):
            if not self.queue.jobs:
                break
            name, args, key = self.queue.jobs.pop(0)
            await getattr(service, name)(self.ctx, *args)
            self.queue.ids.discard(key)
        self.db.expire_all()
        return self.db.get(MergeProofreadingRun, run_id)

    async def test_three_files_and_default_does_not_repeat_success(self):
        run = self.create()
        run = await self.drain(run.id)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(len(self.model_calls), 3)
        self.assertTrue(all(segment.target_text == 'Texte corrige' for segment in self.db.query(Segment)))
        preview = service.preview(self.db, self.view_id, self.fixture.user, self.options())
        self.assertEqual((preview['pending'], preview['reviewed']), (0, 6))
        self.assertEqual([item.status for item in self.db.query(MergeProofreadingItem).filter_by(run_id=run.id)], ['completed'] * 3)
        with self.assertRaises(HTTPException):
            self.create()
        self.assertEqual(len(self.model_calls), 3)

    async def test_unchanged_success_and_manual_change_reentry(self):
        self.unchanged = True
        run = self.create()
        await self.drain(run.id)
        self.assertEqual(service.preview(self.db, self.view_id, self.fixture.user, self.options())['pending'], 0)
        segment = self.db.query(Segment).first()
        baseline = self.db.query(ProofreadingSegmentBaseline).filter_by(segment_id=segment.id).one()
        self.assertTrue(segment_already_reviewed(segment, baseline))
        segment.target_text = 'Manual adjustment'
        segment.version += 1
        self.db.commit()
        self.assertEqual(service.preview(self.db, self.view_id, self.fixture.user, self.options())['pending'], 1)
        prepare_workflow_proofreading(self.db, self.db.get(FileRecord, segment.file_record_id), self.fixture.user, refresh_segment_ids={segment.id})
        self.assertIsNone(baseline.reviewed_fingerprint)

    async def test_current_file_scope_and_duplicate_start(self):
        options = self.options(file_id=str(self.file_ids[1]))
        run = self.create(options)
        with self.assertRaises(HTTPException) as error:
            self.create(options)
        self.assertEqual(error.exception.status_code, 409)
        await self.drain(run.id)
        self.assertEqual(len(self.model_calls), 1)
        self.assertTrue(all(s.target_text == 'Texte corrige' for s in self.db.query(Segment).filter_by(file_record_id=self.file_ids[1])))
        self.assertEqual(service.preview(self.db, self.view_id, self.fixture.user, self.options())['pending'], 4)

    async def test_protection_and_locked_file_rechecked_before_execution(self):
        segment = self.db.query(Segment).filter_by(file_record_id=self.file_ids[0]).first()
        segment.auto_write_protection = 'protected'
        segment.status = 'confirmed'
        self.db.commit()
        preview = service.preview(self.db, self.view_id, self.fixture.user, self.options())
        self.assertEqual(preview['protected'], 1)
        run = self.create()
        self.fixture.lock(self.db.get(FileRecord, self.file_ids[1]))
        result = await self.drain(run.id)
        self.assertEqual(result.status, 'partial_failed')
        self.assertEqual(len(self.model_calls), 2)
        self.db.refresh(segment)
        self.assertEqual(segment.target_text, 'Traduction 0')
        item = self.db.query(MergeProofreadingItem).filter_by(run_id=run.id, file_record_id=self.file_ids[1]).one()
        self.assertEqual(item.status, 'skipped')
        self.assertIn('锁定', item.message)

    async def test_failure_continues_other_files_and_failed_only_retries(self):
        self.fail_file = self.file_ids[1]
        run = self.create()
        result = await self.drain(run.id)
        self.assertEqual(result.status, 'partial_failed')
        self.assertEqual(len(self.model_calls), 3)
        self.fail_file = None
        retry = self.create(self.options(retry_scope='failed_only'))
        await self.drain(retry.id)
        self.assertEqual(len(self.model_calls), 4)
        self.assertEqual(service.preview(self.db, self.view_id, self.fixture.user, self.options())['pending'], 0)

    async def test_cancel_before_start_and_no_model_call(self):
        run = self.create()
        await service.merge_proofreading_tick(self.ctx, str(run.id))
        self.db.expire_all()
        service.cancel_run(self.db, self.db.get(MergeProofreadingRun, run.id), self.fixture.user)
        result = await self.drain(run.id)
        self.assertEqual(result.status, 'canceled')
        self.assertEqual(self.model_calls, [])

    async def test_duplicate_child_delivery_does_not_call_twice(self):
        run = self.create()
        await service.merge_proofreading_tick(self.ctx, str(run.id))
        item = self.db.query(MergeProofreadingItem).filter_by(run_id=run.id, status='dispatched').one()
        await asyncio.to_thread(service.execute_item, str(item.id))
        await asyncio.to_thread(service.execute_item, str(item.id))
        self.assertEqual(len(self.model_calls), 1)

    async def test_context_loads_baselines_for_all_requested_files(self):
        response = routes.merge_proofreading_context(self.view_id, ','.join(map(str, self.file_ids)), self.db, self.fixture.user)
        self.assertEqual(len(response['files']), 3)
        self.assertEqual(sum(len(file['items']) for file in response['files']), 6)
        batch_ids = {file['proofreading_context']['batch_id'] for file in response['files']}
        self.assertEqual(len(batch_ids), 3)

    async def test_archive_unique_names_and_partial_failure_manifest(self):
        readiness = {'available_formats': ['proofreading_audit_xlsx'], 'unavailable_formats': {}, 'has_warnings': False}
        def queue(db, *, file_record_id, export_type, current_user, **kwargs):
            task = FileExportTask(file_record_id=file_record_id, export_type=export_type,
                created_by_id=current_user.id, expires_at=datetime.now() + timedelta(hours=24), status='queued')
            db.add(task)
            db.commit()
            return {'task_id': str(task.id)}
        def export(task_id):
            with self.sessions() as db:
                task = db.get(FileExportTask, task_id)
                if task.file_record_id == self.file_ids[1]:
                    task.status, task.error = 'failed', '模拟导出失败'
                else:
                    output = Path(self.directory.name) / f'{task.id}.xlsx'
                    output.write_bytes(b'exported content')
                    task.status, task.filename, task.result_path = 'completed', '同名.xlsx', str(output)
                db.commit()
        with patch.object(service, 'build_export_readiness', return_value=readiness),              patch('app.services.file_export_queue.queue_file_export', side_effect=queue),              patch('app.services.file_export_queue.run_file_export_task', side_effect=export):
            run = self.create({'format': 'proofreading_audit_xlsx', 'acknowledge_warnings': True}, 'export')
            result = await self.drain(run.id)
        self.assertEqual(result.status, 'partial_failed')
        with ZipFile(result.result_path) as archive:
            names = archive.namelist()
            self.assertEqual(len(set(names)), 3)
            self.assertEqual(len([name for name in names if name.endswith('.xlsx')]), 2)
            manifest = json.loads(archive.read('导出结果清单.json'))
            self.assertEqual([item['status'] for item in manifest], ['completed', 'failed', 'completed'])
        response = routes.download_merge_proofreading_export(self.view_id, run.id, self.db, self.fixture.user)
        self.assertEqual(response.media_type, 'application/zip')
        result.created_at = service.now() - timedelta(days=2)
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            routes.download_merge_proofreading_export(self.view_id, run.id, self.db, self.fixture.user)
        self.assertEqual(error.exception.status_code, 410)

    async def test_interrupted_child_is_failed_without_automatic_model_retry(self):
        run = self.create()
        item = self.db.query(MergeProofreadingItem).filter_by(run_id=run.id).order_by(MergeProofreadingItem.sort_order).first()
        item.status, item.child_run_id = 'running', 'interrupted-run'
        item.updated_at = service.now() - timedelta(minutes=1)
        batch = self.db.get(ProofreadingBatch, item.batch_id)
        ids = [str(s.id) for s in self.db.query(Segment).filter_by(file_record_id=item.file_record_id)]
        batch.config_json = json.dumps({'workflow_step_id': str(self.fixture.target.id),
            'generation': {'run_id': item.child_run_id, 'merge_run_id': str(run.id), 'segment_ids': ids}})
        batch.status = 'running'
        self.db.commit()
        arq, jobs = ModuleType('arq'), ModuleType('arq.jobs')
        jobs.JobStatus = SimpleNamespace(not_found='not_found', complete='complete')
        jobs.Job = lambda *args, **kwargs: SimpleNamespace(status=AsyncMock(return_value='not_found'))
        with patch.dict('sys.modules', {'arq': arq, 'arq.jobs': jobs}):
            await service.merge_proofreading_tick(self.ctx, str(run.id))
        self.db.expire_all()
        self.assertEqual(self.db.get(MergeProofreadingItem, item.id).status, 'failed')
        self.assertEqual(self.db.get(ProofreadingBatch, batch.id).status, 'failed')
        self.assertEqual(self.model_calls, [])
        result = service.preview(self.db, self.view_id, self.fixture.user, self.options(retry_scope='failed_only'))
        self.assertEqual(result['pending'], 2)

    async def test_cancel_does_not_cancel_someone_elses_batch(self):
        run = self.create()
        item = self.db.query(MergeProofreadingItem).filter_by(run_id=run.id).first()
        item.status, item.child_run_id = 'running', 'owned-run'
        batch = self.db.get(ProofreadingBatch, item.batch_id)
        batch.status, batch.config_json = 'running', json.dumps({'generation': {'run_id': 'other-run', 'merge_run_id': 'other'}})
        self.db.commit()
        service.cancel_run(self.db, run, self.fixture.user)
        self.assertEqual(batch.status, 'running')
        self.assertFalse(batch.cancel_requested)

    async def test_unsupported_export_preview_and_warning_confirmation(self):
        readiness = {'available_formats': ['proofreading_docx_revisions'], 'unavailable_formats': {}, 'has_warnings': True}
        with patch.object(service, 'build_export_readiness', return_value=readiness):
            preview = service.preview(self.db, self.view_id, self.fixture.user, {}, 'export')
            self.assertEqual(preview['format_file_counts']['proofreading_docx_revisions'], 3)
            with self.assertRaises(HTTPException) as error:
                self.create({'format': 'proofreading_docx_revisions'}, 'export')
            self.assertEqual(error.exception.status_code, 409)
            with self.assertRaises(HTTPException):
                self.create({'format': 'proofreading_audit_xlsx'}, 'export')

    async def test_download_denied_when_any_original_file_becomes_invisible(self):
        run = self.create()
        run.kind, run.status = 'export', 'completed'
        output = Path(self.directory.name) / 'protected.zip'
        output.write_bytes(b'archive')
        run.result_path = str(output)
        self.db.commit()
        with patch.object(service, 'get_run', return_value=(run, {self.file_ids[0]})):
            with self.assertRaises(HTTPException) as error:
                routes.download_merge_proofreading_export(self.view_id, run.id, self.db, self.fixture.user)
        self.assertEqual(error.exception.status_code, 403)

    async def test_all_export_failures_do_not_create_empty_archive(self):
        run = self.create()
        run.kind = 'export'
        for item in self.db.query(MergeProofreadingItem).filter_by(run_id=run.id):
            item.status = 'failed'
        self.db.commit()
        service.finalize(self.db, run)
        self.assertEqual(run.status, 'failed')
        self.assertIsNone(run.result_path)


if __name__ == '__main__':
    unittest.main()
