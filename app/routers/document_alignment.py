from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_business_manager
from app.database import get_db
from app.models import DocumentAlignmentPair, ProofreadingBatch, Project, User
from app.services.document_alignment.segments import materialize_alignment
from app.services.document_alignment.export import export_alignment_csv as build_alignment_csv
from app.services.document_alignment.service import (
    create_alignment_batch, preview_document_pair, refresh_pair_text,
    merge_alignment_pair_range, run_alignment_batch, serialize_pair, validate_pair_integrity,
)
from app.services.import_task_storage import (
    cleanup_import_task_staging, get_import_task_staging_dir, stage_import_file_streams,
)
from app.services.proofreading import serialize_batch

router = APIRouter()


class AlignmentBatchCreate(BaseModel):
    preview_token: str
    source_language: str
    target_language: str
    granularity: Literal["sentence", "paragraph"] = "sentence"
    use_llm_for_hard_blocks: bool = False
    full_review: bool = True
    alignment_strategy: Literal["order_first", "structure_aware"] = "order_first"


class PairPatch(BaseModel):
    src_indices: list[int] | None = None
    tgt_indices: list[int] | None = None
    locked: bool | None = None


class PairSplit(BaseModel):
    pair_id: UUID
    src_at: int = Field(ge=0)
    tgt_at: int = Field(ge=0)


class PairMerge(BaseModel):
    first_pair_id: UUID | None = None
    second_pair_id: UUID | None = None
    pair_ids: list[UUID] = Field(default_factory=list, max_length=100)


class BoundaryShift(BaseModel):
    pair_id: UUID
    side: Literal["source", "target"]
    direction: Literal["next_into_current", "current_into_next"]


def _project(db: Session, project_id: UUID) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在。")
    if project.workflow_template_id != "proofread":
        raise HTTPException(400, "当前项目不是“校对”工作流。")
    return project


def _batch(db: Session, batch_id: UUID, project_id: UUID | None = None) -> ProofreadingBatch:
    batch = db.get(ProofreadingBatch, batch_id)
    if not batch or batch.batch_kind != "document_pair" or project_id is not None and batch.project_id != project_id:
        raise HTTPException(404, "双文档对齐批次不存在。")
    return batch


def _load_pair_preview(token: str) -> tuple[str, bytes, str, bytes]:
    value = (token or "").strip().lower()
    if not value or any(char not in "0123456789abcdef-" for char in value):
        raise HTTPException(400, "预览令牌无效。")
    task_dir = get_import_task_staging_dir(value)
    files = sorted(path for path in task_dir.iterdir() if path.is_file()) if task_dir.exists() else []
    if len(files) != 2:
        raise HTTPException(410, "预览文件已过期，请重新上传。")
    names = [path.name.split("_", 1)[1] if "_" in path.name else path.name for path in files]
    return names[0], files[0].read_bytes(), names[1], files[1].read_bytes()


@router.post("/projects/{project_id}/document-alignment/preview")
async def preview_alignment(
    project_id: UUID, source_file: UploadFile = File(...), target_file: UploadFile = File(...),
    db: Session = Depends(get_db), _: User = Depends(require_business_manager),
):
    _project(db, project_id)
    source_name, target_name = source_file.filename or "source.txt", target_file.filename or "target.txt"
    allowed = {".docx", ".doc", ".txt"}
    if Path(source_name).suffix.lower() not in allowed or Path(target_name).suffix.lower() not in allowed:
        raise HTTPException(400, "仅支持 docx、doc 和 txt 文档。")
    token = str(uuid4())
    try:
        staged = await asyncio.to_thread(
            stage_import_file_streams, token,
            [(source_name, source_file.file), (target_name, target_file.file)], max_files=2,
        )
        source_bytes = Path(str(staged[0]["path"])).read_bytes()
        target_bytes = Path(str(staged[1]["path"])).read_bytes()
        summary = await asyncio.to_thread(preview_document_pair, source_bytes, source_name, target_bytes, target_name)
        return {**summary, "preview_token": token}
    except Exception as exc:
        cleanup_import_task_staging(token)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(400, str(exc)) from exc


