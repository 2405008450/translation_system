from __future__ import annotations

import json
import unittest
from datetime import datetime
from uuid import UUID
from types import SimpleNamespace
from unittest.mock import patch

from app.models import SegmentRevision
from app.services.auto_write_protection import set_auto_write_protection
from app.services.proofreading import _review_group_batch, _pack_groups, _build_prompt
from app.services.proofreading_context import pack_readonly_context
from app.routers.api import (_llm_segment_should_translate, _get_llm_write_skip_reason,
                             SegmentProtectionUpdate, update_segment_auto_write_protection)
import test_workflow_proofreading as workflow_fixture
import test_auto_tm_sync as tm_fixture


class AutoWriteProtectionTests(workflow_fixture.WorkflowTestCase):
    setup_workflow = workflow_fixture.TranslationWorkflowProofreadingTests.setup_workflow
    run_generation = workflow_fixture.TranslationWorkflowProofreadingTests.run_generation
    assign = workflow_fixture.TranslationWorkflowProofreadingTests.assign

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.models import FileSegmentStats, SegmentComment, SegmentQAIssue, ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask
        for model in (FileSegmentStats, SegmentComment, SegmentQAIssue, ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask):
            saved = [(column, column.server_default) for column in model.__table__.columns
                     if column.server_default is not None and '::uuid' in str(column.server_default.arg)]
            try:
                for column, _ in saved:
                    column.server_default = None
                model.__table__.create(self.engine)
            finally:
                for column, default in saved:
                    column.server_default = default

    async def test_protected_repeat_is_context_only_and_editable_repeats_are_independent(self):
        batch = self.setup_workflow()
        for segment in self.segments[:3]:
            segment.source_text = "Same source"
            segment.source_hash = "same"
        protected = self.segments[0]
        set_auto_write_protection(protected, "protected", reason="tm_exact")
        self.db.commit()
        before = (protected.target_text, protected.version, protected.source)
        await self.run_generation(batch)
        self.assertEqual(len(self.last_groups), 2)
        self.assertEqual(len({group['sid'] for group in self.last_groups}), 2)
        self.assertTrue(any(str(protected.id) == context['sid'] for group in self.last_groups
                            for context in group['readonly_context']))
        self.assertEqual((protected.target_text, protected.version, protected.source), before)
        self.assertEqual(self.db.query(SegmentRevision).filter_by(segment_id=protected.id).count(), 0)
        self.assertEqual(json.loads(batch.config_json)['skipped_protected_segments'], 1)

    async def test_new_protection_during_request_prevents_writeback(self):
        batch = self.setup_workflow()
        protected = self.segments[0]
        before = protected.target_text
        def protect():
            set_auto_write_protection(protected, "protected", reason="manual", actor_id=self.user.id)
            self.db.commit()
        await self.run_generation(batch, callback=protect)
        self.assertEqual(protected.target_text, before)
        self.assertEqual(protected.auto_write_protection, "protected")
        self.assertEqual(self.db.query(SegmentRevision).filter_by(segment_id=protected.id).count(), 0)

    async def test_all_protected_completes_without_model_call(self):
        batch = self.setup_workflow()
        for segment in self.segments:
            set_auto_write_protection(segment, "protected", reason="manual")
        self.db.commit()
        with patch('app.services.proofreading._review_group_batch') as model:
            from app.services.proofreading import generate_batch
            await generate_batch(self.db, batch, current_user=self.user)
            model.assert_not_called()
        self.assertEqual(batch.status, "completed")
        self.assertEqual(batch.progress, 100)
        self.assertIn("没有需要校对", batch.message)

    def test_scope_never_overrides_protection_and_released_allows_legacy_exact(self):
        self.setup_workflow()
        segment = self.segments[0]
        segment.source = "tm"
        segment.score = 1
        segment.status = "exact"
        set_auto_write_protection(segment, "protected", reason="tm_exact")
        self.assertFalse(_llm_segment_should_translate(segment, "all_with_exact"))
        self.assertFalse(_llm_segment_should_translate(segment, "current_segment", sentence_id=segment.sentence_id))
        base = segment.version
        set_auto_write_protection(segment, "released", reason="manual")
        task = SimpleNamespace(base_version=base, source_text=segment.source_text, base_target_text=segment.target_text)
        self.assertIsNotNone(_get_llm_write_skip_reason(segment, task, "all_with_exact"))
        self.assertTrue(_llm_segment_should_translate(segment, "all_with_exact"))

    def test_endpoint_checks_versions_and_does_not_edit_translation(self):
        self.setup_workflow()
        first, second = self.segments[:2]
        payload = SegmentProtectionUpdate(state="protected", items=[
            {"segment_id": first.id, "expected_version": first.version},
            {"segment_id": second.id, "expected_version": second.version + 1},
        ])
        before = first.target_text
        with patch('app.routers.api.publish_segment_changes'):
            result = update_segment_auto_write_protection(self.file.id, payload, self.db, self.user, None)
        self.assertEqual(result['updated_count'], 1)
        self.assertEqual(result['items'][1]['error'], 'version_conflict')
        self.assertEqual(first.target_text, before)
        self.assertEqual(first.auto_write_protection, 'protected')
        self.assertEqual(second.auto_write_protection, 'none')

    async def test_context_does_not_leak_outside_assignment(self):
        batch = self.setup_workflow()
        user, _ = self.assign('limited-context', 2, 2)
        await self.run_generation(batch, user=user)
        self.assertEqual(len(self.last_groups), 1)
        self.assertEqual(self.last_groups[0]['readonly_context'], [])

    async def test_unknown_sid_is_not_applied_via_valid_seq(self):
        group = {'sid': 'editable', 'source_text': 'Hello', 'variants': ['Bonjour'], 'segments': []}
        completion = SimpleNamespace(provider='test', model='test', content=json.dumps([
            {'sid': 'editable', 'seq': 99, 'reviewed_target_text': 'Bonjour !'},
            {'sid': 'readonly', 'seq': 0, 'reviewed_target_text': 'Injected'},
        ]))
        with patch('app.services.proofreading.request_chat_completion', return_value=completion):
            results, _, _ = await _review_group_batch([group], source_language='en-US', target_language='fr-FR',
                                                      rules_text='', provider='auto', model=None)
        self.assertEqual(set(results), {'editable'})
        self.assertEqual(results['editable']['reviewed_target_text'], 'Bonjour !')

    def test_context_is_deduplicated_and_budgeted(self):
        contexts = [{'sid': str(i), 'offset': 1 if i % 2 else -2, 'source_text': 'x' * 1000,
                     'target_text': 'y' * 1000} for i in range(8)]
        groups = [{'sid': str(i + 10), 'source_text': 'Hello', 'variants': ['Bonjour'],
                   'readonly_context': contexts} for i in range(20)]
        context = pack_readonly_context(groups)
        ids = [item['sid'] for item in context['readonly_context']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(sum(len(json.dumps(item, ensure_ascii=False)) for item in context['readonly_context']), 6000)
        self.assertTrue(all(len(packed) <= 20 for packed in _pack_groups(groups)))
        self.assertIn('readonly_context', _build_prompt(groups[:1], 'en-US', 'fr-FR', ''))

    def test_manual_edit_retains_protection(self):
        from app.services.file_record_service import update_segment_by_sentence_id
        self.setup_workflow()
        segment = self.segments[0]
        set_auto_write_protection(segment, 'protected', reason='tm_exact')
        self.db.commit()
        with patch('app.services.review_sync.record_edit'), patch('app.services.file_record_service.record_translation_metric_event'), \
                patch('app.services.file_record_service.sync_file_record_status'):
            update_segment_by_sentence_id(self.db, self.file.id, segment.sentence_id, '人工修订',
                                          current_user=self.user, track_revision=False)
        self.assertEqual(segment.target_text, '人工修订')
        self.assertEqual(segment.auto_write_protection, 'protected')

    def test_ai_format_application_cannot_change_protected_layout(self):
        from fastapi import HTTPException
        from app.services.style_tag_check_service import apply_style_tag_check_item
        self.setup_workflow()
        segment = self.segments[0]
        set_auto_write_protection(segment, 'protected', reason='manual')
        self.db.commit()
        before = segment.segment_metadata
        item = SimpleNamespace(suggested_target_layout_text=segment.target_text)
        with patch('app.services.style_tag_check_service._get_item_segment', return_value=segment):
            with self.assertRaises(HTTPException) as error:
                apply_style_tag_check_item(self.db, item)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(segment.segment_metadata, before)

    def test_permission_conflict_is_reported_per_item(self):
        self.setup_workflow()
        user, _ = self.assign('restricted-protection', 2, 2)
        first, second = self.segments[:2]
        payload = SegmentProtectionUpdate(state='protected', items=[
            {'segment_id': segment.id, 'expected_version': segment.version} for segment in (first, second)
        ])
        with patch('app.routers.api.publish_segment_changes'):
            result = update_segment_auto_write_protection(self.file.id, payload, self.db, user, None)
        self.assertEqual(result['items'][0]['error'], 'no_write_access')
        self.assertEqual(result['updated_count'], 1)
        self.assertEqual(first.auto_write_protection, 'none')

    def test_workflow_transitions_keep_protection_when_confirmation_resets(self):
        from app.routers import api
        self.setup_workflow()
        segment = self.segments[0]
        segment.workflow_step_id = self.translation_step.id
        segment.status = 'confirmed'
        set_auto_write_protection(segment, 'protected', reason='tm_exact')
        self.db.commit()
        for source_step, target_step in [(self.translation_step, self.review_step),
                                         (self.review_step, self.proofread_step)]:
            api._advance_assignment_workflow_transitions(self.db, [{
                'file_record_id': str(self.file.id), 'from_step': {'id': str(source_step.id)},
                'target_step': {'id': str(target_step.id)}, 'ranges': [{'range_start': None, 'range_end': None}],
            }], self.user)
            self.db.commit()
            self.assertEqual(segment.auto_write_protection, 'protected')
            self.assertNotEqual(segment.status, 'confirmed')

    def test_split_and_merge_inherit_protection(self):
        from app.routers import api
        from app.models import Segment
        self.setup_workflow()
        segment = self.segments[0]
        set_auto_write_protection(segment, 'protected', reason='manual')
        self.db.commit()
        with patch.object(api, 'publish_segment_changes'):
            result = api.split_segment(self.file.id, segment.sentence_id,
                                       api.SegmentSplitRequest(split_offset=3), self.db, self.user, None)
            second = self.db.query(Segment).filter_by(id=UUID(result['second']['id'])).one()
            self.assertEqual(second.auto_write_protection, 'protected')
            set_auto_write_protection(segment, 'released', reason='manual')
            self.db.commit()
            api.merge_segment(self.file.id, segment.sentence_id,
                              api.SegmentMergeRequest(target_sentence_id=second.sentence_id), self.db, self.user, None)
        self.assertEqual(segment.auto_write_protection, 'protected')


class TMProtectionTests(unittest.TestCase):
    _session = tm_fixture.AutoTMSyncTests._session

    def setup_data(self, db):
        from app.models import MemoryBase, MemoryEntry, FileRecord, Segment, User
        collection = MemoryBase(name='Protection TM', source_language='en-US', target_language='fr-FR')
        user = User(username='protection-admin', hashed_password='x', role='admin')
        db.add_all([collection, user])
        db.flush()
        file = FileRecord(filename='protection.txt', source_language='en-US', target_language='fr-FR',
                          collection_id=collection.id, collection_ids_json=json.dumps([str(collection.id)]))
        entry = MemoryEntry(collection_id=collection.id, source_text='Hello world', target_text='Bonjour',
                            source_language='en-US', target_language='fr-FR')
        segment = Segment(file_record=file, sentence_id='s1', source_text='Hello world', display_text='Hello world',
                          target_text='Bonjour', source='tm', status='exact', score=1,
                          matched_source_text='Hello world', matched_updated_at=datetime(2026, 10, 1))
        entry.updated_at = datetime(2026, 10, 1)
        db.add_all([file, entry, segment])
        db.commit()
        return file, entry, segment, user

    def test_rematch_protects_exact_with_both_confirmation_options(self):
        from app.routers import api
        for confirm in (False, True):
            with self.subTest(confirm=confirm), self._session() as db:
                file, entry, segment, user = self.setup_data(db)
                segment.source = 'none'
                segment.target_text = ''
                segment.status = 'none'
                db.commit()
                match = SimpleNamespace(status='exact', score=1, target_text='Bonjour', matched_source_text='Hello world',
                                        matched_collection_name='Protection TM', matched_creator_name=None,
                                        matched_created_at=None, matched_updated_at=None)
                with patch.object(api, 'match_sentences_with_stats', return_value=([match], {})):
                    result = api.rematch_file_record(file.id, api.RematchRequest(
                        collection_ids=[entry.collection_id], auto_confirm_exact=confirm,
                    ), db, user, None)
                self.assertEqual(segment.auto_write_protection, 'protected')
                self.assertEqual(segment.status, 'confirmed' if confirm else 'exact')
                self.assertEqual(result['exact'], 1)
                # 后续重新匹配不能覆盖，即使取消“跳过已确认”。
                with patch.object(api, 'match_sentences_with_stats') as matcher:
                    result = api.rematch_file_record(file.id, api.RematchRequest(
                        collection_ids=[entry.collection_id], skip_confirmed=False,
                    ), db, user, None)
                matcher.assert_not_called()
                self.assertEqual(result['skipped_protected_segments'], 1)
                self.assertEqual(segment.target_text, 'Bonjour')

    def test_backfill_requires_unique_unchanged_evidence_and_respects_release(self):
        from app.services.protection_backfill import inspect_backfill_candidate
        with self._session() as db:
            file, entry, segment, _ = self.setup_data(db)
            self.assertTrue(inspect_backfill_candidate(db, segment, file)['eligible'])
            entry.target_text = 'Changed'
            db.flush()
            self.assertEqual(inspect_backfill_candidate(db, segment, file)['reason'], 'tm_translation_changed')
            entry.target_text = segment.target_text
            entry.updated_at = datetime(2026, 10, 2)
            db.flush()
            self.assertEqual(inspect_backfill_candidate(db, segment, file)['reason'], 'tm_revision_unverifiable')
            set_auto_write_protection(segment, 'released', reason='manual')
            self.assertEqual(inspect_backfill_candidate(db, segment, file)['reason'], 'protection_already_decided')

    def test_backfill_rejects_ambiguous_entries(self):
        from app.models import MemoryBase, MemoryEntry
        from app.services.protection_backfill import inspect_backfill_candidate
        with self._session() as db:
            file, entry, segment, _ = self.setup_data(db)
            second = MemoryBase(name='Another TM', source_language='en-US', target_language='fr-FR')
            db.add(second)
            db.flush()
            file.collection_ids_json = json.dumps([str(entry.collection_id), str(second.id)])
            db.add(MemoryEntry(collection_id=second.id, source_text=entry.source_text,
                               target_text=entry.target_text, source_language='en-US', target_language='fr-FR'))
            db.flush()
            self.assertEqual(inspect_backfill_candidate(db, segment, file)['reason'], 'missing_or_ambiguous_tm_entry')

    def test_sync_and_background_tm_do_not_mutate_protected_target(self):
        from app.services.auto_tm_sync import refresh_unconfirmed_segment_matches
        from app.services.project_segment_sync import _is_safe_project_sync_target, _clear_project_synced_segment_fields
        with self._session() as db:
            file, _, segment, _ = self.setup_data(db)
            set_auto_write_protection(segment, 'protected', reason='tm_exact')
            db.commit()
            before = (segment.target_text, segment.version, segment.source, segment.score)
            with patch('app.services.auto_tm_sync.match_sentences_with_stats', return_value=([
                SimpleNamespace(status='exact')
            ], {})):
                refresh_unconfirmed_segment_matches(db, file_record_id=file.id, collection_ids=[file.collection_id])
            self.assertEqual((segment.target_text, segment.version, segment.source, segment.score), before)
            self.assertFalse(_is_safe_project_sync_target(segment))
            self.assertFalse(_clear_project_synced_segment_fields(segment))
            self.assertEqual(segment.target_text, 'Bonjour')
