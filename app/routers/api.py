from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.document_workspace import build_docx_workspace
from app.services.file_record_service import (
    create_file_record_with_segments,
    get_file_record_with_segments,
    list_file_records,
    update_segment_by_sentence_id,
    batch_update_segments,
    delete_file_record,
)
from app.services.slate_parser import parse_docx_for_slate


router = APIRouter()


class SegmentUpdate(BaseModel):
    sentence_id: str
    target_text: str
    source: str = "manual"


class BatchSegmentUpdate(BaseModel):
    updates: list[SegmentUpdate]


def _validate_docx_upload(file: UploadFile) -> None:
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="仅支持 DOCX 文件。")


@router.post("/parser/slate")
async def upload_for_slate(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    db: Session = Depends(get_db),
):
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    try:
        result = parse_docx_for_slate(db=db, raw_bytes=raw_bytes, similarity_threshold=threshold)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/parser/workspace")
async def upload_for_workspace(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    db: Session = Depends(get_db),
):
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    try:
        return build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=threshold,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ========== 文档管理 API ==========

@router.post("/file-records")
@router.post("/documents", include_in_schema=False)
async def create_file_record(
    file: UploadFile = File(...),
    threshold: float = Form(default=0.6),
    db: Session = Depends(get_db),
):
    """上传文档并创建持久化记录"""
    _validate_docx_upload(file)

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="文件为空。")

    try:
        file_record = create_file_record_with_segments(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.docx",
            similarity_threshold=threshold,
        )
        return {
            "id": file_record.id,
            "filename": file_record.filename,
            "status": file_record.status,
            "created_at": file_record.created_at.isoformat(),
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/file-records")
@router.get("/documents", include_in_schema=False)
def get_file_records(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """获取文档列表"""
    file_records = list_file_records(db, skip=skip, limit=limit)
    return [
        {
            "id": file_record.id,
            "filename": file_record.filename,
            "status": file_record.status,
            "created_at": file_record.created_at.isoformat(),
            "updated_at": file_record.updated_at.isoformat(),
        }
        for file_record in file_records
    ]


<<<<<<< HEAD
@router.get("/file-records/{file_record_id}")
@router.get("/documents/{file_record_id}", include_in_schema=False)
def get_file_record(
    file_record_id: UUID,
    skip: int = 0,
    limit: int = 200,
=======
@router.get("/documents/{document_id}")
def get_document(
    document_id: UUID,
>>>>>>> 506e4e1 (移除 __pycache__ 追踪)
    db: Session = Depends(get_db),
):
    """获取文档详情及片段，支持分页"""
    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 1000)
    result = get_file_record_with_segments(
        db,
        file_record_id,
        skip=safe_skip,
        limit=safe_limit,
    )
    if not result:
        raise HTTPException(status_code=404, detail="文档不存在。")

    file_record = result["file_record"]
    segments = result["segments"]

    return {
        "id": file_record.id,
        "filename": file_record.filename,
        "status": file_record.status,
        "created_at": file_record.created_at.isoformat(),
        "updated_at": file_record.updated_at.isoformat(),
        "total_segments": result["total_segments"],
        "skip": result["skip"],
        "limit": result["limit"],
        "segments": [
            {
                "id": seg.id,
                "sentence_id": seg.sentence_id,
                "source_text": seg.source_text,
                "display_text": seg.display_text,
                "target_text": seg.target_text,
                "status": seg.status,
                "score": seg.score,
                "matched_source_text": seg.matched_source_text,
                "source": seg.source,
                "block_type": seg.block_type,
                "block_index": seg.block_index,
            }
            for seg in segments
        ],
    }


@router.put("/file-records/{file_record_id}/segments/{sentence_id}")
@router.put("/documents/{file_record_id}/segments/{sentence_id}", include_in_schema=False)
def update_segment(
<<<<<<< HEAD
    file_record_id: UUID,
=======
    document_id: UUID,
>>>>>>> 506e4e1 (移除 __pycache__ 追踪)
    sentence_id: str,
    update: SegmentUpdate,
    db: Session = Depends(get_db),
):
    """更新单个片段的译文"""
    segment = update_segment_by_sentence_id(
        db=db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        target_text=update.target_text,
        source=update.source,
    )
    if not segment:
        raise HTTPException(status_code=404, detail="片段不存在。")

    return {
        "id": segment.id,
        "sentence_id": segment.sentence_id,
        "target_text": segment.target_text,
        "status": segment.status,
        "source": segment.source,
    }


@router.put("/file-records/{file_record_id}/segments")
@router.put("/documents/{file_record_id}/segments", include_in_schema=False)
def batch_update(
<<<<<<< HEAD
    file_record_id: UUID,
=======
    document_id: UUID,
>>>>>>> 506e4e1 (移除 __pycache__ 追踪)
    batch: BatchSegmentUpdate,
    db: Session = Depends(get_db),
):
    """批量更新片段译文"""
    updated_count = batch_update_segments(
        db=db,
        file_record_id=file_record_id,
        updates=[u.model_dump() for u in batch.updates],
    )
    return {"updated_count": updated_count}


<<<<<<< HEAD
@router.delete("/file-records/{file_record_id}")
@router.delete("/documents/{file_record_id}", include_in_schema=False)
def remove_file_record(
    file_record_id: UUID,
=======
@router.delete("/documents/{document_id}")
def remove_document(
    document_id: UUID,
>>>>>>> 506e4e1 (移除 __pycache__ 追踪)
    db: Session = Depends(get_db),
):
    """删除文档及其所有片段"""
    success = delete_file_record(db, file_record_id)
    if not success:
        raise HTTPException(status_code=404, detail="文档不存在。")
    return {"message": "文档已删除。"}


# ========== TM 管理 API ==========

class TMEntry(BaseModel):
    source_text: str
    target_text: str


class BatchTMEntry(BaseModel):
    entries: list[TMEntry]


@router.post("/tm/add")
def add_tm_entry(
    entry: TMEntry,
    db: Session = Depends(get_db),
):
    """添加单条 TM 记录（去重：相同原文不重复添加）"""
    from app.models import TranslationMemory
    from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text

    source_text = normalize_text(entry.source_text)
    target_text = normalize_text(entry.target_text)

    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="原文和译文不能为空。")

    source_hash = build_source_hash(source_text)

    # 检查是否已存在
    existing = db.query(TranslationMemory).filter(
        TranslationMemory.source_hash == source_hash
    ).first()

    if existing:
        # 已存在，更新译文
        existing.target_text = target_text
        db.commit()
        return {"status": "updated", "id": existing.id, "message": "已更新现有记录。"}

    # 不存在，新增
    tm = TranslationMemory(
        source_text=source_text,
        target_text=target_text,
        source_hash=source_hash,
        source_normalized=normalize_match_text(source_text) or source_text,
    )
    db.add(tm)
    db.commit()
    db.refresh(tm)

    return {"status": "created", "id": tm.id, "message": "已添加新记录。"}


@router.post("/tm/batch-add")
def batch_add_tm_entries(
    batch: BatchTMEntry,
    db: Session = Depends(get_db),
):
    """批量添加 TM 记录（去重）"""
    from app.models import TranslationMemory
    from app.services.normalizer import build_source_hash, normalize_match_text, normalize_text

    created_count = 0
    updated_count = 0
    skipped_count = 0

    for entry in batch.entries:
        source_text = normalize_text(entry.source_text)
        target_text = normalize_text(entry.target_text)

        if not source_text or not target_text:
            skipped_count += 1
            continue

        source_hash = build_source_hash(source_text)

        existing = db.query(TranslationMemory).filter(
            TranslationMemory.source_hash == source_hash
        ).first()

        if existing:
            existing.target_text = target_text
            updated_count += 1
        else:
            tm = TranslationMemory(
                source_text=source_text,
                target_text=target_text,
                source_hash=source_hash,
                source_normalized=normalize_match_text(source_text) or source_text,
            )
            db.add(tm)
            created_count += 1

    db.commit()

    return {
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
    }
