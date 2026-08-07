from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import (
    DocumentAlignmentPair, FileRecord, ProofreadingBatch, ProofreadingColumnBinding,
    ProofreadingSegmentBaseline, Segment, User,
)
from app.services.analytics_service import count_source_words
from app.services.document_storage import save_source_file
from app.services.normalizer import build_source_hash
from app.services.proofreading import IMPORTED_TRANSLATION_SOURCE
from app.services.reference_sync_service import attach_project_reference_bases_to_file

from .service import _source_cache_path, validate_pair_integrity


def materialize_alignment(db: Session, batch: ProofreadingBatch, *, current_user: User) -> FileRecord:
    if batch.batch_kind != "document_pair" or batch.alignment_status != "draft":
        raise ValueError("当前批次没有可确认的对齐草稿。")
    validate_pair_integrity(db, batch.id)
    existing = db.query(FileRecord).filter(
        FileRecord.project_id == batch.project_id,
        FileRecord.document_parse_options.contains(str(batch.id)),
    ).first()
    if existing:
        raise ValueError("该对齐批次已经生成过句段。")
    source_path = _source_cache_path(batch.id, batch.filename)
    if not source_path.is_file():
        raise ValueError("原文文档缓存已失效，请重新上传。")
    project = batch.project
    file_record = FileRecord(
        project_id=batch.project_id, filename=batch.filename, file_hash=batch.file_hash,
        status="in_progress", document_parse_mode="full",
        document_parse_options=json.dumps({"alignment_mode": "document_pair", "proofreading_batch_id": str(batch.id)}, ensure_ascii=False),
        creator_id=current_user.id, deadline=project.deadline, access_level=project.access_level,
        source_language=batch.source_language, target_language=batch.target_language,
    )
    db.add(file_record)
    db.flush()
    save_source_file(file_record.id, batch.filename, source_path.read_bytes())
    attach_project_reference_bases_to_file(db, file_record)
    # generate_batch 以 binding 为语言分组；双文档批次使用一个内部 binding 即可原样复用整条校对链路。
    binding = ProofreadingColumnBinding(
        batch_id=batch.id, file_record_id=file_record.id, sheet_index=0,
        sheet_name="双文档对齐", header_row=1, source_column=1, target_column=2,
        output_column=3, source_header="原文", target_header="译文",
        target_language=batch.target_language,
    )
    db.add(binding)
    db.flush()
    first_step = project.workflow_steps[0].id if project.workflow_steps else None
    pairs = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(DocumentAlignmentPair.pair_order).all()
    sequence = 0
    additions = 0
    for pair in pairs:
        src_indices = json.loads(pair.src_indices or "[]")
        tgt_indices = json.loads(pair.tgt_indices or "[]")
        if not src_indices:
            additions += 1
            continue
        metadata = {
            "proofreading_batch_id": str(batch.id), "alignment_pair_id": str(pair.id),
            "src_indices": src_indices, "tgt_indices": tgt_indices,
            "confidence": pair.confidence, "method": pair.method,
        }
        segment = Segment(
            file_record_id=file_record.id, workflow_step_id=first_step,
            sentence_id=f"align-{pair.pair_order:05d}", source_text=pair.source_text,
            source_hash=build_source_hash(pair.source_text), display_text=pair.source_text,
            target_text=pair.target_text, status="none",
            source=IMPORTED_TRANSLATION_SOURCE if pair.target_text else "none",
            source_word_count=count_source_words(pair.source_text), block_type=pair.block_type,
            block_index=pair.block_index, row_index=pair.row_index, cell_index=pair.cell_index,
            sequence_index=sequence, display_index=sequence,
            segment_metadata=json.dumps(metadata, ensure_ascii=False),
        )
        db.add(segment)
        db.flush()
        db.add(ProofreadingSegmentBaseline(
            batch_id=batch.id, binding_id=binding.id, segment_id=segment.id, sheet_index=0,
            row_index=pair.pair_order, source_cell_ref=f"S{pair.pair_order}",
            target_cell_ref=f"T{pair.pair_order}", original_target_text=pair.target_text,
        ))
        sequence += 1
    batch.alignment_status = "confirmed"
    batch.status = "ready"
    batch.progress = 100
    batch.total_segments = sequence
    batch.skipped_segments = sum(not pair.target_text and bool(pair.source_text) for pair in pairs)
    batch.message = f"对齐已确认，已生成 {sequence} 个句段；另有 {additions} 条增译仅保留在对齐报告中。"
    project.status = "in_progress"
    db.commit()
    db.refresh(file_record)
    return file_record
