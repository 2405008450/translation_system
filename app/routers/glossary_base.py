from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_admin
from app.database import get_db
from app.models import FileRecord, GlossaryBase, GlossaryEntry, User
from app.services.glossary_importer import (
    XLSX_EXTENSIONS,
    import_glossary_from_xlsx_upload,
    preview_glossary_from_xlsx_upload,
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
    ensure_export_task_status,
    queue_resource_export,
)
from app.services.xlsx_exporter import build_tabular_xlsx, build_xlsx_download_response


router = APIRouter(dependencies=[Depends(get_current_user)])


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
    _: User = Depends(require_admin),
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


@router.post("/glossary-bases/import-xlsx/preview")
@router.post("/glossary-bases/import/preview", include_in_schema=False)
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
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的 XLSX 文件为空。")

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
        preview = preview_glossary_from_xlsx_upload(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "uploaded.xlsx",
            glossary_base_id=glossary_base_id,
            source_language=resolved_source_language,
            target_language=resolved_target_language,
            preview_limit=max(1, min(preview_limit, 500)),
            skip_header=skip_header,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"词汇表预览失败：{exc}") from exc

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
        "glossary_base_id": str(glossary_base.id) if glossary_base else None,
        "glossary_base_name": glossary_base.name if glossary_base else "",
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


@router.post("/glossary-bases/import-xlsx")
async def import_glossary_base_xlsx(
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
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的 XLSX 文件为空。")

    glossary_base = _get_glossary_base_or_404(db, glossary_base_id)
    resolved_source_language, resolved_target_language = _resolve_glossary_base_language_pair(
        glossary_base,
        source_language,
        target_language,
    )
    try:
        import_summary = import_glossary_from_xlsx_upload(
            db=db,
            raw_bytes=raw_bytes,
            filename=file.filename or "uploaded.xlsx",
            glossary_base_id=glossary_base_id,
            source_language=resolved_source_language,
            target_language=resolved_target_language,
            creator_id=current_user.id,
            skip_header=skip_header,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"词汇表导入失败：{exc}") from exc

    notification_title, notification_body = build_resource_import_notification(
        resource_label="词汇表",
        resource_name=glossary_base.name,
        filename=import_summary.filename,
        imported_rows=import_summary.imported_rows,
        created_rows=import_summary.created_rows,
        updated_rows=import_summary.updated_rows,
        skipped_empty_rows=import_summary.skipped_empty_rows,
        skipped_header_rows=import_summary.skipped_header_rows,
        source_language=resolved_source_language,
        target_language=resolved_target_language,
    )
    create_operation_notification(
        db,
        user_id=current_user.id,
        notification_type="resource_import",
        title=notification_title,
        body=notification_body,
    )
    db.commit()

    return {
        "filename": import_summary.filename,
        "created_rows": import_summary.created_rows,
        "updated_rows": import_summary.updated_rows,
        "skipped_empty_rows": import_summary.skipped_empty_rows,
        "skipped_header_rows": import_summary.skipped_header_rows,
        "imported_rows": import_summary.imported_rows,
        "glossary_base_id": str(glossary_base.id),
        "glossary_base_name": glossary_base.name,
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


@router.get("/glossary-bases/{glossary_base_id}/entries")
def list_glossary_entries(
    glossary_base_id: UUID,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    case_sensitive: bool = False,
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
        query
        .order_by(GlossaryEntry.updated_at.desc(), GlossaryEntry.created_at.desc())
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


@router.get("/glossary-bases/export-tasks/{task_id}/download")
def download_glossary_base_export_task(task_id: str):
    return build_resource_export_download_response(task_id, expected_resource_type="glossary")
