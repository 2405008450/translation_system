from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.document_workspace import build_docx_workspace
from app.services.document_service import (
    create_document_with_segments,
    get_document_with_segments,
    list_documents,
    update_segment_by_sentence_id,
    batch_update_segments,
    delete_document,
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

@router.post("/documents")
async def create_document(
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
        document = create_document_with_segments(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "untitled.docx",
            similarity_threshold=threshold,
        )
        return {
            "id": document.id,
            "filename": document.filename,
            "status": document.status,
            "created_at": document.created_at.isoformat(),
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/documents")
def get_documents(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """获取文档列表"""
    documents = list_documents(db, skip=skip, limit=limit)
    return [
        {
            "id": doc.id,
            "filename": doc.filename,
            "status": doc.status,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat(),
        }
        for doc in documents
    ]


@router.get("/documents/{document_id}")
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
):
    """获取文档详情及所有片段"""
    result = get_document_with_segments(db, document_id)
    if not result:
        raise HTTPException(status_code=404, detail="文档不存在。")

    doc = result["document"]
    segments = result["segments"]

    return {
        "id": doc.id,
        "filename": doc.filename,
        "status": doc.status,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
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


@router.put("/documents/{document_id}/segments/{sentence_id}")
def update_segment(
    document_id: int,
    sentence_id: str,
    update: SegmentUpdate,
    db: Session = Depends(get_db),
):
    """更新单个片段的译文"""
    segment = update_segment_by_sentence_id(
        db=db,
        document_id=document_id,
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


@router.put("/documents/{document_id}/segments")
def batch_update(
    document_id: int,
    batch: BatchSegmentUpdate,
    db: Session = Depends(get_db),
):
    """批量更新片段译文"""
    updated_count = batch_update_segments(
        db=db,
        document_id=document_id,
        updates=[u.model_dump() for u in batch.updates],
    )
    return {"updated_count": updated_count}


@router.delete("/documents/{document_id}")
def remove_document(
    document_id: int,
    db: Session = Depends(get_db),
):
    """删除文档及其所有片段"""
    success = delete_document(db, document_id)
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
