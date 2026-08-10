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

TRANSLATION_ONLY_SOURCE_LABEL = "（增译，无对应原文）"


def _pair_indices(value: str | None) -> list[int]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _pair_segment_metadata(batch: ProofreadingBatch, pair: DocumentAlignmentPair) -> dict:
    src_indices = _pair_indices(pair.src_indices)
    tgt_indices = _pair_indices(pair.tgt_indices)
    return {
        "proofreading_batch_id": str(batch.id),
        "alignment_pair_id": str(pair.id),
        "alignment_pair_order": pair.pair_order,
        "src_indices": src_indices,
        "tgt_indices": tgt_indices,
        "translation_only": not src_indices,
        "confidence": pair.confidence,
        "method": pair.method,
    }


def _create_pair_segment(
    db: Session,
    batch: ProofreadingBatch,
    binding: ProofreadingColumnBinding,
    file_record: FileRecord,
    pair: DocumentAlignmentPair,
    *,
    workflow_step_id,
    sequence: int,
) -> tuple[Segment, ProofreadingSegmentBaseline]:
    metadata = _pair_segment_metadata(batch, pair)
    translation_only = bool(metadata["translation_only"])
    source_text = TRANSLATION_ONLY_SOURCE_LABEL if translation_only else pair.source_text
    source_hash_text = f"translation-only:{pair.id}" if translation_only else pair.source_text
    segment = Segment(
        file_record_id=file_record.id, workflow_step_id=workflow_step_id,
        sentence_id=f"align-{pair.pair_order:05d}", source_text=source_text,
        source_hash=build_source_hash(source_hash_text), display_text=source_text,
        target_text=pair.target_text, status="none",
        source=IMPORTED_TRANSLATION_SOURCE if pair.target_text else "none",
        source_word_count=0 if translation_only else count_source_words(pair.source_text),
        block_type=pair.block_type, block_index=pair.block_index,
        row_index=pair.row_index, cell_index=pair.cell_index,
        sequence_index=sequence, display_index=sequence,
        segment_metadata=json.dumps(metadata, ensure_ascii=False),
    )
    db.add(segment)
    db.flush()
    baseline = ProofreadingSegmentBaseline(
        batch_id=batch.id, binding_id=binding.id, segment_id=segment.id, sheet_index=0,
        row_index=pair.pair_order, source_cell_ref=f"S{pair.pair_order}",
        target_cell_ref=f"T{pair.pair_order}", original_target_text=pair.target_text,
    )
    db.add(baseline)
    return segment, baseline


def ensure_document_pair_segments_complete(db: Session, batch: ProofreadingBatch) -> int:
    """幂等补齐历史双文档批次中曾被跳过的增译，并恢复 pair_order 顺序。"""
    if batch.batch_kind != "document_pair" or batch.alignment_status != "confirmed":
        return 0
    binding = db.query(ProofreadingColumnBinding).filter_by(batch_id=batch.id).first()
    if binding is None:
        return 0
    file_record = db.get(FileRecord, binding.file_record_id)
    if file_record is None:
        return 0
    baselines = db.query(ProofreadingSegmentBaseline).filter_by(batch_id=batch.id).all()
    segments_by_pair_id: dict[str, Segment] = {}
    baselines_by_segment = {item.segment_id: item for item in baselines}
    for segment in db.query(Segment).filter_by(file_record_id=file_record.id).all():
        try:
            metadata = json.loads(segment.segment_metadata or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        pair_id = str(metadata.get("alignment_pair_id") or "")
        if pair_id:
            segments_by_pair_id[pair_id] = segment

    pairs = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(
        DocumentAlignmentPair.pair_order,
    ).all()
    workflow_step_id = batch.project.workflow_steps[0].id if batch.project.workflow_steps else None
    added = 0
    ordered_segments: list[Segment] = []
    for pair in pairs:
        segment = segments_by_pair_id.get(str(pair.id))
        if segment is None:
            segment, baseline = _create_pair_segment(
                db, batch, binding, file_record, pair,
                workflow_step_id=workflow_step_id, sequence=len(ordered_segments),
            )
            added += 1
            baselines_by_segment[segment.id] = baseline
        else:
            metadata = _pair_segment_metadata(batch, pair)
            segment.segment_metadata = json.dumps(metadata, ensure_ascii=False)
            if metadata["translation_only"]:
                segment.source_text = TRANSLATION_ONLY_SOURCE_LABEL
                segment.display_text = TRANSLATION_ONLY_SOURCE_LABEL
                segment.source_hash = build_source_hash(f"translation-only:{pair.id}")
                segment.source_word_count = 0
        ordered_segments.append(segment)
        baseline = baselines_by_segment.get(segment.id)
        if baseline is None:
            baseline = ProofreadingSegmentBaseline(
                batch_id=batch.id,
                binding_id=binding.id,
                segment_id=segment.id,
                sheet_index=0,
                row_index=pair.pair_order,
                source_cell_ref=f"S{pair.pair_order}",
                target_cell_ref=f"T{pair.pair_order}",
                original_target_text=pair.target_text,
            )
            db.add(baseline)
            baselines_by_segment[segment.id] = baseline
        baseline.row_index = pair.pair_order
    for sequence, segment in enumerate(ordered_segments):
        segment.sequence_index = sequence
        segment.display_index = sequence
    batch.total_segments = len(ordered_segments)
    if added and batch.status == "ready":
        translation_only_count = sum(not _pair_indices(pair.src_indices) for pair in pairs)
        batch.message = (
            f"对齐已确认，当前共有 {len(ordered_segments)} 个句段，"
            f"其中增译 {translation_only_count} 条均已保留并可编辑。"
        )
    db.flush()
    return added


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
    for pair in pairs:
        _create_pair_segment(
            db, batch, binding, file_record, pair,
            workflow_step_id=first_step, sequence=sequence,
        )
        sequence += 1
    batch.alignment_status = "confirmed"
    batch.status = "ready"
    batch.progress = 100
    batch.total_segments = sequence
    batch.skipped_segments = sum(not pair.target_text and bool(pair.source_text) for pair in pairs)
    additions = sum(not _pair_indices(pair.src_indices) for pair in pairs)
    batch.message = f"对齐已确认，已生成 {sequence} 个句段，其中增译 {additions} 条。"
    project.status = "in_progress"
    db.commit()
    db.refresh(file_record)
    return file_record