@router.post("/projects/{project_id}/document-alignment-batches")
def create_batch(
    project_id: UUID, payload: AlignmentBatchCreate, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db), current_user: User = Depends(require_business_manager),
):
    project = _project(db, project_id)
    source_name, source_bytes, target_name, target_bytes = _load_pair_preview(payload.preview_token)
    try:
        batch = create_alignment_batch(
            db, project=project, current_user=current_user,
            source_bytes=source_bytes, source_filename=source_name,
            target_bytes=target_bytes, target_filename=target_name,
            source_language=payload.source_language, target_language=payload.target_language,
            granularity=payload.granularity, use_llm_for_hard_blocks=payload.use_llm_for_hard_blocks,
            full_review=payload.full_review, alignment_strategy=payload.alignment_strategy,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        cleanup_import_task_staging(payload.preview_token)
    background_tasks.add_task(run_alignment_batch, batch.id)
    return serialize_batch(db, batch)


@router.get("/proofreading-batches/{batch_id}/alignment-pairs")
def list_pairs(
    batch_id: UUID, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100),
    confidence_level: str | None = None, only_unlocked: bool = False,
    db: Session = Depends(get_db), _: User = Depends(require_business_manager),
):
    batch = _batch(db, batch_id)
    query = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id)
    if confidence_level:
        query = query.filter(DocumentAlignmentPair.confidence_level == confidence_level)
    if only_unlocked:
        query = query.filter(DocumentAlignmentPair.locked.is_(False))
    total = query.count()
    rows = query.order_by(DocumentAlignmentPair.pair_order).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [serialize_pair(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.patch("/alignment-pairs/{pair_id}")
def patch_pair(pair_id: UUID, payload: PairPatch, db: Session = Depends(get_db), _: User = Depends(require_business_manager)):
    pair = db.get(DocumentAlignmentPair, pair_id)
    if not pair:
        raise HTTPException(404, "配对不存在。")
    _batch(db, pair.batch_id)
    if payload.src_indices is not None:
        pair.src_indices = json.dumps(payload.src_indices)
    if payload.tgt_indices is not None:
        pair.tgt_indices = json.dumps(payload.tgt_indices)
    if payload.locked is not None:
        pair.locked = payload.locked
    refresh_pair_text(db, pair)
    try:
        validate_pair_integrity(db, pair.batch_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return serialize_pair(pair)


@router.post("/proofreading-batches/{batch_id}/alignment-pairs/split")
def split_pair(batch_id: UUID, payload: PairSplit, db: Session = Depends(get_db), _: User = Depends(require_business_manager)):
    _batch(db, batch_id)
    pair = db.get(DocumentAlignmentPair, payload.pair_id)
    if not pair or pair.batch_id != batch_id:
        raise HTTPException(404, "配对不存在。")
    src, tgt = json.loads(pair.src_indices), json.loads(pair.tgt_indices)
    if (
        payload.src_at > len(src) or payload.tgt_at > len(tgt)
        or (payload.src_at == 0 and payload.tgt_at == 0)
        or (payload.src_at == len(src) and payload.tgt_at == len(tgt))
    ):
        raise HTTPException(400, "拆分位置无效。")
    # 先转为负序号再平移，避免唯一约束在逐行更新时发生瞬时冲突。
    db.query(DocumentAlignmentPair).filter(
        DocumentAlignmentPair.batch_id == batch_id, DocumentAlignmentPair.pair_order > pair.pair_order,
    ).update({DocumentAlignmentPair.pair_order: -DocumentAlignmentPair.pair_order - 1}, synchronize_session=False)
    db.flush()
    db.query(DocumentAlignmentPair).filter(
        DocumentAlignmentPair.batch_id == batch_id, DocumentAlignmentPair.pair_order < 0,
    ).update({DocumentAlignmentPair.pair_order: -DocumentAlignmentPair.pair_order}, synchronize_session=False)
    second = DocumentAlignmentPair(
        batch_id=batch_id, pair_order=pair.pair_order + 1,
        src_indices=json.dumps(src[payload.src_at:]), tgt_indices=json.dumps(tgt[payload.tgt_at:]),
        source_text="", target_text="", confidence=0.8, confidence_level="high", method="manual", features="{}",
        block_type=pair.block_type, block_index=pair.block_index, row_index=pair.row_index, cell_index=pair.cell_index,
    )
    pair.src_indices, pair.tgt_indices = json.dumps(src[:payload.src_at]), json.dumps(tgt[:payload.tgt_at])
    db.add(second)
    db.flush()
    refresh_pair_text(db, pair); refresh_pair_text(db, second)
    validate_pair_integrity(db, batch_id)
    db.commit()
    return {"items": [serialize_pair(pair), serialize_pair(second)]}


@router.post("/proofreading-batches/{batch_id}/alignment-pairs/merge")
def merge_pairs(batch_id: UUID, payload: PairMerge, db: Session = Depends(get_db), _: User = Depends(require_business_manager)):
    _batch(db, batch_id)
    pair_ids = payload.pair_ids
    if not pair_ids and payload.first_pair_id and payload.second_pair_id:
        pair_ids = [payload.first_pair_id, payload.second_pair_id]
    try:
        merged = merge_alignment_pair_range(db, batch_id, pair_ids)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.refresh(merged)
    return serialize_pair(merged)


@router.post("/proofreading-batches/{batch_id}/alignment-pairs/shift-boundary")
def shift_boundary(batch_id: UUID, payload: BoundaryShift, db: Session = Depends(get_db), _: User = Depends(require_business_manager)):
    _batch(db, batch_id)
    current = db.get(DocumentAlignmentPair, payload.pair_id)
    if not current or current.batch_id != batch_id:
        raise HTTPException(404, "配对不存在。")
    nxt = db.query(DocumentAlignmentPair).filter_by(batch_id=batch_id, pair_order=current.pair_order + 1).first()
    if not nxt:
        raise HTTPException(400, "当前配对没有下一项。")
    field = "src_indices" if payload.side == "source" else "tgt_indices"
    left, right = json.loads(getattr(current, field)), json.loads(getattr(nxt, field))
    if payload.direction == "next_into_current":
        if not right: raise HTTPException(400, "下一配对没有可移动单元。")
        left.append(right.pop(0))
    else:
        if not left: raise HTTPException(400, "当前配对没有可移动单元。")
        right.insert(0, left.pop())
    setattr(current, field, json.dumps(left)); setattr(nxt, field, json.dumps(right))
    refresh_pair_text(db, current); refresh_pair_text(db, nxt)
    validate_pair_integrity(db, batch_id)
    db.commit()
    return {"items": [serialize_pair(current), serialize_pair(nxt)]}


@router.post("/proofreading-batches/{batch_id}/alignment/rerun")
def rerun(batch_id: UUID, background_tasks: BackgroundTasks, db: Session = Depends(get_db), _: User = Depends(require_business_manager)):
    batch = _batch(db, batch_id)
    if batch.alignment_status in {"aligning", "canceling"}:
        raise HTTPException(409, "当前双文档对齐任务仍在运行。")
    batch.alignment_status = "aligning"; batch.status = "aligning"; batch.progress = 0
    batch.message = "正在准备向量对齐窗口…"
    batch.error_message = ""
    batch.cancel_requested = False
    batch.finished_at = None
    db.commit()
    background_tasks.add_task(run_alignment_batch, batch.id)
    return {"batch_id": str(batch.id), "alignment_status": "aligning"}


@router.post("/proofreading-batches/{batch_id}/alignment/cancel")
def cancel_alignment(
    batch_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_business_manager),
):
    batch = _batch(db, batch_id)
    if batch.alignment_status not in {"aligning", "canceling"}:
        raise HTTPException(409, "当前双文档对齐任务不在运行中。")
    batch.cancel_requested = True
    batch.alignment_status = "canceling"
    batch.status = "canceling"
    batch.message = "正在终止双文档对齐；当前远端请求结束后停止。"
    db.commit()
    return serialize_batch(db, batch)


@router.get("/proofreading-batches/{batch_id}/alignment/export.csv")
def export_alignment_csv(
    batch_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_business_manager),
):
    batch = _batch(db, batch_id)
    if batch.alignment_status not in {"draft", "confirmed"}:
        raise HTTPException(409, "请等待对齐草稿生成完成后再导出 CSV。")
    pairs = (
        db.query(DocumentAlignmentPair)
        .filter(DocumentAlignmentPair.batch_id == batch.id)
        .order_by(DocumentAlignmentPair.pair_order)
        .all()
    )
    filename = f"{Path(batch.filename).stem}_原文译文对照.csv"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return StreamingResponse(iter([build_alignment_csv(pairs)]), media_type="text/csv; charset=utf-8", headers=headers)


@router.post("/proofreading-batches/{batch_id}/alignment/confirm")
def confirm(batch_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(require_business_manager)):
    batch = _batch(db, batch_id)
    try:
        file_record = materialize_alignment(db, batch, current_user=current_user)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"batch": serialize_batch(db, batch), "file_record_id": str(file_record.id)}
