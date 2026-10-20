from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.services.workflow_capabilities import is_standalone_proofreading
from app.services.workflow_proofreading import (
    batch_config, can_read_workflow_file, eligible_workflow_segments,
    prepare_workflow_proofreading,
)
from app.auth import can_access_all_projects, get_current_user, require_business_manager
from app.database import get_db
from app.models import (
    FileAssignment,
    FileRecord,
    Project,
    ProofreadingBatch,
    ProofreadingColumnBinding,
    ProofreadingSegmentBaseline,
    Segment,
    TranslationReviewReport,
    TranslationReviewReportItem,
    User,
)
from app.services.import_task_storage import (
    cleanup_expired_import_staging,
    cleanup_import_task_staging,
    get_import_task_staging_dir,
    stage_import_file_streams,
)
from app.services.proofreading import (
    create_batch_from_workbook,
    get_latest_generation_error_segment_ids,
    load_exported_batch,
    preview_workbook,
    run_generate_batch,
    run_export_batch,
    serialize_batch,
)
from app.services.llm_service import LLMConfigurationError, validate_provider_choice
from app.services.document_alignment.segments import ensure_document_pair_segments_complete
from app.services.document_alignment.export import build_export_readiness, export_batch_is_busy, is_xlsx_pair, target_revision_export_available
from app.services.file_export_queue import queue_file_export
from app.services.file_record_service import delete_proofreading_batch

router = APIRouter()


class ProofreadingTargetMapping(BaseModel):
    target_column: int
    target_language: str


class ProofreadingSheetMapping(BaseModel):
    sheet_index: int
    header_row: int
    source_column: int
    targets: list[ProofreadingTargetMapping] = Field(default_factory=list)


class ProofreadingBatchCreateRequest(BaseModel):
    preview_token: str
    source_language: str
    mappings: list[ProofreadingSheetMapping] = Field(default_factory=list)


class ProofreadingGenerateRequest(BaseModel):
    provider: Literal["auto", "deepseek", "openrouter"] = "auto"
    model: str | None = None
    user_instructions: str = Field(default="", max_length=12000)
    retry_scope: Literal["all", "failed_only"] = "all"


class ProofreadingExportTaskRequest(BaseModel):
    format: Literal[
        "proofreading_docx_target_revisions",
        "proofreading_docx_layout",
        "proofreading_docx_ordered",
        "proofreading_audit_xlsx",
        "proofreading_xlsx_original",
        "proofreading_xlsx_target",
    ]
    acknowledge_warnings: bool = False


def _get_project_or_404(db: Session, project_id: UUID) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在。")
    return project


def _get_batch_or_404(db: Session, batch_id: UUID) -> ProofreadingBatch:
    batch = db.query(ProofreadingBatch).filter(ProofreadingBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="校对批次不存在。")
    return batch


def _require_batch_access(db: Session, batch: ProofreadingBatch, user: User) -> None:
    if can_access_all_projects(user):
        return
    files = db.query(FileRecord).join(ProofreadingColumnBinding).filter(ProofreadingColumnBinding.batch_id == batch.id).all()
    if not any(can_read_workflow_file(db, file, user) for file in files):
        raise HTTPException(404, "校对任务不存在或未分配给当前用户。")


@router.post("/file-records/{file_record_id}/workflow/proofreading/prepare")
def prepare_file_workflow_proofreading(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file = db.get(FileRecord, file_record_id)
    if file is None or not can_read_workflow_file(db, file, current_user):
        raise HTTPException(404, "任务不存在或未分配给当前用户。")
    batch = prepare_workflow_proofreading(db, file, current_user)
    db.commit()
    return {"batch_id": str(batch.id) if batch else None}


def _load_preview_file(preview_token: str) -> tuple[str, bytes]:
    token = (preview_token or "").strip()
    if not token or any(char not in "0123456789abcdef-" for char in token.lower()):
        raise HTTPException(status_code=400, detail="预览令牌无效。")
    task_dir = get_import_task_staging_dir(token)
    files = sorted(path for path in task_dir.iterdir() if path.is_file()) if task_dir.exists() else []
    if len(files) != 1:
        raise HTTPException(status_code=410, detail="预览文件已过期，请重新上传。")
    staged_path = files[0]
    original_name = staged_path.name.split("_", 1)[1] if "_" in staged_path.name else staged_path.name
    return original_name, staged_path.read_bytes()


@router.post("/projects/{project_id}/proofreading/preview")
async def preview_proofreading_workbook(
    project_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_business_manager),
):
    project = _get_project_or_404(db, project_id)
    if not is_standalone_proofreading(project):
        raise HTTPException(status_code=400, detail="当前项目不是“校对”工作流。")
    filename = file.filename or "proofreading.xlsx"
    if Path(filename).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="校对工作流一期仅支持 .xlsx 文件。")
    cleanup_expired_import_staging()
    preview_token = str(uuid4())
    try:
        staged = await asyncio.to_thread(
            stage_import_file_streams,
            preview_token,
            [(filename, file.file)],
            max_files=1,
        )
        raw_bytes = Path(str(staged[0]["path"])).read_bytes()
        result = await asyncio.to_thread(preview_workbook, raw_bytes, filename)
    except Exception as exc:  # noqa: BLE001
        cleanup_import_task_staging(preview_token)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**result, "preview_token": preview_token}


