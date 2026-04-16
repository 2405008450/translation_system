import json
from io import BytesIO
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.document_exporter import (
    DOCX_MEDIA_TYPE,
    build_translated_docx_filename,
    export_translated_docx,
)
from app.services.document_workspace import build_docx_workspace
from app.services.file_record_service import (
    batch_update_segments,
    create_file_record_with_segments,
    delete_file_record,
    get_file_record as get_file_record_model,
    get_file_record_with_segments,
    get_tm_target_text_map,
    list_file_records,
    list_segments_for_file_record,
    load_file_record_source,
    update_segment_by_sentence_id,
    update_segment_with_llm_result,
)
from app.services.llm_service import (
    LLMConfigurationError,
    LLMTranslationFailure,
    LLMTranslationTask,
    iter_batch_translate,
    validate_provider_choice,
)
from app.services.slate_parser import parse_docx_for_slate


router = APIRouter()


class SegmentUpdate(BaseModel):
    sentence_id: str
    target_text: str
    source: str = "manual"


class BatchSegmentUpdate(BaseModel):
    updates: list[SegmentUpdate]


class LLMTranslateRequest(BaseModel):
    scope: Literal["fuzzy_only", "none_only", "all"] = "all"
    provider: Literal["auto", "deepseek", "openrouter"] = "auto"


def _build_docx_download_response(filename: str, docx_bytes: bytes) -> StreamingResponse:
    export_filename = build_translated_docx_filename(filename)
    ascii_filename = export_filename.encode("ascii", "ignore").decode("ascii").strip() or "translated.docx"
    ascii_filename = ascii_filename.replace('"', "")
    quoted_filename = quote(export_filename)

    return StreamingResponse(
        BytesIO(docx_bytes),
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{quoted_filename}"
            )
        },
    )


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_llm_translation_tasks(
    db: Session,
    file_record_id: UUID,
    scope: Literal["fuzzy_only", "none_only", "all"],
) -> list[LLMTranslationTask]:
    statuses_by_scope = {
        "fuzzy_only": {"fuzzy"},
        "none_only": {"none"},
        "all": {"fuzzy", "none"},
    }
    target_statuses = statuses_by_scope[scope]
    segments = list_segments_for_file_record(db, file_record_id)
    tm_target_text_map = get_tm_target_text_map(
        db,
        [segment.matched_source_text for segment in segments if segment.matched_source_text],
    )

    tasks: list[LLMTranslationTask] = []
    for index, segment in enumerate(segments):
        if segment.status not in target_statuses:
            continue

        previous_segment = segments[index - 1] if index > 0 else None
        next_segment = segments[index + 1] if index + 1 < len(segments) else None
        tm_target_text = tm_target_text_map.get(
            segment.matched_source_text or "",
            segment.target_text if segment.source == "tm" else "",
        )

        tasks.append(
            LLMTranslationTask(
                sentence_id=segment.sentence_id,
                status=segment.status,
                source_text=segment.source_text,
                matched_source_text=segment.matched_source_text,
                tm_target_text=tm_target_text,
                previous_source_text=previous_segment.source_text if previous_segment else None,
                previous_target_text=(previous_segment.target_text or None) if previous_segment else None,
                next_source_text=next_segment.source_text if next_segment else None,
                next_target_text=(next_segment.target_text or None) if next_segment else None,
            )
        )

    return tasks


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


@router.get("/file-records/{file_record_id}")
@router.get("/documents/{file_record_id}", include_in_schema=False)
def get_file_record(
    file_record_id: UUID,
    skip: int = 0,
    limit: int = 200,
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


@router.get("/file-records/{file_record_id}/export-docx")
@router.get("/documents/{file_record_id}/export-docx", include_in_schema=False)
def export_file_record_docx(
    file_record_id: UUID,
    db: Session = Depends(get_db),
):
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    raw_bytes = load_file_record_source(file_record)
    if raw_bytes is None:
        raise HTTPException(status_code=400, detail="The source DOCX is unavailable for export.")

    segments = list_segments_for_file_record(db, file_record_id)
    try:
        translated_docx = export_translated_docx(raw_bytes=raw_bytes, segments=segments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _build_docx_download_response(file_record.filename, translated_docx)


@router.put("/file-records/{file_record_id}/segments/{sentence_id}")
@router.put("/documents/{file_record_id}/segments/{sentence_id}", include_in_schema=False)
def update_segment(
    file_record_id: UUID,
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
    file_record_id: UUID,
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


@router.post("/file-records/{file_record_id}/llm-translate")
@router.post("/documents/{file_record_id}/llm-translate", include_in_schema=False)
async def llm_translate_file_record(
    file_record_id: UUID,
    request: Request,
    payload: LLMTranslateRequest | None = None,
    db: Session = Depends(get_db),
):
    """对 fuzzy / none 片段触发 LLM 译文修正，并通过 SSE 逐条返回结果。"""
    file_record = get_file_record_model(db, file_record_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="文档不存在。")

    body = payload or LLMTranslateRequest()
    try:
        validate_provider_choice(body.provider)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    translation_tasks = _build_llm_translation_tasks(
        db=db,
        file_record_id=file_record_id,
        scope=body.scope,
    )

    async def event_stream():
        updated_count = 0
        error_count = 0
        total_count = len(translation_tasks)

        yield _sse_event(
            "start",
            {
                "file_record_id": str(file_record_id),
                "scope": body.scope,
                "provider": body.provider,
                "total": total_count,
            },
        )

        if total_count == 0:
            yield _sse_event(
                "complete",
                {
                    "file_record_id": str(file_record_id),
                    "updated_count": 0,
                    "error_count": 0,
                    "total": 0,
                },
            )
            return

        async for result in iter_batch_translate(
            translation_tasks,
            provider=body.provider,
        ):
            if await request.is_disconnected():
                break

            if isinstance(result, LLMTranslationFailure):
                error_count += 1
                yield _sse_event(
                    "error",
                    {
                        "sentence_id": result.sentence_id,
                        "status": result.status,
                        "message": result.error_message,
                    },
                )
                continue

            try:
                segment = update_segment_with_llm_result(
                    db=db,
                    file_record_id=file_record_id,
                    sentence_id=result.sentence_id,
                    target_text=result.translated_text,
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                error_count += 1
                yield _sse_event(
                    "error",
                    {
                        "sentence_id": result.sentence_id,
                        "status": result.status,
                        "message": f"数据库更新失败：{exc}",
                    },
                )
                continue

            if not segment:
                error_count += 1
                yield _sse_event(
                    "error",
                    {
                        "sentence_id": result.sentence_id,
                        "status": result.status,
                        "message": "片段不存在，无法写回 LLM 译文。",
                    },
                )
                continue

            updated_count += 1
            yield _sse_event(
                "segment",
                {
                    "sentence_id": segment.sentence_id,
                    "target_text": segment.target_text,
                    "status": segment.status,
                    "source": segment.source,
                    "provider": result.provider,
                    "model": result.model,
                },
            )

        if not await request.is_disconnected():
            yield _sse_event(
                "complete",
                {
                    "file_record_id": str(file_record_id),
                    "updated_count": updated_count,
                    "error_count": error_count,
                    "total": total_count,
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/file-records/{file_record_id}")
@router.delete("/documents/{file_record_id}", include_in_schema=False)
def remove_file_record(
    file_record_id: UUID,
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
