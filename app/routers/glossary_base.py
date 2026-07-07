from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.auth import get_current_user, get_user_display_name, require_admin, require_resource_creator
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import FileRecord, GlossaryBase, GlossaryEntry, User
from app.services.glossary_importer import (
    XLSX_EXTENSIONS,
    import_glossary_from_xlsx_path,
    preview_glossary_from_xlsx_path,
)
from app.services.import_task_storage import (
    cleanup_import_task_staging,
    stage_import_file_streams,
)
from app.services.import_task_state import (
    ImportTaskCanceled,
    import_task_cancel_requested,
    raise_if_import_task_canceled,
    set_import_task_status,
)
from app.services.language_pairs import require_language_pair
from app.services.normalizer import normalize_match_text, normalize_text
from app.services.notification_service import (
    build_resource_import_notification,
    create_operation_notification,
)
from app.services.resource_export_queue import (
    ResourceExportFormat,
    build_resource_export_download_response,
    cancel_resource_export_task,
    ensure_export_task_status,
    queue_resource_export,
)
from app.services.xlsx_exporter import build_tabular_xlsx, build_xlsx_download_response
from app.services.task_file_service import UploadLimitError


router = APIRouter(dependencies=[Depends(get_current_user)])
logger = logging.getLogger(__name__)


def _resource_import_preview_max_scan_rows() -> int:
    value = int(get_settings().resource_import_preview_max_scan_rows or 1000)
    return max(1, min(value, 10000))


def _resource_import_batch_size() -> int:
    value = int(get_settings().resource_import_batch_size or 1000)
    return max(1, min(value, 5000))


def _resource_import_max_file_bytes(_: str) -> int:
    return max(1, int(get_settings().resource_import_max_size_mb or 1024)) * 1024 * 1024


def _stage_resource_upload_file(file: UploadFile) -> tuple[str, dict[str, Any]]:
    task_id = str(uuid4())
    try:
        staged = stage_import_file_streams(
            task_id,
            [(file.filename or "uploaded.xlsx", file.file)],
            max_files=1,
            max_size_resolver=_resource_import_max_file_bytes,
            max_total_bytes=_resource_import_max_file_bytes(file.filename or "uploaded.xlsx"),
        )[0]
        return task_id, staged
    except UploadLimitError as exc:
        cleanup_import_task_staging(task_id)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception:
        cleanup_import_task_staging(task_id)
        raise


class GlossaryBasePayload(BaseModel):
    name: str
    description: str | None = None
    source_language: str
    target_language: str


class GlossaryEntryPayload(BaseModel):
    source_text: str
    target_text: str
    note: str | None = None


def _normalize_glossary_base_name(name: str) -> str:
    return " ".join(name.strip().split())