@router.post("/projects/{project_id}/proofreading-batches")
def create_proofreading_batch(
    project_id: UUID,
    payload: ProofreadingBatchCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_business_manager),
):
    project = _get_project_or_404(db, project_id)
    filename, raw_bytes = _load_preview_file(payload.preview_token)
    try:
        batch = create_batch_from_workbook(
            db,
            project=project,
            current_user=current_user,
            raw_bytes=raw_bytes,
            filename=filename,
            source_language=payload.source_language,
            mappings=[mapping.model_dump() for mapping in payload.mappings],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cleanup_import_task_staging(payload.preview_token)
    return serialize_batch(db, batch)


@router.get("/projects/{project_id}/proofreading-batches")
def list_proofreading_batches(
    project_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_business_manager),
):
    _get_project_or_404(db, project_id)
    batches = (
        db.query(ProofreadingBatch)
        .filter(ProofreadingBatch.project_id == project_id)
        .order_by(ProofreadingBatch.created_at.desc())
        .all()
    )
    return {"items": [serialize_batch(db, batch) for batch in batches]}


@router.get("/proofreading-batches/{batch_id}")
def get_proofreading_batch(
    batch_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    batch = _get_batch_or_404(db, batch_id)
    _require_batch_access(db, batch, current_user)
    payload = serialize_batch(db, batch)
    if not can_access_all_projects(current_user):
        payload["bindings"] = [binding for binding in payload["bindings"]
                               if can_read_workflow_file(db, db.get(FileRecord, UUID(binding["file_record_id"])), current_user)]
    return payload


@router.delete("/proofreading-batches/{batch_id}")
def remove_proofreading_batch(
    batch_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_business_manager),
):
    """删除校对批次及其对齐数据、绑定的语言任务文件。"""
    batch = _get_batch_or_404(db, batch_id)
    if batch.status in {"aligning", "queued", "running", "canceling"}:
        raise HTTPException(status_code=409, detail="任务正在运行，请先暂停或取消后再删除。")
    if batch.export_status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="导出任务正在运行，请稍后再删除。")
    delete_proofreading_batch(db, batch)
    return {"message": "任务已删除。"}


