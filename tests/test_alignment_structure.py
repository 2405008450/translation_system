from __future__ import annotations

import json
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import (
    DocumentAlignmentPair, DocumentAlignmentUnit, FileRecord, Project, ProjectWorkflowStep,
    ProofreadingBatch, ProofreadingColumnBinding, ProofreadingSegmentBaseline,
    Segment, SegmentComment, SegmentQAIssue, SegmentRevision, User,
)
from app.routers.document_alignment import PairRangeReplace, PairSplit, replace_pair_range, split_pair
from app.services.document_alignment.service import replace_alignment_pair_range, validate_pair_integrity
from app.services.document_alignment.segments import ensure_document_pair_segments_complete


class AlignmentStructureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        tables = [User.__table__, Project.__table__, ProjectWorkflowStep.__table__, FileRecord.__table__,
                  Segment.__table__, SegmentRevision.__table__, SegmentComment.__table__, SegmentQAIssue.__table__,
                  ProofreadingBatch.__table__, DocumentAlignmentPair.__table__, DocumentAlignmentUnit.__table__,
                  ProofreadingColumnBinding.__table__, ProofreadingSegmentBaseline.__table__]
        # 测试库由 ORM 生成 UUID，临时移除 PostgreSQL 专用的默认表达式。
        defaults = [(column, column.server_default) for table in tables for column in table.columns
                    if column.server_default is not None and "::uuid" in str(column.server_default.arg)]
        try:
            for column, _ in defaults:
                column.server_default = None
            for table in tables:
                table.create(self.engine)
        finally:
            for column, default in defaults:
                column.server_default = default
        self.db = Session(self.engine)
        self.user = User(username="admin", hashed_password="x", role="admin")
        self.project = Project(name="对齐结构回归", workflow_template_id="proofread")
        self.db.add_all([self.user, self.project])
        self.db.flush()
        self.db.add(ProjectWorkflowStep(project_id=self.project.id, step_key="proofread", name="校对",
                                        step_type="proofread", sort_order=0))
        self.db.commit()

    async def asyncTearDown(self):
        self.db.close()
        self.engine.dispose()

    def _structure_fixture(self, groups=None, confirmed=False):
        groups = groups or [([0, 1], [0, 1]), ([2], [2])]
        batch = ProofreadingBatch(project_id=self.project.id, created_by_id=self.user.id,
            filename="structure.docx", file_hash="structure-test", source_language="zh-CN", target_language="en-US",
            batch_kind="document_pair", alignment_status="confirmed" if confirmed else "draft",
            workflow_stage="alignment", status="ready" if confirmed else "draft")
        self.db.add(batch)
        self.db.flush()
        for side, position in (("source", 0), ("target", 1)):
            for index in sorted({i for group in groups for i in group[position]}):
                self.db.add(DocumentAlignmentUnit(batch_id=batch.id, side=side, unit_index=index,
                    para_index=index, block_type="paragraph", block_index=index,
                    text=f"原文{index}" if side == "source" else f"Target {index}"))
        pairs = []
        for order, (src, tgt) in enumerate(groups):
            pair = DocumentAlignmentPair(batch_id=batch.id, pair_order=order,
                src_indices=json.dumps(src), tgt_indices=json.dumps(tgt),
                source_text="\n".join(f"原文{i}" for i in src), target_text="\n".join(f"Target {i}" for i in tgt),
                confidence=0.8, confidence_level="high", method="dp", features="{}")
            self.db.add(pair)
            pairs.append(pair)
        if confirmed:
            file_record = FileRecord(project_id=self.project.id, filename="structure.docx", file_hash="structure-test",
                status="in_progress", creator_id=self.user.id, source_language="zh-CN", target_language="en-US",
                document_parse_mode="full", document_parse_options="{}")
            self.db.add(file_record)
            self.db.flush()
            self.db.add(ProofreadingColumnBinding(batch_id=batch.id, file_record_id=file_record.id,
                sheet_index=0, sheet_name="双文档对齐", header_row=1, source_column=1, target_column=2,
                output_column=3, source_header="原文", target_header="译文", target_language="en-US"))
            self.db.flush()
            ensure_document_pair_segments_complete(self.db, batch)
        self.db.commit()
        return batch, pairs

    async def test_structure_stale_ids_return_conflict_and_leave_pairs_unchanged(self):
        batch, pairs = self._structure_fixture()
        payload = PairRangeReplace(start_order=0, delete_count=1, expected_pair_ids=[pairs[1].id],
            replacements=[{"src_indices": [0], "tgt_indices": [0]}, {"src_indices": [1], "tgt_indices": [1]}])
        with self.assertRaises(HTTPException) as failure:
            replace_pair_range(batch.id, payload, self.db, self.user)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual([pair.id for pair in self.db.query(DocumentAlignmentPair).order_by(DocumentAlignmentPair.pair_order)],
                         [pair.id for pair in pairs])

    async def test_legacy_split_rejects_indivisible_manual_target_without_copying_it(self):
        batch, pairs = self._structure_fixture()
        pairs[0].target_text = "人工修改，没有明确边界"
        pairs[0].features = json.dumps({"manual_target_text": pairs[0].target_text}, ensure_ascii=False)
        self.db.commit()
        with self.assertRaises(HTTPException) as failure:
            split_pair(batch.id, PairSplit(pair_id=pairs[0].id, src_at=1, tgt_at=1), self.db, self.user)
        self.assertEqual(failure.exception.status_code, 400)
        self.assertIn("无损", failure.exception.detail)
        self.assertEqual(self.db.query(DocumentAlignmentPair).count(), 2)
        self.assertEqual(self.db.get(DocumentAlignmentPair, pairs[0].id).target_text, "人工修改，没有明确边界")

    async def test_same_pair_id_with_new_text_rejects_stale_snapshot(self):
        batch, pairs = self._structure_fixture()
        snapshot = {"source_text": pairs[0].source_text, "target_text": pairs[0].target_text}
        pairs[0].target_text = "其他会话刚保存的修改"
        pairs[0].features = json.dumps({"manual_target_text": pairs[0].target_text}, ensure_ascii=False)
        self.db.commit()
        with self.assertRaises(HTTPException) as failure:
            replace_pair_range(batch.id, PairRangeReplace(start_order=0, delete_count=1,
                expected_pair_ids=[pairs[0].id], expected_texts=[snapshot],
                replacements=[{"src_indices": [0, 1], "tgt_indices": [0, 1], "target_text": "过期的覆盖"}]),
                self.db, self.user)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.db.get(DocumentAlignmentPair, pairs[0].id).target_text, "其他会话刚保存的修改")

    async def test_manual_text_merge_and_snapshot_restore_are_lossless(self):
        batch, pairs = self._structure_fixture(groups=[([0], [0]), ([1], [1])])
        pairs[0].target_text = "人工译文甲"
        pairs[0].features = json.dumps({"manual_target_text": "人工译文甲"}, ensure_ascii=False)
        pairs[1].target_text = "人工译文乙"
        pairs[1].features = json.dumps({"manual_target_text": "人工译文乙"}, ensure_ascii=False)
        self.db.commit()
        before = [{"src_indices": [i], "tgt_indices": [i], "locked": False,
                   "source_text": pair.source_text, "target_text": pair.target_text} for i, pair in enumerate(pairs)]
        merged = replace_alignment_pair_range(self.db, batch.id, 0, 2,
            [{"src_indices": [0, 1], "tgt_indices": [0, 1], "locked": True}], expected_pair_ids=[pair.id for pair in pairs])
        self.assertEqual(merged[0].target_text, "人工译文甲\n人工译文乙")
        restored = replace_alignment_pair_range(self.db, batch.id, 0, 1, before, expected_pair_ids=[merged[0].id])
        self.assertEqual([pair.target_text for pair in restored], ["人工译文甲", "人工译文乙"])
        validate_pair_integrity(self.db, batch.id)

    async def test_split_rebuild_and_repeated_materialization_keep_unique_baselines(self):
        batch, pairs = self._structure_fixture(confirmed=True)
        result = replace_pair_range(batch.id, PairRangeReplace(start_order=0, delete_count=1,
            expected_pair_ids=[pairs[0].id], replacements=[{"src_indices": [0], "tgt_indices": [0]},
            {"src_indices": [1], "tgt_indices": [1]}]), self.db, self.user)
        for _ in range(3):
            ensure_document_pair_segments_complete(self.db, batch, refresh_existing=True)
        rows = self.db.query(Segment).order_by(Segment.sequence_index).all()
        self.assertEqual(len(rows), 3)
        self.assertEqual([row.target_text for row in rows], ["Target 0", "Target 1", "Target 2"])
        self.assertEqual(len({row.sentence_id for row in rows}), 3)
        baselines = self.db.query(ProofreadingSegmentBaseline).all()
        self.assertEqual(len(baselines), 3)
        self.assertEqual({row.segment_id for row in baselines}, {row.id for row in rows})
        self.assertEqual({item["id"] for item in result["items"]},
            {json.loads(row.segment_metadata)["alignment_pair_id"] for row in rows[:2]})

    async def test_asymmetric_split_does_not_copy_single_target_to_both_rows(self):
        batch, pairs = self._structure_fixture(groups=[([0, 1], [0])])
        result = split_pair(batch.id, PairSplit(pair_id=pairs[0].id, src_at=1, tgt_at=1), self.db, self.user)
        self.assertEqual([row["target_text"] for row in result["items"]], ["Target 0", ""])
        self.assertEqual([row["tgt_indices"] for row in result["items"]], [[0], []])
        validate_pair_integrity(self.db, batch.id)

    async def test_translation_only_split_keeps_each_target_unit_once(self):
        batch, pairs = self._structure_fixture(groups=[([], [0, 1])])
        result = split_pair(batch.id, PairSplit(pair_id=pairs[0].id, src_at=0, tgt_at=1), self.db, self.user)
        self.assertEqual([row["src_indices"] for row in result["items"]], [[], []])
        self.assertEqual([row["target_text"] for row in result["items"]], ["Target 0", "Target 1"])
        validate_pair_integrity(self.db, batch.id)

    async def test_repeated_request_after_commit_returns_conflict_without_extra_rows(self):
        batch, pairs = self._structure_fixture(confirmed=True)
        payload = PairRangeReplace(start_order=0, delete_count=1, expected_pair_ids=[pairs[0].id],
            replacements=[{"src_indices": [0], "tgt_indices": [0]}, {"src_indices": [1], "tgt_indices": [1]}])
        replace_pair_range(batch.id, payload, self.db, self.user)
        with self.assertRaises(HTTPException) as failure:
            replace_pair_range(batch.id, payload, self.db, self.user)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(self.db.query(DocumentAlignmentPair).count(), 3)
        self.assertEqual(self.db.query(Segment).count(), 3)
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 3)

    async def test_identical_document_text_in_distinct_units_is_preserved(self):
        batch, pairs = self._structure_fixture(groups=[([0], [0]), ([1], [1])], confirmed=True)
        for pair in pairs:
            pair.target_text = "合法重复的文档内容"
        for unit in self.db.query(DocumentAlignmentUnit).filter_by(batch_id=batch.id, side="target"):
            unit.text = "合法重复的文档内容"
        self.db.commit()
        ensure_document_pair_segments_complete(self.db, batch, refresh_existing=True)
        self.assertEqual([row.target_text for row in self.db.query(Segment)], ["合法重复的文档内容"] * 2)
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 2)

    async def test_invalid_duplicate_indices_roll_back_structure_and_materialization(self):
        batch, pairs = self._structure_fixture(confirmed=True)
        with self.assertRaises(HTTPException):
            replace_pair_range(batch.id, PairRangeReplace(start_order=0, delete_count=1,
                expected_pair_ids=[pairs[0].id], replacements=[{"src_indices": [0, 1], "tgt_indices": [0, 1]},
                {"src_indices": [], "tgt_indices": [1]}]), self.db, self.user)
        self.assertEqual(self.db.query(DocumentAlignmentPair).count(), 2)
        self.assertEqual(self.db.query(Segment).count(), 2)
        self.assertEqual(self.db.query(ProofreadingSegmentBaseline).count(), 2)

