"""为普通翻译工作流关联校对批次，不复制文件或句段。"""
from __future__ import annotations


import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth import can_access_all_projects
from app.models import FileAssignment, FileRecord, ProjectWorkflowStep, ProofreadingBatch, ProofreadingColumnBinding, ProofreadingSegmentBaseline, Segment, TranslationReviewReportItem, User
from app.services.workflow_capabilities import WORKFLOW_PROOFREADING_KIND, is_standalone_proofreading, proofreading_step_id

ACTIVE_STATUSES = {"queued", "running", "canceling"}


def batch_config(batch: ProofreadingBatch) -> dict:
    try:
        value = json.loads(batch.config_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def workflow_batch_file(db: Session, batch: ProofreadingBatch) -> FileRecord | None:
    binding = db.query(ProofreadingColumnBinding).filter_by(batch_id=batch.id).first()
    return db.get(FileRecord, binding.file_record_id) if binding else None


def can_read_workflow_file(db: Session, file: FileRecord, user: User) -> bool:
    return can_access_all_projects(user) or db.query(FileAssignment.id).filter_by(
        file_record_id=file.id, assignee_id=user.id, status="active",
    ).first() is not None


def eligible_workflow_segments(db: Session, batch: ProofreadingBatch, user: User | None, *, include_protected: bool = False) -> list[Segment]:
    if user is None or not user.is_active:
        return []
    # 沿用工作台已有的阶段、显示序号及分配区间权限。
    from app.routers.api import _filter_writable_segments
    file_ids = {binding.file_record_id for binding in db.query(ProofreadingColumnBinding).filter_by(batch_id=batch.id)}
    result = []
    for file_id in file_ids:
        file = db.get(FileRecord, file_id)
        if file is None:
            continue
        step_id = batch_config(batch).get("workflow_step_id") or proofreading_step_id(file.project)
        if not step_id:
            continue
        segments = db.query(Segment).filter(
            Segment.file_record_id == file.id,
            Segment.workflow_step_id == UUID(str(step_id)),
            ((Segment.status != "confirmed") & (Segment.auto_write_protection != "protected"))
            | ((Segment.auto_write_protection == "protected") if include_protected else False),
        ).all()
        result.extend(segment for segment in _filter_writable_segments(db, file, user, segments)
                      if (segment.target_text or "").strip())
    return result


def prepare_workflow_proofreading(
    db: Session, file: FileRecord, user: User, *, refresh_segment_ids: set[UUID] | None = None,
) -> ProofreadingBatch | None:
    if file.project_id is None or is_standalone_proofreading(file.project):
        return None
    # 同一文件的准备与生成入口串行，避免并发创建重复批次和基线。
    db.query(FileRecord).filter_by(id=file.id).with_for_update().one()
    step = db.query(ProjectWorkflowStep).filter_by(project_id=file.project_id, step_type="proofread").order_by(
        ProjectWorkflowStep.sort_order.desc(),
    ).first()
    if step is None:
        return None
    segments = db.query(Segment).filter_by(file_record_id=file.id, workflow_step_id=step.id).all()
    if not segments:
        return None
    binding = db.query(ProofreadingColumnBinding).join(ProofreadingBatch).filter(
        ProofreadingColumnBinding.file_record_id == file.id,
        ProofreadingBatch.batch_kind == WORKFLOW_PROOFREADING_KIND,
    ).first()
    if binding is None:
        batch = ProofreadingBatch(
            project_id=file.project_id, created_by_id=user.id, filename=file.filename,
            source_language=file.source_language or file.project.source_language,
            target_language=file.target_language or file.project.target_language or "",
            batch_kind=WORKFLOW_PROOFREADING_KIND, workflow_stage="proofreading", status="ready",
            config_json=json.dumps({"workflow_step_id": str(step.id)}, ensure_ascii=False),
        )
        db.add(batch)
        db.flush()
        binding = ProofreadingColumnBinding(
            batch_id=batch.id, file_record_id=file.id, sheet_index=0, sheet_name="工作流校对",
            header_row=1, source_column=1, target_column=2, output_column=3,
            source_header="原文", target_header="审校后译文", target_language=batch.target_language,
        )
        db.add(binding)
        db.flush()
    else:
        batch = db.get(ProofreadingBatch, binding.batch_id)
    baselines = {item.segment_id: item for item in db.query(ProofreadingSegmentBaseline).filter_by(batch_id=batch.id)}
    changed = False
    for segment in segments:
        baseline = baselines.get(segment.id)
        if baseline is None:
            baseline = ProofreadingSegmentBaseline(
                batch_id=batch.id, binding_id=binding.id, segment_id=segment.id, sheet_index=0,
                row_index=int(segment.display_index or 0), source_cell_ref="", target_cell_ref="",
                original_target_text=segment.target_text or "",
            )
            db.add(baseline)
            changed = True
        elif segment.id in (refresh_segment_ids or set()):
            baseline.original_target_text = segment.target_text or ""
            baseline.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            segment.llm_provider = None
            segment.llm_model = None
            db.query(TranslationReviewReportItem).filter_by(
                segment_id=segment.id, category_key="generation_error", status="open",
            ).update({"status": "resolved"}, synchronize_session=False)
            changed = True
        baseline.row_index = int(segment.display_index or 0)
    batch.total_segments = len(segments)
    if changed and batch.status not in ACTIVE_STATUSES:
        batch.status = "ready"
        batch.message = "已准备校对句段，请选择模型并手动开始。"
    db.flush()
    return batch
