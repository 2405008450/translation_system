"""PostgreSQL 集成测试，所有数据写入随机隔离 schema。

运行：设置 REVIEW_SYNC_TEST_DATABASE_URL 后执行 python -m unittest discover -s scripts -p test_review_sync.py -v。
测试账号须能创建 schema；不会读写现有业务表。
"""
from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import FileRecord, Project, ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask, Segment, SegmentRevision, User
from app.services.file_record_service import batch_update_segments
from app.services.normalizer import build_source_hash
from app.services.review_sync import active_member, enqueue, lock_project, process_task, resolve_group


@unittest.skipUnless(os.environ.get("REVIEW_SYNC_TEST_DATABASE_URL"), "需要显式指定 PostgreSQL 测试连接")
class ReviewSyncIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "review_sync_test_" + uuid4().hex
        url = os.environ["REVIEW_SYNC_TEST_DATABASE_URL"]
        cls.admin_engine = create_engine(url)
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.engine = create_engine(url, connect_args={"options": f"-csearch_path={cls.schema}"})
        cls.addClassCleanup(cls.cleanup_schema)
        with cls.engine.begin() as connection:
            connection.execute(text(f'CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA "{cls.schema}"'))
            connection.execute(text(f'CREATE EXTENSION IF NOT EXISTS btree_gin WITH SCHEMA "{cls.schema}"'))
        with cls.engine.connect() as connection:
            assert connection.execute(text("SELECT current_schema()")).scalar() == cls.schema
        Base.metadata.create_all(cls.engine)

    @classmethod
    def cleanup_schema(cls):
        cls.engine.dispose()
        assert cls.schema.startswith("review_sync_test_") and len(cls.schema) == 49
        with cls.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.admin_engine.dispose()

    def setUp(self):
        # 必须与生产 SessionLocal 一致，避免查询隐式 flush 掩盖同步分流缺陷。
        self.db = Session(self.engine, autoflush=False)
        self.addCleanup(self.db.close)
        self.publish = patch("app.services.review_sync.publish_segment_changes")
        self.publish.start()
        self.addCleanup(self.publish.stop)
        self.user = User(username=uuid4().hex, hashed_password="x", role="admin")
        self.project = Project(name="修订测试", review_sync_enabled=True)
        self.db.add_all([self.user, self.project])
        self.db.flush()
        self.files = [FileRecord(project_id=self.project.id, filename=f"{i}.txt", source_language="zh-CN", target_language="en-US") for i in range(2)]
        self.db.add_all(self.files)
        self.db.flush()
        self.source = self.make_segment(self.files[0], "s1")
        self.target = self.make_segment(self.files[1], "s1")
        self.same_file = self.make_segment(self.files[0], "s2")
        self.db.commit()

    def make_segment(self, file, sentence, value="A", **kwargs):
        segment = Segment(file_record_id=file.id, sentence_id=sentence, source_text="相同原文",
                          display_text="相同原文", source_hash=build_source_hash("相同原文"),
                          target_text=value, source="manual", status="confirmed", version=1, **kwargs)
        self.db.add(segment)
        self.db.flush()
        return segment

    def edit(self, value, segment=None):
        segment = segment or self.source
        result = batch_update_segments(self.db, segment.file_record_id, [{"sentence_id": segment.sentence_id,
            "target_text": value, "source": "manual", "track_revision": True, "base_version": segment.version}],
            current_user=self.user, return_result=True)
        self.assertEqual(result.updated_count, 1)
        return active_member(self.db, segment.id)

    def queue(self):
        member = active_member(self.db, self.source.id)
        task = enqueue(self.db, self.source, member.group_id, self.source.version, self.user)
        self.db.commit()
        return task

    def propagate(self):
        task = self.queue()
        result = process_task(self.db, task.id)
        self.db.expire_all()
        return result

    def test_cross_file_confirmed_manual_revisions_and_reject(self):
        self.edit("B")
        result = self.propagate()
        self.assertEqual(result["updated_count"], 2)
        for segment in (self.source, self.target, self.same_file):
            self.assertEqual(segment.target_text, "B")
            self.assertNotEqual(segment.status, "confirmed")
            member = active_member(self.db, segment.id)
            revision = self.db.get(SegmentRevision, member.revision_id)
            self.assertEqual((revision.before_text, revision.after_text), ("A", "B"))
        revision = self.db.get(SegmentRevision, active_member(self.db, self.target.id).revision_id)
        result = resolve_group(self.db, revision, "rejected", self.user)
        self.db.commit()
        self.assertEqual(result["updated_count"], 3)
        self.assertEqual([s.target_text for s in (self.source, self.target, self.same_file)], ["A"] * 3)

    def test_first_input_into_empty_translation_creates_group_before_confirmation(self):
        for segment in (self.source, self.target, self.same_file):
            segment.target_text = ""
            segment.status = "none"
        self.db.commit()
        member = self.edit("First translation")
        self.assertIsNotNone(member)
        revision = self.db.get(SegmentRevision, member.revision_id)
        self.assertEqual(revision.before_text, "")
        self.assertEqual(self.propagate()["updated_count"], 2)
        self.assertEqual([s.target_text for s in (self.source, self.target, self.same_file)],
                         ["First translation"] * 3)

    def test_continuous_autosaves_keep_first_baseline_and_are_idempotent(self):
        self.edit("B")
        self.edit("C")
        self.propagate()
        self.edit("D")
        self.propagate()
        count = self.db.query(SegmentRevision).filter(SegmentRevision.file_record_id.in_([f.id for f in self.files])).count()
        self.assertEqual(count, 3)
        revision = self.db.get(SegmentRevision, active_member(self.db, self.target.id).revision_id)
        self.assertEqual((revision.before_text, revision.after_text), ("A", "D"))
        task = self.queue()
        self.assertIsNone(process_task(self.db, task.id))

    def test_old_text_and_independent_revision_disabled_language_and_permission(self):
        different = self.make_segment(self.files[1], "different", "A ")
        disabled = self.make_segment(self.files[1], "disabled", project_sync_disabled=True)
        independent = self.make_segment(self.files[1], "independent")
        self.db.add(SegmentRevision(file_record_id=self.files[1].id, segment_id=independent.id,
                    sentence_id=independent.sentence_id, before_text="old", after_text="A", source="manual", status="pending"))
        foreign_file = FileRecord(project_id=self.project.id, filename="fr.txt", source_language="zh-CN", target_language="fr-FR")
        self.db.add(foreign_file)
        self.db.flush()
        foreign = self.make_segment(foreign_file, "s1")
        denied = self.make_segment(self.files[1], "denied")
        self.db.commit()
        self.edit("B")
        from app.services.review_sync import can_write
        with patch("app.services.review_sync.can_write", side_effect=lambda db, s, u: s.id != denied.id and can_write(db, s, u)):
            result = self.propagate()
        self.assertEqual(result["skipped_count"], 4)
        self.assertEqual(different.target_text, "A ")
        for segment in (disabled, independent, foreign, denied):
            self.assertEqual(segment.target_text, "A")

    def test_independent_edit_detaches_and_survives_reject(self):
        self.edit("B")
        self.propagate()
        group_id = active_member(self.db, self.source.id).group_id
        self.edit("custom", self.target)
        self.edit("C")
        result = self.propagate()
        self.assertEqual(result["reasons"]["independently_modified"], 1)
        self.assertEqual(self.target.target_text, "custom")
        revision = self.db.get(SegmentRevision, active_member(self.db, self.source.id).revision_id)
        resolve_group(self.db, revision, "rejected", self.user)
        self.db.commit()
        self.assertEqual(self.target.target_text, "custom")
        self.assertEqual(self.db.get(ReviewSyncGroup, group_id).status, "rejected")

    def test_revert_to_baseline_and_new_group_after_accept(self):
        self.edit("B")
        self.propagate()
        self.edit("A")
        self.propagate()
        self.assertEqual(self.target.target_text, "A")
        self.assertIsNone(active_member(self.db, self.source.id))
        self.edit("C")
        self.propagate()
        old_group = active_member(self.db, self.source.id).group_id
        revision = self.db.get(SegmentRevision, active_member(self.db, self.source.id).revision_id)
        resolve_group(self.db, revision, "accepted", self.user)
        self.db.commit()
        self.edit("D")
        self.assertNotEqual(active_member(self.db, self.source.id).group_id, old_group)

    def test_accept_before_worker_cancels_task_and_disable_preserves_resolution(self):
        self.edit("B")
        task = self.queue()
        revision = self.db.get(SegmentRevision, active_member(self.db, self.source.id).revision_id)
        resolve_group(self.db, revision, "rejected", self.user)
        self.db.commit()
        self.assertIsNone(process_task(self.db, task.id))
        self.assertEqual(self.target.target_text, "A")
        self.edit("C")
        self.propagate()
        self.project.review_sync_enabled = False
        self.db.commit()
        revision = self.db.get(SegmentRevision, active_member(self.db, self.target.id).revision_id)
        result = resolve_group(self.db, revision, "accepted", self.user)
        self.db.commit()
        self.assertEqual(result["updated_count"], 3)

    def test_stale_job_does_not_write_and_new_generation_requeues(self):
        self.edit("B")
        task = self.queue()
        self.edit("C")
        self.assertIsNone(process_task(self.db, task.id))
        self.assertEqual(self.target.target_text, "A")
        self.propagate()
        self.assertEqual(self.target.target_text, "C")

    def test_real_permission_recheck_skips_unassigned_reviewer(self):
        self.edit("B")
        task = self.queue()
        self.user.role = "user"
        self.user.translator_type = "external"
        self.db.commit()
        result = process_task(self.db, task.id)
        self.assertEqual(result["updated_count"], 0)
        self.assertEqual(self.target.target_text, "A")

    def test_two_workers_serialize_without_duplicate_revisions(self):
        self.edit("B")
        task = self.queue()
        task_id = task.id
        self.db.commit()
        def run():
            with Session(self.engine, autoflush=False) as db:
                return process_task(db, task_id)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: run(), range(2)))
        self.assertEqual(sum(r["updated_count"] for r in results if r), 2)
        self.db.expire_all()
        group_id = active_member(self.db, self.source.id).group_id
        self.assertEqual(self.db.query(ReviewSyncMember).filter_by(group_id=group_id).count(), 3)

    def test_edit_waits_for_worker_project_lock(self):
        self.edit("B")
        task = self.queue()
        task_id, project_id = task.id, self.project.id
        self.db.commit()
        started = Event()
        done = Event()
        def run():
            started.set()
            with Session(self.engine, autoflush=False) as db:
                process_task(db, task_id)
            done.set()
        lock_project(self.db, project_id)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run)
            self.assertTrue(started.wait(5))
            self.assertFalse(done.wait(0.2))
            self.db.commit()
            future.result(timeout=10)
        self.assertTrue(done.is_set())

    def test_disabled_and_untracked_edits_do_not_create_groups(self):
        self.project.review_sync_enabled = False
        self.db.commit()
        self.assertIsNone(self.edit("B"))
        self.assertEqual(self.target.target_text, "A")
        self.project.review_sync_enabled = True
        self.db.commit()
        batch_update_segments(self.db, self.target.file_record_id, [{"sentence_id": self.target.sentence_id,
            "target_text": "C", "track_revision": False}], current_user=self.user)
        self.assertIsNone(active_member(self.db, self.target.id))

    def test_api_serialization_and_batch_resolution_deduplicate_groups(self):
        from app.services.review_sync import revision_sync_info, segment_sync_info
        from app.services.revision_service import batch_reject_revisions
        self.edit("B")
        self.propagate()
        self.assertIsNotNone(segment_sync_info(self.source)["review_sync_group_id"])
        self.assertIsNone(segment_sync_info(self.target)["review_sync_group_id"])
        revision = self.db.get(SegmentRevision, active_member(self.db, self.target.id).revision_id)
        self.assertEqual(revision_sync_info(revision)["review_sync_count"], 3)
        with patch("app.services.revision_service.require_revisions_table"), patch("app.services.segment_events.publish_segment_changes"):
            count = batch_reject_revisions(self.db, file_record_id=self.files[0].id, current_user=self.user)
        self.assertEqual(count, 3)
        self.assertEqual(self.db.info["review_sync_result"]["updated_count"], 3)
        self.assertEqual(self.target.target_text, "A")

    def test_batch_resolution_does_not_fall_back_and_overwrite_skipped_member(self):
        from app.services.revision_service import batch_reject_revisions
        from app.services.review_sync import can_write
        self.edit("B")
        self.propagate()
        with patch("app.services.revision_service.require_revisions_table"), patch("app.services.segment_events.publish_segment_changes"), \
             patch("app.services.review_sync.can_write", side_effect=lambda db, s, u: s.id != self.same_file.id and can_write(db, s, u)):
            count = batch_reject_revisions(self.db, file_record_id=self.files[0].id, current_user=self.user)
        self.assertEqual(count, 2)
        self.assertEqual(self.same_file.target_text, "B")
        self.assertEqual(self.db.info["review_sync_result"]["skipped_count"], 1)

    def test_failed_task_retries_atomically_and_can_be_manually_requeued(self):
        from app.services.review_sync import run_review_sync_once
        self.edit("B")
        task = self.queue()
        task_id = task.id
        self.db.commit()
        with patch("app.services.review_sync.SessionLocal", side_effect=lambda: Session(self.engine, autoflush=False)), \
             patch("app.services.review_sync._finish_files", side_effect=RuntimeError("test failure")), \
             patch("app.services.review_sync.logger.exception"):
            for _ in range(3):
                run_review_sync_once()
        self.db.expire_all()
        self.assertEqual(self.db.get(ReviewSyncTask, task_id).status, "failed")
        self.assertEqual(self.target.target_text, "A")
        self.assertIsNone(active_member(self.db, self.target.id))
        task = self.queue()
        self.assertEqual(task.attempts, 0)
        process_task(self.db, task.id)
        self.assertEqual(self.target.target_text, "B")

    def test_migration_is_idempotent(self):
        from app.services.review_sync_schema import schema_statements
        for _ in range(2):
            for statement in schema_statements():
                self.db.execute(text(statement))
        self.db.commit()
        project = Project(name="默认开启")
        self.db.add(project)
        self.db.commit()
        self.assertTrue(project.review_sync_enabled)

    def test_upgrade_enables_existing_projects_only_once(self):
        from app.services.review_sync_schema import DEFAULT_ENABLED_MIGRATION, default_needs_upgrade
        from sqlalchemy import inspect
        self.db.execute(text("ALTER TABLE projects ALTER COLUMN review_sync_enabled SET DEFAULT FALSE"))
        self.project.review_sync_enabled = False
        self.db.commit()
        self.assertTrue(default_needs_upgrade(inspect(self.db.connection())))
        self.db.execute(text(DEFAULT_ENABLED_MIGRATION))
        self.db.commit()
        self.assertTrue(self.project.review_sync_enabled)
        self.assertFalse(default_needs_upgrade(inspect(self.db.connection())))
        self.project.review_sync_enabled = False
        self.db.commit()
        self.db.execute(text(DEFAULT_ENABLED_MIGRATION))
        self.db.commit()
        self.assertFalse(self.project.review_sync_enabled)
        project_id = self.db.execute(text("INSERT INTO projects (name, status) VALUES ('database default', 'draft') RETURNING id")).scalar_one()
        self.db.commit()
        self.assertTrue(self.db.get(Project, project_id).review_sync_enabled)


if __name__ == "__main__":
    unittest.main()
