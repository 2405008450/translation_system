from __future__ import annotations

import unittest
import json
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.routers import api
from app.models import AutoTMOutbox, AutoTMRematchQueue, FileRecord, MemoryBase, MemoryEntry, Project, ProjectSegmentSyncOutbox, Segment, SegmentRevision, User
from app.services.auto_tm_sync import (
    enqueue_confirmed_segments_for_auto_tm,
    process_due_auto_tm_rematches,
    process_auto_tm_outbox,
    refresh_unconfirmed_segment_matches,
    register_file_rematch_work,
    register_project_collection_rematch_work,
)
from app.services.file_record_service import batch_update_segments
from app.services.normalizer import build_source_hash, normalize_match_text
from app.services.tm_match_state import (
    build_tm_match_signature,
    is_tm_match_signature_current,
    mark_tm_match_signature_current,
)
from app.services.project_segment_sync import (
    ProjectSegmentSyncSummary,
    converge_pretranslation_llm_segments,
    disable_project_sync_for_segments,
    enable_project_sync_for_segments,
    sync_project_repeated_segments_from_file,
    sync_project_repeated_segments_from_segments,
    sync_project_segments_from_source,
)
from app.services.project_sync_outbox import enqueue_project_segment_sync, process_project_sync_outbox
from app.services.translation_memory_service import TMUpsertEntry, batch_upsert_tm_entries


def _create_auto_tm_test_schema(db: Session) -> None:
    statements = [
        """
        CREATE TABLE users (
            id CHAR(32) PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            nickname VARCHAR(50),
            hashed_password VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL,
            translator_type VARCHAR(20) NOT NULL DEFAULT 'internal',
            is_active BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE memory_bases (
            id CHAR(32) PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            source_language VARCHAR(20),
            target_language VARCHAR(20),
            project_id CHAR(32),
            origin VARCHAR(20) NOT NULL DEFAULT 'manual',
            entry_count BIGINT NOT NULL DEFAULT 0,
            creator_id CHAR(32),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE memory_entries (
            id CHAR(32) PRIMARY KEY,
            collection_id CHAR(32),
            source_text TEXT NOT NULL,
            target_text TEXT NOT NULL,
            source_hash VARCHAR(64),
            source_normalized TEXT,
            source_language VARCHAR(20),
            target_language VARCHAR(20),
            creator_id CHAR(32),
            last_modified_by_id CHAR(32),
            external_tuid TEXT,
            tmx_metadata JSON,
            import_batch_id CHAR(32),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE projects (
            id CHAR(32) PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full',
            source_language VARCHAR(20),
            target_language VARCHAR(20),
            workflow_template_id VARCHAR(40) NOT NULL DEFAULT 'custom',
            creator_id CHAR(32),
            deadline DATETIME,
            access_level VARCHAR(20) NOT NULL DEFAULT 'team',
            translation_guidelines TEXT NOT NULL DEFAULT '',
            quality_qa_settings TEXT NOT NULL DEFAULT '{}',
            auto_tm_enabled BOOLEAN NOT NULL DEFAULT 1,
            translation_rules TEXT NOT NULL DEFAULT '',
            translation_rules_filename VARCHAR(255),
            translation_rules_updated_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE UNIQUE INDEX uq_memory_entries_collection_source_hash_language_pair
        ON memory_entries (collection_id, source_hash, source_language, target_language)
        """,
        """
        CREATE TABLE file_records (
            id CHAR(32) PRIMARY KEY,
            project_id CHAR(32),
            filename VARCHAR(255) NOT NULL,
            translated_filename VARCHAR(255),
            file_hash VARCHAR(64),
            status VARCHAR(20) NOT NULL,
            document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full',
            document_parse_options TEXT NOT NULL DEFAULT '{}',
            document_statistics TEXT NOT NULL DEFAULT '{}',
            active_operation VARCHAR(40),
            active_operation_token VARCHAR(64),
            active_operation_updated_at DATETIME,
            active_operation_user_id CHAR(32),
            assignee_id CHAR(32),
            assigned_by_id CHAR(32),
            assigned_at DATETIME,
            source_language VARCHAR(20),
            target_language VARCHAR(20),
            creator_id CHAR(32),
            collection_id CHAR(32),
            collection_ids_json TEXT NOT NULL DEFAULT '[]',
            tm_match_threshold FLOAT NOT NULL DEFAULT 0.8,
            tm_scope_mode VARCHAR(24) NOT NULL DEFAULT 'selected',
            tm_match_signature VARCHAR(64),
            tm_last_matched_at DATETIME,
            term_base_id CHAR(32),
            term_base_ids TEXT NOT NULL DEFAULT '[]',
            term_base_write_ids TEXT NOT NULL DEFAULT '[]',
            qa_term_base_ids TEXT NOT NULL DEFAULT '[]',
            glossary_base_ids TEXT NOT NULL DEFAULT '[]',
            deadline DATETIME,
            access_level VARCHAR(20) NOT NULL DEFAULT 'team',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE segments (
            auto_write_protection VARCHAR(20) NOT NULL DEFAULT 'none',
            auto_write_protection_reason VARCHAR(40),
            auto_write_protection_updated_at DATETIME,
            auto_write_protection_updated_by_id CHAR(32),
            id CHAR(32) PRIMARY KEY,
            file_record_id CHAR(32) NOT NULL,
            workflow_step_id CHAR(32),
            sentence_id VARCHAR(100) NOT NULL,
            source_text TEXT NOT NULL,
            source_hash VARCHAR(64),
            display_text TEXT NOT NULL,
            source_html TEXT,
            target_text TEXT NOT NULL,
            target_html TEXT,
            status VARCHAR(20) NOT NULL,
            project_sync_disabled BOOLEAN NOT NULL DEFAULT 0,
            project_sync_source_segment_id CHAR(32),
            project_sync_source_file_record_id CHAR(32),
            version INTEGER NOT NULL DEFAULT 1,
            score FLOAT NOT NULL,
            matched_source_text TEXT,
            matched_collection_name VARCHAR(120),
            matched_creator_name VARCHAR(100),
            matched_created_at DATETIME,
            matched_updated_at DATETIME,
            source VARCHAR(20) NOT NULL,
            source_word_count INTEGER NOT NULL DEFAULT 0,
            llm_provider VARCHAR(40),
            llm_model VARCHAR(200),
            last_modified_by_id CHAR(32),
            block_type VARCHAR(20) NOT NULL,
            block_index INTEGER NOT NULL,
            row_index INTEGER,
            cell_index INTEGER,
            sequence_index INTEGER NOT NULL DEFAULT -1,
            segment_metadata TEXT NOT NULL DEFAULT '{}',
            display_index INTEGER NOT NULL DEFAULT -1,
            confirmed_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE file_segment_stats (
            file_record_id CHAR(32) PRIMARY KEY,
            total INTEGER NOT NULL DEFAULT 0,
            project_sync_count INTEGER NOT NULL DEFAULT 0,
            exact_count INTEGER NOT NULL DEFAULT 0,
            fuzzy_count INTEGER NOT NULL DEFAULT 0,
            none_count INTEGER NOT NULL DEFAULT 0,
            confirmed_count INTEGER NOT NULL DEFAULT 0,
            empty_target_count INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE segment_revisions (
            id CHAR(32) PRIMARY KEY,
            file_record_id CHAR(32) NOT NULL,
            segment_id CHAR(32) NOT NULL,
            sentence_id VARCHAR(100) NOT NULL,
            before_text TEXT NOT NULL DEFAULT '',
            after_text TEXT NOT NULL DEFAULT '',
            source VARCHAR(20) NOT NULL DEFAULT 'manual',
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            author_id CHAR(32),
            resolved_by_id CHAR(32),
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        )
        """,
        """
        CREATE TABLE translation_metric_events (
            id CHAR(32) PRIMARY KEY,
            event_key VARCHAR(140),
            project_id CHAR(32),
            file_record_id CHAR(32),
            segment_id CHAR(32),
            user_id CHAR(32),
            source VARCHAR(20) NOT NULL DEFAULT 'manual',
            source_language VARCHAR(20),
            target_language VARCHAR(20),
            source_word_count INTEGER NOT NULL DEFAULT 0,
            target_was_empty BOOLEAN NOT NULL DEFAULT TRUE,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE auto_tm_outbox (
            id CHAR(32) PRIMARY KEY,
            file_record_id CHAR(32) NOT NULL,
            segment_id CHAR(32) NOT NULL,
            sentence_id VARCHAR(100) NOT NULL,
            collection_id CHAR(32) NOT NULL,
            source_text TEXT NOT NULL,
            target_text TEXT NOT NULL,
            source_language VARCHAR(20) NOT NULL,
            target_language VARCHAR(20) NOT NULL,
            creator_id CHAR(32),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            last_enqueued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE UNIQUE INDEX uq_auto_tm_outbox_file_segment_collection
        ON auto_tm_outbox (file_record_id, segment_id, collection_id)
        """,
        """
        CREATE TABLE auto_tm_rematch_queue (
            id CHAR(32) PRIMARY KEY,
            file_record_id CHAR(32) NOT NULL,
            collection_id CHAR(32) NOT NULL,
            pending_entry_count INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            first_pending_at DATETIME,
            last_pending_at DATETIME,
            last_processed_at DATETIME,
            error_message TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE UNIQUE INDEX uq_auto_tm_rematch_queue_file_record
        ON auto_tm_rematch_queue (file_record_id)
        """,
        """
        CREATE TABLE project_segment_sync_outbox (
            id CHAR(32) PRIMARY KEY,
            project_id CHAR(32) NOT NULL,
            source_language VARCHAR(20) NOT NULL DEFAULT '',
            target_language VARCHAR(20) NOT NULL DEFAULT '',
            source_hash VARCHAR(64) NOT NULL,
            source_file_record_id CHAR(32),
            source_segment_id CHAR(32),
            source_version INTEGER,
            trigger_kind VARCHAR(20) NOT NULL DEFAULT 'confirmed',
            generation INTEGER NOT NULL DEFAULT 1,
            requested_by_id CHAR(32),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT NOT NULL DEFAULT '',
            last_enqueued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            processed_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE UNIQUE INDEX uq_project_sync_outbox_scope
        ON project_segment_sync_outbox (project_id, source_language, target_language, source_hash)
        """,
    ]
    for statement in statements:
        db.execute(text(statement))
    db.execute(text("ALTER TABLE projects ADD COLUMN review_sync_enabled BOOLEAN NOT NULL DEFAULT FALSE"))
    from app.models import ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask
    for model in (ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask):
        model.__table__.create(db.connection(), checkfirst=True)
    db.commit()


