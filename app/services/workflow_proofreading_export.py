"""普通翻译工作流的校对交付快照与 Word 导出。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import FileRecord, ProofreadingBatch, ProofreadingSegmentBaseline, Segment
from app.services.file_record_service import (
    get_file_record_source_filename, get_file_record_source_path,
    get_segment_ordering_for_file_record,
)
from app.services.workflow_proofreading import batch_config


@dataclass(frozen=True)
class WorkflowProofreadingExportSnapshot:
    filename: str
    source_bytes: bytes | None
    target_language: str
    document_parse_mode: str
    document_parse_options: Any
    segments: list[dict[str, Any]]
    revisions: list[dict[str, Any]]


def workflow_revision_export_unavailable_reason(file: FileRecord | None) -> str:
    if file is None:
        return "校对批次没有可用的原文件绑定。"
    filename = get_file_record_source_filename(file)
    if Path(filename).suffix.lower() in {".doc", ".docx"} and get_file_record_source_path(file) is None:
        return "Word 源文件缺失，无法生成保留排版的校对修订版。"
    return ""


def prepare_workflow_proofreading_export(
    db: Session, batch: ProofreadingBatch, file: FileRecord,
) -> WorkflowProofreadingExportSnapshot:
    if batch.batch_kind != "workflow_segments":
        raise ValueError("该导出格式仅用于普通翻译工作流的校对阶段。")
    reason = workflow_revision_export_unavailable_reason(file)
    if reason:
        raise ValueError(reason)
    step_id = str(batch_config(batch).get("workflow_step_id") or "")
    # 一条联接查询同时读取当前译文、阶段和基线，避免混用人工编辑前后的数据。
    items = db.query(Segment, ProofreadingSegmentBaseline).outerjoin(
        ProofreadingSegmentBaseline,
        and_(ProofreadingSegmentBaseline.segment_id == Segment.id,
             ProofreadingSegmentBaseline.batch_id == batch.id),
    ).filter(Segment.file_record_id == file.id).order_by(
        *get_segment_ordering_for_file_record(file),
    ).all()
    if not items:
        raise ValueError("当前文件没有可导出的句段。")
    segments: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    sentence_ids: set[str] = set()
    active_count = 0
    for segment, baseline in items:
        sentence_id = str(segment.sentence_id or "")
        if not sentence_id or sentence_id in sentence_ids:
            raise ValueError("句段定位标识缺失或重复，无法安全生成校对修订 Word。")
        sentence_ids.add(sentence_id)
        payload = {column.key: getattr(segment, column.key) for column in Segment.__table__.columns}
        # 空译文明确标记；有删除修订时则保留真实空字符串。
        payload["target_text"] = segment.target_text or "【译文缺失】"
        if str(segment.workflow_step_id) == step_id:
            if baseline is None:
                raise ValueError("部分校对句段缺少比较基线，请刷新工作台补齐后再导出。")
            active_count += 1
            before, after = baseline.original_target_text or "", segment.target_text or ""
            if before != after:
                payload["target_text"] = after
                payload["target_html"] = None
                revisions.append({
                    "id": f"proofreading-{batch.id}-{segment.id}", "sentence_id": sentence_id,
                    "before_text": before, "after_text": after,
                    "status": "pending", "source": "manual", "author": {"name": "校对工作流"},
                })
        segments.append(payload)
    if not active_count:
        raise ValueError("当前文件没有处于校对阶段的句段。")
    filename = get_file_record_source_filename(file)
    source_path = get_file_record_source_path(file)
    return WorkflowProofreadingExportSnapshot(
        filename=filename,
        source_bytes=source_path.read_bytes() if source_path and Path(filename).suffix.lower() in {".doc", ".docx"} else None,
        target_language=file.target_language or batch.target_language or "",
        document_parse_mode=file.document_parse_mode or "full",
        document_parse_options=file.document_parse_options,
        segments=segments, revisions=revisions,
    )


def render_workflow_proofreading_docx(snapshot: WorkflowProofreadingExportSnapshot) -> tuple[bytes, str]:
    """只接受普通数据快照；文档渲染期间不再访问数据库。"""
    from app.services.document_alignment.export import _docx_package_is_well_formed
    from app.services.document_exporter import export_revision_paragraphs_docx
    from app.services.task_file_service import export_translated_task_file

    if Path(snapshot.filename).suffix.lower() in {".doc", ".docx"}:
        exported = export_translated_task_file(
            raw_bytes=snapshot.source_bytes, filename=snapshot.filename, segments=snapshot.segments,
            document_parse_mode=snapshot.document_parse_mode,
            document_parse_options=snapshot.document_parse_options,
            target_language=snapshot.target_language, revisions=snapshot.revisions,
            include_revision_marks=True, require_revision_marks=True,
        )
        content = exported.content
    else:
        content = export_revision_paragraphs_docx(
            snapshot.segments, snapshot.revisions, target_language=snapshot.target_language,
        )
    if not _docx_package_is_well_formed(content):
        raise ValueError("校对修订 Word 生成失败，请检查句段内容后重试。")
    return content, f"{Path(snapshot.filename).stem}_校对修订版.docx"