@router.post("/proofreading-batches/{batch_id}/generate")
def generate_proofreading_batch(
    batch_id: UUID,
    payload: ProofreadingGenerateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    batch = db.query(ProofreadingBatch).filter_by(id=batch_id).with_for_update().populate_existing().first()
    if batch is None:
        raise HTTPException(404, "校对任务不存在。")
    _require_batch_access(db, batch, current_user)
    workflow_ids = None
    if batch.batch_kind == "workflow_segments" or not can_access_all_projects(current_user):
        workflow_ids = [str(segment.id) for segment in eligible_workflow_segments(db, batch, current_user)]
        if not workflow_ids:
            raise HTTPException(403, "当前没有有权校对的未确认非空句段。")
    if batch.status in {"queued", "running", "canceling"}:
        raise HTTPException(status_code=409, detail="当前校对批次正在运行。")
    if batch.status not in {"ready", "partial_failed", "failed", "canceled"} and not (
        batch.status == "completed" and eligible_workflow_segments(db, batch, current_user)
    ):
        raise HTTPException(status_code=409, detail=f"当前状态（{batch.status}）不可启动校对。")
    if getattr(batch, "batch_kind", "xlsx_columns") == "document_pair" and getattr(batch, "workflow_stage", "not_applicable") != "proofreading":
        raise HTTPException(status_code=409, detail="请先完成对齐再开始校对。")
    ensure_document_pair_segments_complete(db, batch)
    provider = payload.provider
    model = (payload.model or "").strip() or None
    user_instructions = payload.user_instructions.strip()
    retry_scope = payload.retry_scope
    if workflow_ids is not None and retry_scope == "failed_only":
        failed_ids = get_latest_generation_error_segment_ids(db, batch.id)
        workflow_ids = [value for value in workflow_ids if UUID(value) in failed_ids]
        if not workflow_ids:
            raise HTTPException(409, "当前分配范围内没有可重试的失败项。")
    if retry_scope == "failed_only" and not get_latest_generation_error_segment_ids(db, batch.id):
        raise HTTPException(status_code=409, detail="当前批次没有可重试的生成失败项。")
    try:
        validate_provider_choice(provider=provider, model_override=model)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        batch_config = json.loads(batch.config_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        batch_config = {}
    if not isinstance(batch_config, dict):
        batch_config = {}
    batch_config["generation"] = {
        "provider": provider,
        "model": model or "",
        "user_instructions": user_instructions,
        "retry_scope": retry_scope,
        "started_by_id": str(current_user.id),
        "run_id": str(uuid4()),
        **({"segment_ids": workflow_ids} if workflow_ids is not None else {}),
    }
    batch.config_json = json.dumps(batch_config, ensure_ascii=False)
    batch.cancel_requested = False
    batch.status = "queued"
    batch.progress = 0
    batch.message = "校对任务已排队。"
    batch.error_message = ""
    batch.export_status = "idle"
    batch.export_progress = 0
    batch.export_error_message = ""
    batch.export_filename = ""
    batch.export_path = ""
    batch.finished_at = None
    db.commit()
    background_tasks.add_task(
        run_generate_batch,
        batch.id,
        current_user.id,
        provider,
        model,
        user_instructions,
        retry_scope,
        batch_config["generation"]["run_id"],
    )
    return {"batch_id": str(batch.id), "status": "queued"}


@router.post("/proofreading-batches/{batch_id}/cancel")
def cancel_proofreading_batch(
    batch_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    batch = db.query(ProofreadingBatch).filter_by(id=batch_id).with_for_update().populate_existing().first()
    if batch is None:
        raise HTTPException(404, "校对任务不存在。")
    _require_batch_access(db, batch, current_user)
    generation = batch_config(batch).get("generation", {})
    if not can_access_all_projects(current_user) and generation.get("started_by_id") != str(current_user.id):
        raise HTTPException(403, "只有启动者或管理人员可以取消校对。")
    if batch.status not in {"queued", "running", "canceling"}:
        raise HTTPException(status_code=409, detail="当前校对批次不在运行中，无法取消。")
    batch.cancel_requested = True
    batch.message = "正在取消校对。"
    if batch.status == "queued":
        batch.status = "canceled"
        batch.message = "校对已取消。"
        batch.finished_at = batch.finished_at or datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        batch.status = "canceling"
    db.commit()
    return serialize_batch(db, batch)


@router.post("/proofreading-batches/{batch_id}/exports")
def export_proofreading_batch(
    batch_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(require_business_manager),
):
    batch = _get_batch_or_404(db, batch_id)
    if batch.status in {"ready", "queued", "running", "canceling"}:
        raise HTTPException(status_code=409, detail="请先完成 LLM 校对，再生成合并 Excel。")
    if batch.export_status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="校对版 Excel 正在生成。")
    batch.export_status = "queued"
    batch.export_progress = 0
    batch.export_error_message = ""
    db.commit()
    background_tasks.add_task(run_export_batch, batch.id)
    return {"batch_id": str(batch.id), "export_status": "queued"}


@router.get("/proofreading-batches/{batch_id}/export-readiness")
def get_proofreading_export_readiness(
    batch_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    batch = _get_batch_or_404(db, batch_id)
    _require_batch_access(db, batch, current_user)
    readiness = build_export_readiness(db, batch)
    db.commit()
    return readiness


@router.post("/proofreading-batches/{batch_id}/export-tasks")
def create_proofreading_export_task(
    batch_id: UUID,
    payload: ProofreadingExportTaskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    batch = _get_batch_or_404(db, batch_id)
    _require_batch_access(db, batch, current_user)
    if export_batch_is_busy(batch):
        raise HTTPException(status_code=409, detail="请先完成或结束当前解析、对齐或校对任务再导出。")
    readiness = build_export_readiness(db, batch)
    if payload.format not in readiness["available_formats"]:
        if payload.format == "proofreading_docx_target_revisions":
            raise HTTPException(
                status_code=400,
                detail="该批次没有可用的目标 DOCX 原件；历史批次请重新导入双文档后再导出。",
            )
        raise HTTPException(status_code=400, detail="当前校对批次不支持所选导出格式。")
    requires_warning_ack = (
        readiness["has_warnings"]
        and payload.format != "proofreading_audit_xlsx"
    )
    if requires_warning_ack and not payload.acknowledge_warnings:
        raise HTTPException(status_code=409, detail={
            "message": "导出内容仍有未确认或待处理项目。",
            "readiness": readiness,
        })
    binding = db.query(ProofreadingColumnBinding).filter_by(batch_id=batch.id).order_by(
        ProofreadingColumnBinding.sheet_index,
    ).first()
    if binding is None:
        raise HTTPException(status_code=400, detail="校对批次没有可导出的文件绑定。")
    db.commit()
    task = queue_file_export(
        db,
        file_record_id=binding.file_record_id,
        export_type=payload.format,
        current_user=current_user,
    )
    return JSONResponse(status_code=202, content=task)


@router.get("/proofreading-batches/{batch_id}/exports/latest")
def download_proofreading_batch_export(
    batch_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_business_manager),
):
    batch = _get_batch_or_404(db, batch_id)
    try:
        content, filename = load_exported_batch(batch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


@router.get("/file-records/{file_record_id}/proofreading-baselines")
def get_file_proofreading_baselines(
    file_record_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    file_record = db.query(FileRecord).filter(FileRecord.id == file_record_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if not can_access_all_projects(current_user):
        assigned = db.query(FileAssignment.id).filter(
            FileAssignment.file_record_id == file_record_id,
            FileAssignment.assignee_id == current_user.id,
            FileAssignment.status == "active",
        ).first()
        if not assigned:
            raise HTTPException(status_code=404, detail="任务不存在或未分配给当前用户。")
    binding = db.query(ProofreadingColumnBinding).filter(
        ProofreadingColumnBinding.file_record_id == file_record_id,
    ).first()
    # 兼容由旧后端进程生成的双文档任务：进入工作台时幂等补齐所有纯增译项，
    # 确保“有译文、无原文”的内容不会因历史物化逻辑而永久丢失。
    if binding is not None:
        batch = db.get(ProofreadingBatch, binding.batch_id)
        if batch is not None and batch.batch_kind == "document_pair":
            ensure_document_pair_segments_complete(db, batch)
            db.commit()
    rows = (
        db.query(ProofreadingSegmentBaseline, Segment)
        .join(Segment, Segment.id == ProofreadingSegmentBaseline.segment_id)
        .filter(Segment.file_record_id == file_record_id)
        .order_by(Segment.display_index)
        .all()
    )
    batch = db.get(ProofreadingBatch, binding.batch_id) if binding else None
    workflow_mode = batch is not None and batch.batch_kind == "workflow_segments"
    if workflow_mode:
        step_id = batch_config(batch).get("workflow_step_id")
        rows = [(baseline, segment) for baseline, segment in rows if str(segment.workflow_step_id) == step_id]
        if not rows:
            binding = None
    is_proofreading = binding is not None and not workflow_mode
    proofreading_context = None
    review_items_by_segment: dict[UUID, TranslationReviewReportItem] = {}
    if binding is not None:
        batch = db.get(ProofreadingBatch, binding.batch_id)
        try:
            config = json.loads(batch.config_json or "{}") if batch else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            config = {}
        generation = config.get("generation") if isinstance(config, dict) else {}
        if not isinstance(generation, dict):
            generation = {}
        latest_report = (
            db.query(TranslationReviewReport)
            .filter(TranslationReviewReport.proofreading_batch_id == binding.batch_id)
            .order_by(TranslationReviewReport.created_at.desc())
            .first()
        )
        # 每个句段取跨轮次中最新的一条记录：仅重试失败项或被取消的运行只会产生部分
        # 报告，不能让此前成功修改的句段丢失修改理由。已解决的失败项不再展示。
        review_items = (
            db.query(TranslationReviewReportItem)
            .join(TranslationReviewReport, TranslationReviewReport.id == TranslationReviewReportItem.report_id)
            .filter(
                TranslationReviewReport.proofreading_batch_id == binding.batch_id,
                TranslationReviewReportItem.file_record_id == file_record_id,
                TranslationReviewReportItem.segment_id.is_not(None),
                TranslationReviewReportItem.status != "resolved",
            )
            .order_by(
                TranslationReviewReport.created_at.desc(),
                TranslationReviewReportItem.created_at.desc(),
            )
            .all()
        )
        baseline_by_segment = {baseline.segment_id: baseline for baseline, _ in rows}
        for review_item in review_items:
            baseline = baseline_by_segment.get(review_item.segment_id)
            if workflow_mode and baseline and review_item.created_at and baseline.created_at and review_item.created_at < baseline.created_at:
                continue
            if review_item.segment_id is not None:
                review_items_by_segment.setdefault(review_item.segment_id, review_item)
        latest_llm_segment = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                Segment.llm_provider.is_not(None),
                Segment.source == "llm_review",
            )
            .order_by(Segment.updated_at.desc())
            .first()
        )
        actual_provider = (
            latest_llm_segment.llm_provider
            if latest_llm_segment and latest_llm_segment.llm_provider
            else latest_report.provider if latest_report else ""
        )
        actual_model = (
            latest_llm_segment.llm_model
            if latest_llm_segment and latest_llm_segment.llm_model
            else latest_report.model if latest_report else ""
        )
        eligible = eligible_workflow_segments(db, batch, current_user)
        proofreading_context = {
            "can_generate": bool(eligible),
            "can_cancel": can_access_all_projects(current_user) or generation.get("started_by_id") == str(current_user.id),
            "eligible_segments": len(eligible),
            "workflow_step_id": str(config.get("workflow_step_id") or ""),
            "pair_format": "xlsx" if batch and is_xlsx_pair(batch) else "document",
            "batch_id": str(binding.batch_id),
            "batch_kind": getattr(batch, "batch_kind", "xlsx_columns") if batch else "xlsx_columns",
            "workflow_stage": getattr(batch, "workflow_stage", "not_applicable") if batch else "not_applicable",
            "alignment_status": getattr(batch, "alignment_status", "not_applicable") if batch else "not_applicable",
            "batch_status": batch.status if batch else "",
            "batch_progress": int(batch.progress or 0) if batch else 0,
            "sheet_name": binding.sheet_name,
            "target_language": binding.target_language,
            "provider": str(generation.get("provider") or "auto"),
            "model": str(generation.get("model") or ""),
            "user_instructions": str(generation.get("user_instructions") or ""),
            "actual_provider": str(actual_provider or ""),
            "actual_model": str(actual_model or ""),
            "failed_segments": int(batch.failed_segments or 0) if batch else 0,
            "target_revision_export_available": bool(
                batch and target_revision_export_available(batch)
            ),
        }
    return {
        "is_proofreading": is_proofreading,
        "proofreading_context": proofreading_context,
        "items": [
            {
                "segment_id": str(segment.id),
                "sentence_id": segment.sentence_id,
                "original_target_text": baseline.original_target_text,
                "llm_reviewed": segment.source == "llm_review" and bool(segment.llm_provider),
                "review_suggestion": (
                    review_items_by_segment[segment.id].reason
                    if segment.id in review_items_by_segment
                    else ""
                ),
                "review_category": (
                    review_items_by_segment[segment.id].category_key
                    if segment.id in review_items_by_segment
                    else ""
                ),
                "review_confidence": (
                    review_items_by_segment[segment.id].confidence
                    if segment.id in review_items_by_segment
                    else ""
                ),
                "source_cell_ref": baseline.source_cell_ref,
                "target_cell_ref": baseline.target_cell_ref,
                "sheet_index": baseline.sheet_index,
                "row_index": baseline.row_index,
            }
            for baseline, segment in rows
        ]
    }