def _require_glossary_language_pair(
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    try:
        return require_language_pair(source_language, target_language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _get_glossary_base_or_404(db: Session, glossary_base_id: UUID) -> GlossaryBase:
    glossary_base = db.query(GlossaryBase).filter(GlossaryBase.id == glossary_base_id).first()
    if glossary_base is None:
        raise HTTPException(status_code=404, detail="词汇表不存在。")
    return glossary_base


def _resolve_glossary_base_language_pair(
    glossary_base: GlossaryBase,
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    if glossary_base.source_language and glossary_base.target_language:
        normalized_source_language, normalized_target_language = _require_glossary_language_pair(
            source_language,
            target_language,
        )
        if (
            normalized_source_language != glossary_base.source_language
            or normalized_target_language != glossary_base.target_language
        ):
            raise HTTPException(status_code=400, detail="所选词汇表的语言对与本次导入不一致。")
        return normalized_source_language, normalized_target_language

    normalized_source_language, normalized_target_language = _require_glossary_language_pair(
        source_language,
        target_language,
    )
    glossary_base.source_language = normalized_source_language
    glossary_base.target_language = normalized_target_language
    return normalized_source_language, normalized_target_language


def _serialize_glossary_base(glossary_base: GlossaryBase, entry_count: int = 0) -> dict:
    return {
        "id": str(glossary_base.id),
        "name": glossary_base.name,
        "description": glossary_base.description,
        "source_language": glossary_base.source_language,
        "target_language": glossary_base.target_language,
        "creator_id": str(glossary_base.creator_id) if glossary_base.creator_id else None,
        "creator_name": get_user_display_name(glossary_base.creator),
        "created_at": glossary_base.created_at.isoformat(),
        "updated_at": glossary_base.updated_at.isoformat(),
        "entry_count": entry_count,
    }


def _serialize_glossary_entry(entry: GlossaryEntry) -> dict:
    creator_name = None
    if entry.creator:
        creator_name = entry.creator.nickname or entry.creator.username
    last_modified_by_name = None
    if entry.last_modified_by:
        last_modified_by_name = entry.last_modified_by.nickname or entry.last_modified_by.username
    return {
        "id": str(entry.id),
        "glossary_base_id": str(entry.glossary_base_id),
        "source_text": entry.source_text,
        "target_text": entry.target_text,
        "note": entry.note or "",
        "source_language": entry.source_language,
        "target_language": entry.target_language,
        "creator_id": str(entry.creator_id) if entry.creator_id else None,
        "creator_name": creator_name,
        "last_modified_by_id": str(entry.last_modified_by_id) if entry.last_modified_by_id else None,
        "last_modified_by_name": last_modified_by_name,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


def _apply_glossary_entry_sort(query, sort_by: str | None, sort_order: str | None):
    order = "asc" if sort_order == "asc" else "desc"
    sort_columns = {
        "source_text": GlossaryEntry.source_text,
        "target_text": GlossaryEntry.target_text,
        "note": GlossaryEntry.note,
        "created_at": GlossaryEntry.created_at,
        "updated_at": GlossaryEntry.updated_at,
    }
    if sort_by in sort_columns:
        column = sort_columns[sort_by]
        return query.order_by(column.asc() if order == "asc" else column.desc(), GlossaryEntry.id.asc())
    if sort_by == "creator_name":
        creator = aliased(User)
        column = func.coalesce(creator.nickname, creator.username, "")
        query = query.outerjoin(creator, GlossaryEntry.creator_id == creator.id)
        return query.order_by(column.asc() if order == "asc" else column.desc(), GlossaryEntry.id.asc())
    if sort_by == "last_modified_by_name":
        modifier = aliased(User)
        column = func.coalesce(modifier.nickname, modifier.username, "")
        query = query.outerjoin(modifier, GlossaryEntry.last_modified_by_id == modifier.id)
        return query.order_by(column.asc() if order == "asc" else column.desc(), GlossaryEntry.id.asc())
    return query.order_by(GlossaryEntry.updated_at.desc(), GlossaryEntry.created_at.desc())


def _load_bound_glossary_base_ids(file_record: FileRecord) -> list[str]:
    raw_ids = getattr(file_record, "glossary_base_ids", "") or "[]"
    try:
        values = json.loads(raw_ids)
    except (TypeError, ValueError):
        values = []
    if not isinstance(values, list):
        values = []
    return list(dict.fromkeys(str(value) for value in values if value))


def _store_bound_glossary_base_ids(file_record: FileRecord, glossary_base_ids: list[str]) -> None:
    file_record.glossary_base_ids = json.dumps(list(dict.fromkeys(glossary_base_ids)))


@router.get("/glossary-bases")
def list_glossary_bases(db: Session = Depends(get_db)):
    rows = (
        db.query(GlossaryBase, func.count(GlossaryEntry.id).label("entry_count"))
        .outerjoin(GlossaryEntry, GlossaryEntry.glossary_base_id == GlossaryBase.id)
        .group_by(GlossaryBase.id)
        .order_by(GlossaryBase.created_at.desc())
        .all()
    )
    return [
        _serialize_glossary_base(glossary_base, int(entry_count))
        for glossary_base, entry_count in rows
    ]


@router.post("/glossary-bases")
def create_glossary_base(
    payload: GlossaryBasePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_resource_creator),
):
    name = _normalize_glossary_base_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="词汇表名称不能为空。")
    source_language, target_language = _require_glossary_language_pair(
        payload.source_language,
        payload.target_language,
    )
    glossary_base = GlossaryBase(
        name=name,
        description=normalize_text(payload.description or "") or None,
        source_language=source_language,
        target_language=target_language,
        creator_id=current_user.id,
    )
    db.add(glossary_base)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名词汇表已存在。") from exc
    db.refresh(glossary_base)
    return _serialize_glossary_base(glossary_base)


@router.get("/glossary-bases/{glossary_base_id}")
def get_glossary_base(
    glossary_base_id: UUID,
    db: Session = Depends(get_db),
):
    glossary_base = _get_glossary_base_or_404(db, glossary_base_id)
    entry_count = (
        db.query(GlossaryEntry)
        .filter(GlossaryEntry.glossary_base_id == glossary_base.id)
        .count()
    )
    return _serialize_glossary_base(glossary_base, entry_count)


@router.put("/glossary-bases/{glossary_base_id}")
def update_glossary_base(
    glossary_base_id: UUID,
    payload: GlossaryBasePayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    glossary_base = _get_glossary_base_or_404(db, glossary_base_id)
    name = _normalize_glossary_base_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="词汇表名称不能为空。")
    source_language, target_language = _require_glossary_language_pair(
        payload.source_language,
        payload.target_language,
    )
    glossary_base.name = name
    glossary_base.description = normalize_text(payload.description or "") or None
    glossary_base.source_language = source_language
    glossary_base.target_language = target_language
    (
        db.query(GlossaryEntry)
        .filter(GlossaryEntry.glossary_base_id == glossary_base.id)
        .update(
            {
                GlossaryEntry.source_language: source_language,
                GlossaryEntry.target_language: target_language,
            },
            synchronize_session=False,
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名词汇表已存在。") from exc
    db.refresh(glossary_base)
    entry_count = (
        db.query(GlossaryEntry)
        .filter(GlossaryEntry.glossary_base_id == glossary_base.id)
        .count()
    )
    return _serialize_glossary_base(glossary_base, entry_count)


@router.delete("/glossary-bases/{glossary_base_id}")
def delete_glossary_base(
    glossary_base_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    glossary_base = _get_glossary_base_or_404(db, glossary_base_id)
    entry_count = (
        db.query(GlossaryEntry)
        .filter(GlossaryEntry.glossary_base_id == glossary_base.id)
        .count()
    )
    deleted_id = str(glossary_base.id)
    bound_file_records = (
        db.query(FileRecord)
        .filter(FileRecord.glossary_base_ids.isnot(None))
        .all()
    )
    for file_record in bound_file_records:
        current_ids = _load_bound_glossary_base_ids(file_record)
        next_ids = [value for value in current_ids if value != deleted_id]
        if len(next_ids) != len(current_ids):
            _store_bound_glossary_base_ids(file_record, next_ids)
    db.delete(glossary_base)
    db.commit()
    return {"message": "词汇表已删除。", "deleted_entries": entry_count}


@router.post("/glossary-bases/import/preview")
async def preview_glossary_base_xlsx(
    file: UploadFile = File(...),
    glossary_base_id: UUID | None = Form(default=None),
    source_language: str = Form(...),
    target_language: str = Form(...),
    preview_limit: int = Form(default=100),
    skip_header: bool = Form(default=False),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in XLSX_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .xlsx 文件。")
    task_id, staged_file = await asyncio.to_thread(_stage_resource_upload_file, file)

    glossary_base = _get_glossary_base_or_404(db, glossary_base_id) if glossary_base_id else None
    if glossary_base is not None and glossary_base.source_language and glossary_base.target_language:
        resolved_source_language, resolved_target_language = _require_glossary_language_pair(
            source_language,
            target_language,
        )
        if (
            resolved_source_language != glossary_base.source_language
            or resolved_target_language != glossary_base.target_language
        ):
            raise HTTPException(status_code=400, detail="所选词汇表的语言对与本次导入不一致。")
    else:
        resolved_source_language, resolved_target_language = _require_glossary_language_pair(
            source_language,
            target_language,
        )

    try:
        preview = preview_glossary_from_xlsx_path(
            db=db,
            xlsx_path=staged_file["path"],
            filename=file.filename or "uploaded.xlsx",
            glossary_base_id=glossary_base_id,
            source_language=resolved_source_language,
            target_language=resolved_target_language,
            preview_limit=max(1, min(preview_limit, 500)),
            skip_header=skip_header,
            max_scan_rows=_resource_import_preview_max_scan_rows(),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"词汇表预览失败：{exc}") from exc
    finally:
        cleanup_import_task_staging(task_id)

    return {
        "filename": preview.filename,
        "rows": [
            {
                "row_index": row.row_index,
                "source_text": row.source_text,
                "target_text": row.target_text,
                "note": row.note,
                "status": row.status,
                "message": row.message,
            }
            for row in preview.rows
        ],
        "total_rows": preview.total_rows,
        "valid_rows": preview.valid_rows,
        "create_rows": preview.create_rows,
        "update_rows": preview.update_rows,
        "duplicate_rows": preview.duplicate_rows,
        "skipped_empty_rows": preview.skipped_empty_rows,
        "skipped_header_rows": preview.skipped_header_rows,
        "preview_limit": preview.preview_limit,
        "scanned_rows": preview.scanned_rows,
        "truncated": preview.truncated,
        "max_scan_rows": _resource_import_preview_max_scan_rows(),
        "glossary_base_id": str(glossary_base.id) if glossary_base else None,
        "glossary_base_name": glossary_base.name if glossary_base else "",
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


def _build_glossary_import_result_payload(
    *,
    import_summary,
    glossary_base_response_id: UUID,
    glossary_base_response_name: str,
    resolved_source_language: str,
    resolved_target_language: str,
) -> dict[str, Any]:
    return {
        "filename": import_summary.filename,
        "created_rows": import_summary.created_rows,
        "updated_rows": import_summary.updated_rows,
        "skipped_empty_rows": import_summary.skipped_empty_rows,
        "skipped_header_rows": import_summary.skipped_header_rows,
        "imported_rows": import_summary.imported_rows,
        "glossary_base_id": str(glossary_base_response_id),
        "glossary_base_name": glossary_base_response_name,
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


def _run_glossary_resource_import_task(task_id: str, payload: dict[str, Any]) -> None:
    staging_task_id = str(payload.get("staging_task_id") or task_id)
    set_import_task_status(task_id, "running", progress=5, message="词汇表导入开始处理。")
    try:
        raise_if_import_task_canceled(task_id)
        with SessionLocal() as db:
            file_payload = payload["file"]
            filename = file_payload.get("filename") or "uploaded.xlsx"
            glossary_base_id = UUID(str(payload["glossary_base_id"]))
            creator_id = UUID(str(payload["creator_id"]))

            def cancel_check() -> bool:
                return import_task_cancel_requested(task_id)

            try:
                set_import_task_status(task_id, "running", progress=20, message=f"正在导入 {filename}。")
                import_summary = import_glossary_from_xlsx_path(
                    db=db,
                    xlsx_path=file_payload["path"],
                    filename=filename,
                    glossary_base_id=glossary_base_id,
                    source_language=str(payload["source_language"]),
                    target_language=str(payload["target_language"]),
                    creator_id=creator_id,
                    skip_header=bool(payload.get("skip_header")),
                    batch_size=_resource_import_batch_size(),
                    cancel_check=cancel_check,
                )
            except ImportTaskCanceled:
                db.rollback()
                raise
            except Exception as exc:
                db.rollback()
                raise RuntimeError(f"词汇表导入失败：{exc}") from exc
            raise_if_import_task_canceled(task_id)
            db.commit()

            try:
                set_import_task_status(task_id, "running", progress=90, message="正在写入导入通知。")
                raise_if_import_task_canceled(task_id)
                notification_title, notification_body = build_resource_import_notification(
                    resource_label="词汇表",
                    resource_name=str(payload["glossary_base_name"]),
                    filename=import_summary.filename,
                    imported_rows=import_summary.imported_rows,
                    created_rows=import_summary.created_rows,
                    updated_rows=import_summary.updated_rows,
                    skipped_empty_rows=import_summary.skipped_empty_rows,
                    skipped_header_rows=import_summary.skipped_header_rows,
                    source_language=str(payload["source_language"]),
                    target_language=str(payload["target_language"]),
                )
                create_operation_notification(
                    db,
                    user_id=creator_id,
                    notification_type="resource_import",
                    title=notification_title,
                    body=notification_body,
                )
                db.commit()
            except ImportTaskCanceled:
                db.rollback()
                raise
            except Exception:
                db.rollback()
                logger.exception("Glossary import post-processing failed")

            raise_if_import_task_canceled(task_id)
            result = _build_glossary_import_result_payload(
                import_summary=import_summary,
                glossary_base_response_id=glossary_base_id,
                glossary_base_response_name=str(payload["glossary_base_name"]),
                resolved_source_language=str(payload["source_language"]),
                resolved_target_language=str(payload["target_language"]),
            )
            set_import_task_status(
                task_id,
                "completed",
                progress=100,
                message="词汇表导入完成。",
                result=result,
            )
    except ImportTaskCanceled:
        set_import_task_status(task_id, "canceled", progress=100, message="词汇表导入已取消。")
    except Exception as exc:
        logger.exception("glossary resource import task failed task_id=%s", task_id)
        set_import_task_status(
            task_id,
            "failed",
            progress=100,
            message="词汇表导入失败。",
            error=str(exc),
        )
    finally:
        cleanup_import_task_staging(staging_task_id)


async def _queue_glossary_resource_import_task(
    background_tasks: BackgroundTasks,
    task_id: str,
    payload: dict[str, Any],
) -> None:
    from app.routers.api import ARQ_IMPORT_QUEUE_NAME, _enqueue_arq_job

    if await _enqueue_arq_job(
        "glossary_resource_import_job",
        task_id,
        payload,
        queue_name=ARQ_IMPORT_QUEUE_NAME,
    ):
        return
    background_tasks.add_task(_run_glossary_resource_import_task, task_id, payload)


@router.post("/glossary-bases/import-xlsx")
async def import_glossary_base_xlsx(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    glossary_base_id: UUID | None = Form(default=None),
    source_language: str = Form(...),
    target_language: str = Form(...),
    skip_header: bool = Form(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if glossary_base_id is None:
        raise HTTPException(status_code=400, detail="请先选择要导入的词汇表。")
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in XLSX_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .xlsx 文件。")
    task_id, staged_file = await asyncio.to_thread(_stage_resource_upload_file, file)
    try:
        glossary_base = _get_glossary_base_or_404(db, glossary_base_id)
        resolved_source_language, resolved_target_language = _resolve_glossary_base_language_pair(
            glossary_base,
            source_language,
            target_language,
        )
        db.commit()
        payload = {
            "kind": "glossary_resource_import",
            "staging_task_id": task_id,
            "file": staged_file,
            "glossary_base_id": str(glossary_base.id),
            "glossary_base_name": glossary_base.name,
            "source_language": resolved_source_language,
            "target_language": resolved_target_language,
            "skip_header": bool(skip_header),
            "creator_id": str(current_user.id),
        }
        set_import_task_status(task_id, "queued", progress=0, message="词汇表导入任务已进入队列。")
        await _queue_glossary_resource_import_task(background_tasks, task_id, payload)
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "queued",
                "progress": 0,
                "message": "词汇表导入任务已进入队列。",
            },
        )
    except Exception:
        cleanup_import_task_staging(task_id)
        raise


@router.get("/glossary-bases/{glossary_base_id}/entries")
def list_glossary_entries(
    glossary_base_id: UUID,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    case_sensitive: bool = False,
    sort_by: str | None = None,
    sort_order: str | None = "desc",
    db: Session = Depends(get_db),
):
    glossary_base = _get_glossary_base_or_404(db, glossary_base_id)
    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 200)
    query = db.query(GlossaryEntry).filter(GlossaryEntry.glossary_base_id == glossary_base.id)
    normalized_search = normalize_text(search or "")
    if normalized_search:
        like_pattern = f"%{normalized_search}%"
        fields = (GlossaryEntry.source_text, GlossaryEntry.target_text, GlossaryEntry.note)
        if case_sensitive:
            query = query.filter(or_(*(field.like(like_pattern) for field in fields)))
        else:
            query = query.filter(or_(*(field.ilike(like_pattern) for field in fields)))

    total = query.count()
    rows = (
        _apply_glossary_entry_sort(query, sort_by, sort_order)
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )
    return {
        "items": [_serialize_glossary_entry(row) for row in rows],
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
    }


@router.post("/glossary-bases/{glossary_base_id}/entries")
def create_glossary_entry(
    glossary_base_id: UUID,
    payload: GlossaryEntryPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    glossary_base = _get_glossary_base_or_404(db, glossary_base_id)
    source_text = normalize_text(payload.source_text)
    target_text = normalize_text(payload.target_text)
    note = normalize_text(payload.note or "")
    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="原文和译文不能为空。")
    source_normalized = normalize_match_text(source_text) or source_text
    duplicate = (
        db.query(GlossaryEntry)
        .filter(
            GlossaryEntry.glossary_base_id == glossary_base.id,
            GlossaryEntry.source_language == glossary_base.source_language,
            GlossaryEntry.target_language == glossary_base.target_language,
            or_(
                GlossaryEntry.source_normalized == source_normalized,
                GlossaryEntry.source_text == source_text,
            ),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="当前词汇表中已存在相同原文的词条。")
    entry = GlossaryEntry(
        glossary_base_id=glossary_base.id,
        source_text=source_text,
        target_text=target_text,
        note=note or None,
        source_normalized=source_normalized,
        source_language=glossary_base.source_language,
        target_language=glossary_base.target_language,
        creator_id=current_user.id,
        last_modified_by_id=current_user.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _serialize_glossary_entry(entry)


@router.put("/glossary-entries/{entry_id}")
def update_glossary_entry(
    entry_id: UUID,
    payload: GlossaryEntryPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    entry = db.query(GlossaryEntry).filter(GlossaryEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="词汇条目不存在。")
    glossary_base = _get_glossary_base_or_404(db, entry.glossary_base_id)
    source_text = normalize_text(payload.source_text)
    target_text = normalize_text(payload.target_text)
    note = normalize_text(payload.note or "")
    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="原文和译文不能为空。")
    source_normalized = normalize_match_text(source_text) or source_text
    duplicate = (
        db.query(GlossaryEntry)
        .filter(
            GlossaryEntry.id != entry.id,
            GlossaryEntry.glossary_base_id == entry.glossary_base_id,
            GlossaryEntry.source_language == glossary_base.source_language,
            GlossaryEntry.target_language == glossary_base.target_language,
            or_(
                GlossaryEntry.source_normalized == source_normalized,
                GlossaryEntry.source_text == source_text,
            ),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="当前词汇表中已存在相同原文的词条。")
    entry.source_text = source_text
    entry.target_text = target_text
    entry.note = note or None
    entry.source_normalized = source_normalized
    entry.source_language = glossary_base.source_language
    entry.target_language = glossary_base.target_language
    if entry.creator_id is None:
        entry.creator_id = current_user.id
    entry.last_modified_by_id = current_user.id
    db.commit()
    db.refresh(entry)
    return _serialize_glossary_entry(entry)


@router.delete("/glossary-entries/{entry_id}")
def delete_glossary_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    entry = db.query(GlossaryEntry).filter(GlossaryEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="词汇条目不存在。")
    db.delete(entry)
    db.commit()
    return {"message": "词汇条目已删除。"}


@router.get("/glossary-bases/{glossary_base_id}/export-xlsx")
def export_glossary_entries_xlsx(
    glossary_base_id: UUID,
    db: Session = Depends(get_db),
):
    glossary_base = _get_glossary_base_or_404(db, glossary_base_id)
    rows = (
        db.query(GlossaryEntry)
        .filter(GlossaryEntry.glossary_base_id == glossary_base.id)
        .order_by(GlossaryEntry.updated_at.desc(), GlossaryEntry.created_at.desc())
        .all()
    )
    xlsx_bytes = build_tabular_xlsx(
        sheet_title=glossary_base.name,
        headers=["原文", "译文", "备注", "源语言", "目标语言", "创建人", "创建时间", "最后修改人", "更新时间"],
        rows=[
            [
                entry.source_text,
                entry.target_text,
                entry.note or "",
                entry.source_language,
                entry.target_language,
                (entry.creator.nickname or entry.creator.username) if entry.creator else "",
                entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                (entry.last_modified_by.nickname or entry.last_modified_by.username) if entry.last_modified_by else "",
                entry.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
            ]
            for entry in rows
        ],
    )
    return build_xlsx_download_response(f"{glossary_base.name}-glossary.xlsx", xlsx_bytes)


@router.post("/glossary-bases/{glossary_base_id}/exports")
def queue_glossary_base_export(
    glossary_base_id: UUID,
    format: ResourceExportFormat = Query(default="xlsx"),
    db: Session = Depends(get_db),
):
    _get_glossary_base_or_404(db, glossary_base_id)
    return JSONResponse(
        status_code=202,
        content=queue_resource_export(
            resource_type="glossary",
            resource_id=glossary_base_id,
            export_format=format,
        ),
    )


@router.get("/glossary-bases/export-tasks/{task_id}")
def get_glossary_base_export_task(task_id: str):
    return ensure_export_task_status(task_id, expected_resource_type="glossary")


@router.post("/glossary-bases/export-tasks/{task_id}/cancel")
def cancel_glossary_base_export_task(task_id: str):
    return cancel_resource_export_task(task_id, expected_resource_type="glossary")


@router.get("/glossary-bases/export-tasks/{task_id}/download")
def download_glossary_base_export_task(task_id: str):
    return build_resource_export_download_response(task_id, expected_resource_type="glossary")