class AutoTMSyncTests(unittest.TestCase):
    def _session(self):
        engine = create_engine("sqlite:///:memory:", future=True)
        db = Session(engine)
        _create_auto_tm_test_schema(db)
        return db

    def test_confirmed_segment_is_enqueued_and_written_to_bound_memory(self):
        with self._session() as db:
            user = User(username="translator", hashed_password="x", role="user")
            collection = MemoryBase(name="Project TM", source_language="zh-CN", target_language="en-US")
            db.add_all([user, collection])
            db.flush()
            file_record = FileRecord(
                filename="demo.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=collection.id,
            )
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="你好",
                display_text="你好",
                target_text="Hello",
                status="confirmed",
                source="manual",
            )
            db.add_all([file_record, segment])
            db.flush()

            summary = enqueue_confirmed_segments_for_auto_tm(
                db,
                file_record=file_record,
                segments=[segment],
                current_user=user,
            )
            self.assertEqual(summary.queued_count, 1)
            db.commit()

            processed = process_auto_tm_outbox(db)
            self.assertEqual(processed, 1)

            entry = db.query(MemoryEntry).one()
            self.assertEqual(entry.source_text, "你好")
            self.assertEqual(entry.target_text, "Hello")
            self.assertEqual(entry.creator_id, user.id)
            self.assertEqual(db.query(AutoTMOutbox).one().status, "completed")
            self.assertEqual(db.query(AutoTMRematchQueue).one().pending_entry_count, 1)

    def test_confirmed_segment_writes_only_primary_memory_when_multiple_bound(self):
        with self._session() as db:
            user = User(username="translator", hashed_password="x", role="user")
            primary = MemoryBase(name="Primary TM", source_language="zh-CN", target_language="en-US")
            secondary = MemoryBase(name="Secondary TM", source_language="zh-CN", target_language="en-US")
            db.add_all([user, primary, secondary])
            db.flush()
            file_record = FileRecord(
                filename="demo.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=primary.id,
                collection_ids_json=json.dumps([str(primary.id), str(secondary.id)]),
            )
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="你好",
                display_text="你好",
                target_text="Hello",
                status="confirmed",
                source="manual",
            )
            db.add_all([file_record, segment])
            db.flush()

            summary = enqueue_confirmed_segments_for_auto_tm(
                db,
                file_record=file_record,
                segments=[segment],
                current_user=user,
            )
            self.assertEqual(summary.queued_count, 1)
            db.commit()

            self.assertEqual(process_auto_tm_outbox(db), 1)

            entries = db.query(MemoryEntry).all()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].collection_id, primary.id)

    def test_confirmed_segment_is_not_enqueued_when_project_auto_tm_disabled(self):
        with self._session() as db:
            user = User(username="translator", hashed_password="x", role="user")
            collection = MemoryBase(name="Project TM", source_language="zh-CN", target_language="en-US")
            project = Project(name="No auto TM", auto_tm_enabled=False)
            db.add_all([user, collection, project])
            db.flush()
            file_record = FileRecord(
                project_id=project.id,
                filename="demo.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=collection.id,
            )
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="你好",
                display_text="你好",
                target_text="Hello",
                status="confirmed",
                source="manual",
            )
            db.add_all([file_record, segment])
            db.flush()

            summary = enqueue_confirmed_segments_for_auto_tm(
                db,
                file_record=file_record,
                segments=[segment],
                current_user=user,
            )

            self.assertEqual(summary.queued_count, 0)
            self.assertEqual(db.query(AutoTMOutbox).count(), 0)

    def test_collection_change_queues_only_same_project_bound_files(self):
        with self._session() as db:
            project_id = uuid4()
            other_project_id = uuid4()
            collection = MemoryBase(name="Project TM", source_language="zh-CN", target_language="en-US")
            db.add(collection)
            db.flush()
            source_file = FileRecord(
                project_id=project_id,
                filename="source.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=collection.id,
                collection_ids_json=json.dumps([str(collection.id)]),
            )
            sibling_file = FileRecord(
                project_id=project_id,
                filename="sibling.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=collection.id,
                collection_ids_json=json.dumps([str(collection.id)]),
            )
            other_project_file = FileRecord(
                project_id=other_project_id,
                filename="other.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=collection.id,
                collection_ids_json=json.dumps([str(collection.id)]),
            )
            unbound_file = FileRecord(
                project_id=project_id,
                filename="unbound.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=None,
                collection_ids_json="[]",
            )
            db.add_all([source_file, sibling_file, other_project_file, unbound_file])
            db.flush()

            queued_count = register_project_collection_rematch_work(
                db,
                collection_id=collection.id,
                source_file_record_id=source_file.id,
            )

            queued_file_ids = {row.file_record_id for row in db.query(AutoTMRematchQueue).all()}
            self.assertEqual(queued_count, 2)
            self.assertEqual(queued_file_ids, {source_file.id, sibling_file.id})

    def test_refresh_fills_only_empty_unconfirmed_targets(self):
        with self._session() as db:
            collection = MemoryBase(name="Project TM", source_language="zh-CN", target_language="en-US")
            db.add(collection)
            db.flush()
            entry = MemoryEntry(
                collection_id=collection.id,
                source_text="你好",
                target_text="Hello",
                source_hash=build_source_hash("你好"),
                source_normalized=normalize_match_text("你好") or "你好",
                source_language="zh-CN",
                target_language="en-US",
            )
            file_record = FileRecord(
                filename="demo.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=collection.id,
                collection_ids_json=json.dumps([str(collection.id)]),
            )
            empty_segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="你好",
                display_text="你好",
                target_text="",
                status="none",
                source="manual",
            )
            draft_segment = Segment(
                file_record=file_record,
                sentence_id="s2",
                source_text="你好",
                display_text="你好",
                target_text="Draft",
                status="none",
                source="manual",
            )
            confirmed_segment = Segment(
                file_record=file_record,
                sentence_id="s3",
                source_text="你好",
                display_text="你好",
                target_text="Confirmed",
                status="confirmed",
                source="manual",
            )
            db.add_all([entry, file_record, empty_segment, draft_segment, confirmed_segment])
            db.commit()

            updated_count = refresh_unconfirmed_segment_matches(
                db,
                file_record_id=file_record.id,
                collection_ids=[collection.id],
            )

            self.assertGreaterEqual(updated_count, 1)
            self.assertEqual(empty_segment.target_text, "Hello")
            self.assertIn(empty_segment.status, {"exact", "fuzzy"})
            self.assertEqual(draft_segment.target_text, "Draft")
            self.assertEqual(confirmed_segment.target_text, "Confirmed")

    def test_file_rematch_work_queues_single_file_for_background_refresh(self):
        with self._session() as db:
            collection = MemoryBase(name="Project TM", source_language="zh-CN", target_language="en-US")
            file_record = FileRecord(
                filename="demo.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=collection.id,
                collection_ids_json=json.dumps([str(collection.id)]),
            )
            db.add_all([collection, file_record])
            db.flush()

            queued_count = register_file_rematch_work(
                db,
                file_record_id=file_record.id,
                collection_ids=[collection.id],
            )

            queue = db.query(AutoTMRematchQueue).one()
            self.assertEqual(queued_count, 1)
            self.assertEqual(queue.file_record_id, file_record.id)
            self.assertEqual(queue.collection_id, collection.id)

    def test_due_auto_tm_rematch_limits_files_per_run(self):
        with self._session() as db:
            collection = MemoryBase(name="Project TM", source_language="zh-CN", target_language="en-US")
            db.add(collection)
            db.flush()
            file_records = [
                FileRecord(
                    filename=f"demo-{index}.docx",
                    source_language="zh-CN",
                    target_language="en-US",
                    collection_id=collection.id,
                    collection_ids_json=json.dumps([str(collection.id)]),
                )
                for index in range(3)
            ]
            db.add_all(file_records)
            db.flush()
            for file_record in file_records:
                register_file_rematch_work(
                    db,
                    file_record_id=file_record.id,
                    collection_ids=[collection.id],
                )
            db.commit()

            process_due_auto_tm_rematches(db, force=True, max_files=2)

            processed_count = (
                db.query(AutoTMRematchQueue)
                .filter(AutoTMRematchQueue.last_processed_at.isnot(None))
                .count()
            )
            self.assertEqual(processed_count, 2)

    def test_tm_match_signature_tracks_file_options_and_memory_watermark(self):
        with self._session() as db:
            collection = MemoryBase(name="Project TM", source_language="zh-CN", target_language="en-US")
            db.add(collection)
            db.flush()
            entry = MemoryEntry(
                collection_id=collection.id,
                source_text="浣犲ソ",
                target_text="Hello",
                source_hash=build_source_hash("浣犲ソ"),
                source_normalized=normalize_match_text("浣犲ソ") or "浣犲ソ",
                source_language="zh-CN",
                target_language="en-US",
            )
            file_record = FileRecord(
                filename="demo.docx",
                source_language="zh-CN",
                target_language="en-US",
                collection_id=collection.id,
                collection_ids_json=json.dumps([str(collection.id)]),
            )
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="浣犲ソ",
                source_hash=build_source_hash("浣犲ソ"),
                display_text="浣犲ソ",
                target_text="",
                status="none",
                source="manual",
            )
            db.add_all([entry, file_record, segment])
            db.commit()

            signature = build_tm_match_signature(
                db,
                file_record_id=file_record.id,
                collection_ids=[collection.id],
                threshold=0.75,
                skip_confirmed=True,
                overwrite_fuzzy=True,
                auto_confirm_exact=True,
            )
            mark_tm_match_signature_current(file_record, signature)
            db.commit()

            self.assertTrue(is_tm_match_signature_current(file_record, signature))
            changed_threshold_signature = build_tm_match_signature(
                db,
                file_record_id=file_record.id,
                collection_ids=[collection.id],
                threshold=0.8,
                skip_confirmed=True,
                overwrite_fuzzy=True,
                auto_confirm_exact=True,
            )
            self.assertNotEqual(signature, changed_threshold_signature)

            # 生产触发器在明细变化时推进记忆库水位，签名无需扫描明细表。
            collection.updated_at = datetime.now() + timedelta(seconds=5)
            collection.entry_count = 1
            db.commit()
            changed_memory_signature = build_tm_match_signature(
                db,
                file_record_id=file_record.id,
                collection_ids=[collection.id],
                threshold=0.75,
                skip_confirmed=True,
                overwrite_fuzzy=True,
                auto_confirm_exact=True,
            )
            self.assertNotEqual(signature, changed_memory_signature)

            segment.source_text = "鍐嶈"
            segment.source_hash = build_source_hash(segment.source_text)
            db.commit()
            changed_source_signature = build_tm_match_signature(
                db,
                file_record_id=file_record.id,
                collection_ids=[collection.id],
                threshold=0.75,
                skip_confirmed=True,
                overwrite_fuzzy=True,
                auto_confirm_exact=True,
            )
            self.assertNotEqual(changed_memory_signature, changed_source_signature)

    def test_confirmed_segment_without_collection_is_skipped(self):
        with self._session() as db:
            file_record = FileRecord(filename="demo.docx", source_language="zh-CN", target_language="en-US")
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="你好",
                display_text="你好",
                target_text="Hello",
                status="confirmed",
                source="manual",
            )
            db.add_all([file_record, segment])
            db.flush()

            summary = enqueue_confirmed_segments_for_auto_tm(
                db,
                file_record=file_record,
                segments=[segment],
            )

            self.assertEqual(summary.queued_count, 0)
            self.assertEqual(summary.skipped_no_collection_count, 1)
            self.assertEqual(db.query(AutoTMOutbox).count(), 0)

    def test_batch_upsert_overwrites_existing_translation(self):
        with self._session() as db:
            collection = MemoryBase(name="Project TM", source_language="zh-CN", target_language="en-US")
            db.add(collection)
            db.flush()
            source_text = "你好"
            existing = MemoryEntry(
                collection_id=collection.id,
                source_text=source_text,
                target_text="Old",
                source_hash=build_source_hash(source_text),
                source_normalized=normalize_match_text(source_text) or source_text,
                source_language="zh-CN",
                target_language="en-US",
            )
            db.add(existing)
            db.commit()

            summary = batch_upsert_tm_entries(
                db,
                [
                    TMUpsertEntry(
                        collection_id=collection.id,
                        source_text=source_text,
                        target_text="New",
                        source_language="zh-CN",
                        target_language="en-US",
                    )
                ],
            )
            db.commit()

            self.assertEqual(summary.updated_count, 1)
            self.assertEqual(db.query(MemoryEntry).one().target_text, "New")

    def test_batch_update_reports_version_conflict(self):
        with self._session() as db:
            file_record = FileRecord(filename="demo.docx", source_language="zh-CN", target_language="en-US")
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="你好",
                display_text="你好",
                target_text="Old",
                status="confirmed",
                source="manual",
                version=2,
            )
            db.add_all([file_record, segment])
            db.commit()

            result = batch_update_segments(
                db,
                file_record.id,
                [{"sentence_id": "s1", "target_text": "New", "source": "manual", "base_version": 1}],
                return_result=True,
            )

            self.assertEqual(result.updated_count, 0)
            self.assertEqual(len(result.conflicts), 1)
            self.assertEqual(db.query(Segment).one().target_text, "Old")

    def test_batch_update_auto_merges_stale_same_user_edit(self):
        with self._session() as db:
            user = User(username="same-user", hashed_password="x", role="user")
            file_record = FileRecord(filename="demo.docx", source_language="zh-CN", target_language="en-US")
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="hello",
                display_text="hello",
                target_text="Old",
                status="none",
                source="manual",
                version=2,
                last_modified_by=user,
            )
            db.add_all([user, file_record, segment])
            db.commit()

            result = batch_update_segments(
                db,
                file_record.id,
                [{"sentence_id": "s1", "target_text": "New", "source": "manual", "base_version": 1}],
                current_user=user,
                return_result=True,
            )

            self.assertEqual(result.updated_count, 1)
            self.assertEqual(result.conflicts, [])
            self.assertEqual(db.query(Segment).one().target_text, "New")

    def test_batch_update_preserves_confirmed_status_when_text_is_unchanged(self):
        with self._session() as db:
            file_record = FileRecord(filename="demo.docx", source_language="zh-CN", target_language="en-US")
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="hello",
                display_text="hello",
                target_text="Approved",
                target_html="<b>Approved</b>",
                status="confirmed",
                source="tm",
                score=1.0,
                matched_source_text="hello",
                version=2,
            )
            db.add_all([file_record, segment])
            db.commit()

            result = batch_update_segments(
                db,
                file_record.id,
                [
                    {
                        "sentence_id": "s1",
                        "target_text": "Approved",
                        "target_html": "<strong>Approved</strong>",
                        "source": "manual",
                        "base_version": 2,
                    }
                ],
                return_result=True,
            )

            self.assertEqual(result.updated_count, 1)
            saved = db.query(Segment).one()
            self.assertEqual(saved.target_text, "Approved")
            self.assertEqual(saved.status, "confirmed")

    def test_batch_update_keeps_conflict_for_different_manual_user(self):
        with self._session() as db:
            first_user = User(username="first-user", hashed_password="x", role="user")
            second_user = User(username="second-user", hashed_password="x", role="user")
            file_record = FileRecord(filename="demo.docx", source_language="zh-CN", target_language="en-US")
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="hello",
                display_text="hello",
                target_text="First",
                status="none",
                source="manual",
                version=2,
                last_modified_by=first_user,
            )
            db.add_all([first_user, second_user, file_record, segment])
            db.commit()

            result = batch_update_segments(
                db,
                file_record.id,
                [{"sentence_id": "s1", "target_text": "Second", "source": "manual", "base_version": 1}],
                current_user=second_user,
                return_result=True,
            )

            self.assertEqual(result.updated_count, 0)
            self.assertEqual(len(result.conflicts), 1)
            self.assertEqual(db.query(Segment).one().target_text, "First")

    def test_batch_update_auto_merges_manual_over_llm_result(self):
        with self._session() as db:
            user = User(username="editor", hashed_password="x", role="user")
            file_record = FileRecord(filename="demo.docx", source_language="zh-CN", target_language="en-US")
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="hello",
                display_text="hello",
                target_text="Machine",
                status="none",
                source="llm",
                version=2,
            )
            db.add_all([user, file_record, segment])
            db.commit()

            result = batch_update_segments(
                db,
                file_record.id,
                [{"sentence_id": "s1", "target_text": "Manual", "source": "manual", "base_version": 1}],
                current_user=user,
                return_result=True,
            )

            self.assertEqual(result.updated_count, 1)
            self.assertEqual(result.conflicts, [])
            saved = db.query(Segment).one()
            self.assertEqual(saved.target_text, "Manual")
            self.assertEqual(saved.last_modified_by_id, user.id)

    def test_project_sync_fills_new_file_from_existing_project_translation(self):
        with self._session() as db:
            project_id = uuid4()
            source_hash = build_source_hash("重复原文")
            existing_file = FileRecord(
                project_id=project_id,
                filename="existing.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            new_file = FileRecord(
                project_id=project_id,
                filename="new.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            existing_segment = Segment(
                file_record=existing_file,
                sentence_id="s1",
                source_text="重复原文",
                source_hash=source_hash,
                display_text="重复原文",
                target_text="Repeated translation",
                status="confirmed",
                source="manual",
            )
            new_segment = Segment(
                file_record=new_file,
                sentence_id="s1",
                source_text="重复原文",
                source_hash=source_hash,
                display_text="重复原文",
                target_text="",
                status="none",
                source="none",
            )
            db.add_all([existing_file, new_file, existing_segment, new_segment])
            db.commit()

            summary = sync_project_repeated_segments_from_file(db, file_record=new_file)
            db.commit()

            self.assertEqual(summary.filled_count, 1)
            self.assertEqual(new_segment.target_text, "Repeated translation")
            self.assertEqual(new_segment.source, "project_sync")
            self.assertEqual(new_segment.status, "exact")
            self.assertEqual(new_segment.project_sync_source_segment_id, existing_segment.id)
            self.assertEqual(new_segment.project_sync_source_file_record_id, existing_file.id)

    def test_pretranslation_llm_convergence_uses_first_selected_file_and_protects_manual_content(self):
        with self._session() as db:
            project_id = uuid4()
            source_text = "相同的跨文件原文"
            source_hash = build_source_hash(source_text)
            user = User(username="pretranslator", hashed_password="x", role="user")
            first_file = FileRecord(
                project_id=project_id,
                filename="first.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            second_file = FileRecord(
                project_id=project_id,
                filename="second.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            first_llm = Segment(
                file_record=first_file,
                sentence_id="s2",
                source_text=source_text,
                source_hash=source_hash,
                display_text=source_text,
                target_text="Translation selected from the first file.",
                status="none",
                source="llm",
                llm_provider="openrouter",
                llm_model="model-a",
                display_index=2,
            )
            second_llm = Segment(
                file_record=second_file,
                sentence_id="s1",
                source_text=source_text,
                source_hash=source_hash,
                display_text=source_text,
                target_text="A different independently generated translation.",
                status="none",
                source="llm",
                llm_provider="openrouter",
                llm_model="model-b",
                version=3,
                display_index=1,
            )
            manual_segment = Segment(
                file_record=second_file,
                sentence_id="s3",
                source_text=source_text,
                source_hash=source_hash,
                display_text=source_text,
                target_text="Unconfirmed manual translation must remain unchanged.",
                status="none",
                source="manual",
            )
            db.add_all([user, first_file, second_file, first_llm, second_llm, manual_segment])
            db.commit()

            summary = converge_pretranslation_llm_segments(
                db,
                project_id=project_id,
                ordered_file_record_ids=[first_file.id, second_file.id],
                current_user=user,
            )
            db.commit()

            self.assertEqual(summary.duplicate_group_count, 1)
            self.assertEqual(summary.updated_count, 1)
            self.assertEqual(second_llm.target_text, first_llm.target_text)
            self.assertEqual(second_llm.source, "llm")
            self.assertEqual(second_llm.llm_model, "model-a")
            self.assertEqual(second_llm.version, 4)
            self.assertEqual(manual_segment.target_text, "Unconfirmed manual translation must remain unchanged.")
            revision = db.query(SegmentRevision).filter(SegmentRevision.segment_id == second_llm.id).one()
            self.assertEqual(revision.before_text, "A different independently generated translation.")
            self.assertEqual(revision.after_text, first_llm.target_text)
            self.assertEqual(revision.source, "project_sync")

    def test_pretranslation_llm_convergence_prefers_exact_context_over_file_order(self):
        with self._session() as db:
            project_id = uuid4()
            source_text = "精确上下文优先原文"
            source_hash = build_source_hash(source_text)
            first_file = FileRecord(
                project_id=project_id,
                filename="first.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            second_file = FileRecord(
                project_id=project_id,
                filename="second.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            first_llm = Segment(
                file_record=first_file,
                sentence_id="s1",
                source_text=source_text,
                source_hash=source_hash,
                display_text=source_text,
                target_text="First file result",
                status="none",
                source="llm",
                llm_model="model-a",
            )
            exact_llm = Segment(
                file_record=second_file,
                sentence_id="s1",
                source_text=source_text,
                source_hash=source_hash,
                display_text=source_text,
                target_text="Exact-context result",
                status="exact",
                source="llm",
                score=1.0,
                matched_source_text=source_text,
                llm_model="model-b",
            )
            db.add_all([first_file, second_file, first_llm, exact_llm])
            db.commit()

            summary = converge_pretranslation_llm_segments(
                db,
                project_id=project_id,
                ordered_file_record_ids=[first_file.id, second_file.id],
            )
            db.commit()

            self.assertEqual(summary.updated_count, 1)
            self.assertEqual(first_llm.target_text, "Exact-context result")
            self.assertEqual(first_llm.source, "llm")
            self.assertEqual(first_llm.llm_model, "model-b")

    def test_pretranslation_llm_convergence_prefers_confirmed_project_translation(self):
        with self._session() as db:
            project_id = uuid4()
            source_text = "已确认译文优先原文"
            source_hash = build_source_hash(source_text)
            first_file = FileRecord(
                project_id=project_id,
                filename="first.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            second_file = FileRecord(
                project_id=project_id,
                filename="second.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            approved_file = FileRecord(
                project_id=project_id,
                filename="approved.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            first_llm = Segment(
                file_record=first_file,
                sentence_id="s1",
                source_text=source_text,
                source_hash=source_hash,
                display_text=source_text,
                target_text="LLM result A",
                status="none",
                source="llm",
            )
            second_llm = Segment(
                file_record=second_file,
                sentence_id="s1",
                source_text=source_text,
                source_hash=source_hash,
                display_text=source_text,
                target_text="LLM result B",
                status="none",
                source="llm",
            )
            approved = Segment(
                file_record=approved_file,
                sentence_id="s1",
                source_text=source_text,
                source_hash=source_hash,
                display_text=source_text,
                target_text="Approved project translation",
                status="confirmed",
                source="manual",
                confirmed_at=datetime.now(),
            )
            db.add_all([first_file, second_file, approved_file, first_llm, second_llm, approved])
            db.commit()

            summary = converge_pretranslation_llm_segments(
                db,
                project_id=project_id,
                ordered_file_record_ids=[first_file.id, second_file.id],
            )
            db.commit()

            self.assertEqual(summary.updated_count, 2)
            for segment in (first_llm, second_llm):
                self.assertEqual(segment.target_text, "Approved project translation")
                self.assertEqual(segment.source, "project_sync")
                self.assertEqual(segment.status, "exact")
                self.assertEqual(segment.project_sync_source_segment_id, approved.id)
                self.assertEqual(segment.project_sync_source_file_record_id, approved_file.id)

    def test_project_sync_from_updated_segment_updates_enabled_targets(self):
        with self._session() as db:
            project_id = uuid4()
            source_hash = build_source_hash("同步原文")
            source_file = FileRecord(
                project_id=project_id,
                filename="source.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            sibling_file = FileRecord(
                project_id=project_id,
                filename="sibling.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            other_project_file = FileRecord(
                project_id=uuid4(),
                filename="other.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            source_segment = Segment(
                file_record=source_file,
                sentence_id="s1",
                source_text="同步原文",
                source_hash=source_hash,
                display_text="同步原文",
                target_text="Synced",
                status="manual",
                source="manual",
            )
            empty_segment = Segment(
                file_record=sibling_file,
                sentence_id="s1",
                source_text="同步原文",
                source_hash=source_hash,
                display_text="同步原文",
                target_text="",
                status="none",
                source="none",
            )
            draft_segment = Segment(
                file_record=sibling_file,
                sentence_id="s2",
                source_text="同步原文",
                source_hash=source_hash,
                display_text="同步原文",
                target_text="Draft",
                status="none",
                source="manual",
            )
            confirmed_segment = Segment(
                file_record=sibling_file,
                sentence_id="s3",
                source_text="同步原文",
                source_hash=source_hash,
                display_text="同步原文",
                target_text="Confirmed",
                status="confirmed",
                source="manual",
            )
            other_project_segment = Segment(
                file_record=other_project_file,
                sentence_id="s1",
                source_text="同步原文",
                source_hash=source_hash,
                display_text="同步原文",
                target_text="",
                status="none",
                source="none",
            )
            db.add_all([
                source_file,
                sibling_file,
                other_project_file,
                source_segment,
                empty_segment,
                draft_segment,
                confirmed_segment,
                other_project_segment,
            ])
            db.commit()

            summary = sync_project_repeated_segments_from_segments(
                db,
                file_record=source_file,
                source_segments=[source_segment],
            )
            db.commit()

            self.assertEqual(summary.filled_count, 1)
            self.assertEqual(summary.updated_count, 0)
            self.assertEqual(summary.protected_count, 2)
            self.assertEqual(empty_segment.target_text, "Synced")
            self.assertEqual(empty_segment.status, "exact")
            self.assertEqual(empty_segment.project_sync_source_segment_id, source_segment.id)
            self.assertEqual(empty_segment.project_sync_source_file_record_id, source_file.id)
            self.assertEqual(draft_segment.target_text, "Draft")
            self.assertEqual(draft_segment.status, "none")
            self.assertIsNone(draft_segment.project_sync_source_segment_id)
            self.assertIsNone(draft_segment.project_sync_source_file_record_id)
            self.assertEqual(confirmed_segment.target_text, "Confirmed")
            self.assertEqual(confirmed_segment.status, "confirmed")
            self.assertIsNone(confirmed_segment.project_sync_source_segment_id)
            self.assertIsNone(confirmed_segment.project_sync_source_file_record_id)
            self.assertEqual(other_project_segment.target_text, "")

    def test_project_sync_updates_existing_enabled_targets(self):
        with self._session() as db:
            project_id = uuid4()
            source_hash = build_source_hash("同步变更原文")
            source_file = FileRecord(
                project_id=project_id,
                filename="source.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            sibling_file = FileRecord(
                project_id=project_id,
                filename="sibling.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            source_segment = Segment(
                file_record=source_file,
                sentence_id="s1",
                source_text="同步变更原文",
                source_hash=source_hash,
                display_text="同步变更原文",
                target_text="New synced translation",
                status="confirmed",
                source="manual",
            )
            old_synced_segment = Segment(
                file_record=sibling_file,
                sentence_id="s1",
                source_text="同步变更原文",
                source_hash=source_hash,
                display_text="同步变更原文",
                target_text="Old synced translation",
                status="exact",
                source="project_sync",
                version=2,
            )
            manual_segment = Segment(
                file_record=sibling_file,
                sentence_id="s2",
                source_text="同步变更原文",
                source_hash=source_hash,
                display_text="同步变更原文",
                target_text="Manual draft",
                status="none",
                source="manual",
            )
            db.add_all([source_file, sibling_file, source_segment, old_synced_segment, manual_segment])
            db.commit()

            summary = sync_project_repeated_segments_from_segments(
                db,
                file_record=source_file,
                source_segments=[source_segment],
            )
            db.commit()

            self.assertEqual(summary.filled_count, 0)
            self.assertEqual(summary.updated_count, 1)
            self.assertEqual(summary.protected_count, 1)
            self.assertEqual(old_synced_segment.target_text, "New synced translation")
            self.assertEqual(old_synced_segment.source, "project_sync")
            self.assertEqual(old_synced_segment.version, 3)
            self.assertEqual(old_synced_segment.project_sync_source_segment_id, source_segment.id)
            self.assertEqual(old_synced_segment.project_sync_source_file_record_id, source_file.id)
            self.assertEqual(manual_segment.target_text, "Manual draft")
            self.assertEqual(manual_segment.source, "manual")
            self.assertEqual(manual_segment.status, "none")
            self.assertIsNone(manual_segment.project_sync_source_segment_id)
            self.assertIsNone(manual_segment.project_sync_source_file_record_id)

    def test_project_sync_protects_confirmed_targets_when_enabled(self):
        with self._session() as db:
            project_id = uuid4()
            source_hash = build_source_hash("确认同步原文")
            source_file = FileRecord(
                project_id=project_id,
                filename="source.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            sibling_file = FileRecord(
                project_id=project_id,
                filename="sibling.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            source_segment = Segment(
                file_record=source_file,
                sentence_id="s1",
                source_text="确认同步原文",
                source_hash=source_hash,
                display_text="确认同步原文",
                target_text="Approved replacement",
                status="confirmed",
                source="manual",
            )
            manual_confirmed = Segment(
                file_record=sibling_file,
                sentence_id="s1",
                source_text="确认同步原文",
                source_hash=source_hash,
                display_text="确认同步原文",
                target_text="Old confirmed manual",
                status="confirmed",
                source="manual",
                version=4,
            )
            disabled_confirmed = Segment(
                file_record=sibling_file,
                sentence_id="s2",
                source_text="确认同步原文",
                source_hash=source_hash,
                display_text="确认同步原文",
                target_text="Disabled confirmed",
                status="confirmed",
                source="manual",
                project_sync_disabled=True,
            )
            db.add_all([source_file, sibling_file, source_segment, manual_confirmed, disabled_confirmed])
            db.commit()

            summary = sync_project_repeated_segments_from_segments(
                db,
                file_record=source_file,
                source_segments=[source_segment],
            )
            db.commit()

            self.assertEqual(summary.updated_count, 0)
            self.assertEqual(summary.protected_count, 1)
            self.assertEqual(manual_confirmed.target_text, "Old confirmed manual")
            self.assertEqual(manual_confirmed.status, "confirmed")
            self.assertEqual(manual_confirmed.source, "manual")
            self.assertEqual(manual_confirmed.version, 4)
            self.assertEqual(disabled_confirmed.target_text, "Disabled confirmed")

    def test_project_sync_uses_latest_confirmed_candidate(self):
        with self._session() as db:
            project_id = uuid4()
            source_hash = build_source_hash("冲突原文")
            first_file = FileRecord(project_id=project_id, filename="a.docx", source_language="zh-CN", target_language="en-US")
            second_file = FileRecord(project_id=project_id, filename="b.docx", source_language="zh-CN", target_language="en-US")
            target_file = FileRecord(project_id=project_id, filename="target.docx", source_language="zh-CN", target_language="en-US")
            first = Segment(file_record=first_file, sentence_id="s1", source_text="冲突原文", source_hash=source_hash, display_text="冲突原文", target_text="A", status="confirmed", source="manual", confirmed_at=datetime.now())
            # 较新的确认时间必须胜出，不能再被较旧的 manual 来源优先级压过。
            second = Segment(file_record=second_file, sentence_id="s1", source_text="冲突原文", source_hash=source_hash, display_text="冲突原文", target_text="B", status="confirmed", source="tm", confirmed_at=datetime.now() + timedelta(seconds=5))
            target = Segment(file_record=target_file, sentence_id="s1", source_text="冲突原文", source_hash=source_hash, display_text="冲突原文", target_text="", status="none", source="none")
            db.add_all([first_file, second_file, target_file, first, second, target])
            db.commit()

            summary = sync_project_repeated_segments_from_file(db, file_record=target_file)
            db.commit()

            self.assertEqual(summary.filled_count, 1)
            self.assertEqual(summary.conflict_count, 0)
            self.assertEqual(target.target_text, "B")

    def test_blur_sync_uses_exact_saved_source_and_protects_manual_content(self):
        with self._session() as db:
            project_id = uuid4()
            source_hash = build_source_hash("same source")
            source_file = FileRecord(project_id=project_id, filename="source.docx", source_language="en", target_language="zh-CN")
            target_file = FileRecord(project_id=project_id, filename="target.docx", source_language="en", target_language="zh-CN")
            source = Segment(
                file_record=source_file,
                sentence_id="s1",
                source_text="same source",
                source_hash=source_hash,
                display_text="same source",
                target_text="最新失焦译文",
                status="fuzzy",
                source="manual",
                version=7,
            )
            old_confirmed = Segment(
                file_record=target_file,
                sentence_id="s1",
                source_text="same source",
                source_hash=source_hash,
                display_text="same source",
                target_text="旧确认译文",
                status="confirmed",
                source="manual",
            )
            machine_target = Segment(
                file_record=target_file,
                sentence_id="s2",
                source_text="same source",
                source_hash=source_hash,
                display_text="same source",
                target_text="旧 MT",
                status="exact",
                source="llm",
            )
            manual_draft = Segment(
                file_record=target_file,
                sentence_id="s3",
                source_text="same source",
                source_hash=source_hash,
                display_text="same source",
                target_text="其他人工草稿",
                status="fuzzy",
                source="manual",
            )
            db.add_all([source_file, target_file, source, old_confirmed, machine_target, manual_draft])
            db.commit()

            summary = sync_project_segments_from_source(
                db,
                project_id=project_id,
                source_language="en",
                target_language="zh-CN",
                source_hash=source_hash,
                source_segment_id=source.id,
                source_version=7,
            )
            db.commit()

            self.assertEqual(machine_target.target_text, "最新失焦译文")
            self.assertEqual(machine_target.source, "project_sync")
            self.assertEqual(old_confirmed.target_text, "旧确认译文")
            self.assertEqual(manual_draft.target_text, "其他人工草稿")
            self.assertEqual(summary.updated_count, 1)
            self.assertEqual(summary.protected_count, 2)

            source.version = 8
            machine_target.target_text = "再次变更的 MT"
            machine_target.source = "llm"
            db.commit()
            stale_summary = sync_project_segments_from_source(
                db,
                project_id=project_id,
                source_language="en",
                target_language="zh-CN",
                source_hash=source_hash,
                source_segment_id=source.id,
                source_version=7,
            )
            self.assertEqual(stale_summary.stale_source_count, 1)
            self.assertEqual(machine_target.target_text, "再次变更的 MT")

    def test_project_sync_skips_disabled_target_segment(self):
        with self._session() as db:
            project_id = uuid4()
            source_hash = build_source_hash("关闭同步原文")
            source_file = FileRecord(project_id=project_id, filename="source.docx", source_language="zh-CN", target_language="en-US")
            target_file = FileRecord(project_id=project_id, filename="target.docx", source_language="zh-CN", target_language="en-US")
            source_segment = Segment(
                file_record=source_file,
                sentence_id="s1",
                source_text="关闭同步原文",
                source_hash=source_hash,
                display_text="关闭同步原文",
                target_text="Do not sync",
                status="confirmed",
                source="manual",
            )
            disabled_target = Segment(
                file_record=target_file,
                sentence_id="s1",
                source_text="关闭同步原文",
                source_hash=source_hash,
                display_text="关闭同步原文",
                target_text="",
                status="none",
                source="project_sync",
                project_sync_disabled=True,
            )
            db.add_all([source_file, target_file, source_segment, disabled_target])
            db.commit()

            summary = sync_project_repeated_segments_from_file(db, file_record=target_file)
            db.commit()

            self.assertEqual(summary.filled_count, 0)
            self.assertEqual(disabled_target.target_text, "")

    def test_disable_project_sync_clears_project_synced_translation(self):
        with self._session() as db:
            file_record = FileRecord(filename="target.docx", source_language="zh-CN", target_language="en-US")
            synced_segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="同步原文",
                display_text="同步原文",
                target_text="Synced translation",
                target_html="<p>Synced translation</p>",
                status="exact",
                source="project_sync",
                score=1.0,
                matched_source_text="同步原文",
                matched_collection_name="项目同步",
                project_sync_source_segment_id=uuid4(),
                project_sync_source_file_record_id=uuid4(),
                version=3,
            )
            manual_segment = Segment(
                file_record=file_record,
                sentence_id="s2",
                source_text="人工原文",
                display_text="人工原文",
                target_text="Manual translation",
                status="confirmed",
                source="manual",
                version=2,
            )
            db.add_all([file_record, synced_segment, manual_segment])
            db.commit()

            summary = disable_project_sync_for_segments([synced_segment, manual_segment])
            db.commit()

            self.assertEqual(summary.updated_count, 2)
            self.assertEqual(summary.disabled_count, 2)
            self.assertEqual(summary.cleared_count, 1)
            self.assertTrue(synced_segment.project_sync_disabled)
            self.assertEqual(synced_segment.target_text, "")
            self.assertIsNone(synced_segment.target_html)
            self.assertEqual(synced_segment.status, "none")
            self.assertEqual(synced_segment.source, "none")
            self.assertEqual(synced_segment.score, 0.0)
            self.assertIsNone(synced_segment.matched_source_text)
            self.assertIsNone(synced_segment.project_sync_source_segment_id)
            self.assertIsNone(synced_segment.project_sync_source_file_record_id)
            self.assertEqual(synced_segment.version, 4)
            self.assertTrue(manual_segment.project_sync_disabled)
            self.assertEqual(manual_segment.target_text, "Manual translation")
            self.assertEqual(manual_segment.status, "confirmed")
            self.assertEqual(manual_segment.source, "manual")
            self.assertEqual(manual_segment.version, 3)

    def test_disable_project_sync_clears_legacy_disabled_project_synced_translation(self):
        with self._session() as db:
            file_record = FileRecord(filename="target.docx", source_language="zh-CN", target_language="en-US")
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="同步原文",
                display_text="同步原文",
                target_text="Legacy synced",
                status="exact",
                source="project_sync",
                project_sync_disabled=True,
                version=5,
            )
            db.add_all([file_record, segment])
            db.commit()

            summary = disable_project_sync_for_segments([segment])
            db.commit()

            self.assertEqual(summary.updated_count, 1)
            self.assertEqual(summary.disabled_count, 0)
            self.assertEqual(summary.cleared_count, 1)
            self.assertTrue(segment.project_sync_disabled)
            self.assertEqual(segment.target_text, "")
            self.assertEqual(segment.status, "none")
            self.assertEqual(segment.source, "none")
            self.assertEqual(segment.version, 6)

    def test_enable_project_sync_restores_disabled_flag_only(self):
        with self._session() as db:
            file_record = FileRecord(filename="target.docx", source_language="zh-CN", target_language="en-US")
            disabled_segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="人工原文",
                display_text="人工原文",
                target_text="Manual translation",
                status="confirmed",
                source="manual",
                project_sync_disabled=True,
                version=4,
            )
            enabled_segment = Segment(
                file_record=file_record,
                sentence_id="s2",
                source_text="已开启原文",
                display_text="已开启原文",
                target_text="Already enabled",
                status="confirmed",
                source="manual",
                version=2,
            )
            db.add_all([file_record, disabled_segment, enabled_segment])
            db.commit()

            summary = enable_project_sync_for_segments([disabled_segment, enabled_segment])
            db.commit()

            self.assertEqual(summary.updated_count, 1)
            self.assertEqual(summary.disabled_count, 0)
            self.assertEqual(summary.cleared_count, 0)
            self.assertFalse(disabled_segment.project_sync_disabled)
            self.assertEqual(disabled_segment.target_text, "Manual translation")
            self.assertEqual(disabled_segment.status, "confirmed")
            self.assertEqual(disabled_segment.source, "manual")
            self.assertEqual(disabled_segment.version, 5)
            self.assertFalse(enabled_segment.project_sync_disabled)
            self.assertEqual(enabled_segment.version, 2)

    def test_project_sync_outbox_only_enqueues_confirmed_and_deduplicates(self):
        with self._session() as db:
            project = Project(name="Outbox project", source_language="zh-CN", target_language="en-US")
            file_record = FileRecord(
                project=project,
                filename="source.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            source_hash = build_source_hash("重复句段")
            unconfirmed = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="重复句段",
                source_hash=source_hash,
                display_text="重复句段",
                target_text="Draft",
                status="fuzzy",
                source="manual",
            )
            confirmed = Segment(
                file_record=file_record,
                sentence_id="s2",
                source_text="重复句段",
                source_hash=source_hash,
                display_text="重复句段",
                target_text="Approved",
                status="confirmed",
                source="manual",
                confirmed_at=datetime.now(),
            )
            db.add_all([project, file_record, unconfirmed, confirmed])
            db.commit()

            with patch("app.services.project_sync_outbox._project_sync_confirmed_only", return_value=True):
                queued = enqueue_project_segment_sync(
                    db,
                    file_record=file_record,
                    segments=[unconfirmed, confirmed],
                )
                queued_again = enqueue_project_segment_sync(
                    db,
                    file_record=file_record,
                    segments=[confirmed],
                )
            db.commit()

            rows = db.query(ProjectSegmentSyncOutbox).all()
            self.assertEqual(queued, 1)
            self.assertEqual(queued_again, 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].source_segment_id, confirmed.id)
            self.assertEqual(rows[0].source_version, int(confirmed.version or 1))
            self.assertEqual(rows[0].trigger_kind, "confirmed")
            self.assertEqual(rows[0].generation, 2)
            self.assertEqual(rows[0].status, "pending")

    def test_project_sync_outbox_accepts_unconfirmed_explicit_blur_trigger(self):
        with self._session() as db:
            project = Project(name="Blur project", source_language="en", target_language="zh-CN")
            file_record = FileRecord(
                project=project,
                filename="source.docx",
                source_language="en",
                target_language="zh-CN",
            )
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="same source",
                source_hash=build_source_hash("same source"),
                display_text="same source",
                target_text="失焦草稿",
                status="fuzzy",
                source="manual",
                version=4,
            )
            db.add_all([project, file_record, segment])
            db.commit()

            with patch(
                "app.services.project_sync_outbox.project_sync_blur_enabled_for_project",
                return_value=True,
            ):
                queued = enqueue_project_segment_sync(
                    db,
                    file_record=file_record,
                    segments=[segment],
                    trigger_kind="blur",
                )
            db.commit()

            row = db.query(ProjectSegmentSyncOutbox).one()
            self.assertEqual(queued, 1)
            self.assertEqual(row.trigger_kind, "blur")
            self.assertEqual(row.source_segment_id, segment.id)
            self.assertEqual(row.source_version, 4)

    def test_blur_trigger_endpoint_checks_saved_version_before_enqueue(self):
        with self._session() as db:
            user = User(username="blur-admin", hashed_password="x", role="admin")
            project = Project(name="Blur endpoint", source_language="en", target_language="zh-CN")
            file_record = FileRecord(
                project=project,
                filename="source.docx",
                source_language="en",
                target_language="zh-CN",
            )
            segment = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="same source",
                source_hash=build_source_hash("same source"),
                display_text="same source",
                target_text="已保存失焦译文",
                status="fuzzy",
                source="manual",
                version=5,
            )
            db.add_all([user, project, file_record, segment])
            db.commit()

            with (
                patch("app.routers.api.project_sync_blur_enabled_for_project", return_value=True),
                patch("app.routers.api._require_segment_work_access"),
                patch("app.routers.api.enqueue_project_segment_sync", return_value=1) as enqueue,
                patch("app.routers.api._schedule_project_segment_sync_processing") as schedule,
            ):
                response = api.trigger_segment_project_sync(
                    file_record_id=file_record.id,
                    sentence_id=segment.sentence_id,
                    payload=api.SegmentProjectSyncTrigger(expected_version=5),
                    background_tasks=BackgroundTasks(),
                    db=db,
                    current_user=user,
                    operation_token=None,
                )

            self.assertTrue(response.enabled)
            self.assertEqual(response.queued_count, 1)
            self.assertEqual(response.source_version, 5)
            self.assertEqual(enqueue.call_args.kwargs["trigger_kind"], "blur")
            schedule.assert_called_once()

            with patch("app.routers.api._require_segment_work_access"):
                with self.assertRaises(HTTPException) as raised:
                    api.trigger_segment_project_sync(
                        file_record_id=file_record.id,
                        sentence_id=segment.sentence_id,
                        payload=api.SegmentProjectSyncTrigger(expected_version=4),
                        background_tasks=BackgroundTasks(),
                        db=db,
                        current_user=user,
                        operation_token=None,
                    )
            self.assertEqual(raised.exception.status_code, 409)

    def test_project_sync_outbox_marks_completed_and_publishes_after_commit(self):
        with self._session() as db:
            project_id = uuid4()
            file_record_id = uuid4()
            row = ProjectSegmentSyncOutbox(
                project_id=project_id,
                source_language="zh-CN",
                target_language="en-US",
                source_hash=build_source_hash("重复句段"),
            )
            db.add(row)
            db.commit()

            summary = ProjectSegmentSyncSummary(affected_file_ids={file_record_id})
            with (
                patch("app.services.project_sync_outbox.sync_project_segments_for_hash", return_value=summary),
                patch("app.services.project_sync_outbox.publish_segment_changes") as publish,
            ):
                processed = process_project_sync_outbox(db)

            db.refresh(row)
            self.assertEqual(processed, 1)
            self.assertEqual(row.status, "completed")
            self.assertIsNotNone(row.processed_at)
            publish.assert_called_once_with({file_record_id})

    def test_project_sync_outbox_does_not_complete_reenqueued_claim(self):
        with self._session() as db:
            project_id = uuid4()
            row = ProjectSegmentSyncOutbox(
                project_id=project_id,
                source_language="zh-CN",
                target_language="en-US",
                source_hash=build_source_hash("并发重入句段"),
            )
            db.add(row)
            db.commit()
            row_id = row.id

            def reenqueue_while_processing(sync_db: Session, **_kwargs):
                reenqueued_at = datetime.now() + timedelta(seconds=1)
                sync_db.query(ProjectSegmentSyncOutbox).filter(
                    ProjectSegmentSyncOutbox.id == row_id,
                ).update(
                    {
                        "status": "pending",
                        "generation": 2,
                        "last_enqueued_at": reenqueued_at,
                        "updated_at": reenqueued_at,
                    },
                    synchronize_session=False,
                )
                return ProjectSegmentSyncSummary()

            with (
                patch(
                    "app.services.project_sync_outbox.sync_project_segments_for_hash",
                    side_effect=reenqueue_while_processing,
                ),
                patch("app.services.project_sync_outbox.publish_segment_changes") as publish,
            ):
                processed = process_project_sync_outbox(db)

            db.expire_all()
            refreshed = db.query(ProjectSegmentSyncOutbox).filter(
                ProjectSegmentSyncOutbox.id == row_id,
            ).one()
            self.assertEqual(processed, 1)
            # SQLite 测试把“重入”放在同一 SAVEPOINT 内，因此旧同步回滚时模拟更新也会回滚；
            # 生产环境的新入队来自独立事务，会保留 pending/generation=2。
            self.assertEqual(refreshed.status, "processing")
            self.assertEqual(refreshed.generation, 1)
            self.assertIsNone(refreshed.processed_at)
            publish.assert_not_called()

    def test_display_index_map_supports_partial_query_rows(self):
        with self._session() as db:
            file_record = FileRecord(
                filename="partial-rows.docx",
                source_language="zh-CN",
                target_language="en-US",
            )
            first = Segment(
                file_record=file_record,
                sentence_id="s1",
                source_text="甲",
                display_text="甲",
                target_text="",
                status="none",
                source="none",
                display_index=0,
            )
            second = Segment(
                file_record=file_record,
                sentence_id="s2",
                source_text="乙",
                display_text="乙",
                target_text="",
                status="none",
                source="none",
                display_index=1,
            )
            db.add_all([file_record, first, second])
            db.commit()

            partial_rows = db.query(Segment.id, Segment.sentence_id, Segment.status).all()
            index_map = api._get_segment_display_index_map(db, file_record.id, partial_rows)

            self.assertEqual(index_map, {first.id: 0, second.id: 1})


if __name__ == "__main__":
    unittest.main()
