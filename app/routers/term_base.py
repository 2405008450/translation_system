from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
from concurrent.futures import CancelledError, ThreadPoolExecutor
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.auth import (
    can_access_all_projects,
    get_current_user,
    get_user_display_name,
    is_admin_role,
    require_admin,
    require_resource_creator,
)
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import FileAssignment, FileRecord, ProjectAssignment, TermBase, TermEntry, User
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
from app.services.term_entry_service import (
    build_term_entry_conflict_items,
    save_term_entries_batch,
)
from app.services.term_importer import (
    TERM_IMPORT_EXTENSIONS,
    TBX_EXTENSIONS,
    TMX_EXTENSIONS,
    XLSX_EXTENSIONS,
    import_terms_from_tbx_path,
    import_terms_from_tmx_path,
    import_terms_from_xlsx_path,
    import_terms_from_xlsx_upload,
    preview_terms_from_tbx_path,
    preview_terms_from_tmx_path,
    preview_terms_from_xlsx_path,
    preview_terms_from_upload,
)
from app.services.resource_import_batch import create_resource_import_batch
from app.services.import_task_storage import (
    cleanup_import_task_staging,
    read_import_file_bytes,
    stage_import_file_streams,
)
from app.services.import_task_state import (
    ImportTaskCanceled,
    import_task_cancel_requested,
    raise_if_import_task_canceled,
    set_import_task_status,
)
from app.services.task_file_service import UploadLimitError
from app.services.xlsx_exporter import build_tabular_xlsx, build_xlsx_download_response


router = APIRouter(dependencies=[Depends(get_current_user)])
logger = logging.getLogger(__name__)
_TERM_RESOURCE_IMPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="term-resource-import")


class TermBasePayload(BaseModel):
    name: str
    description: str | None = None
    source_language: str
    target_language: str


class TermBaseLanguagePairCopyPayload(BaseModel):
    name: str | None = None
    description: str | None = None
    source_language: str
    target_language: str


class TermBaseMergePayload(BaseModel):
    source_term_base_ids: list[UUID]
    name: str
    description: str | None = None


class TermEntryUpdatePayload(BaseModel):
    source_text: str
    target_text: str
    file_record_id: UUID | None = None


class TermEntryDraftPayload(BaseModel):
    source_text: str
    target_text: str
    action: Literal["add", "replace", "skip"] = "add"


class TermEntryConflictPayload(BaseModel):
    entries: list[TermEntryDraftPayload]


class TermEntryBatchPayload(BaseModel):
    entries: list[TermEntryDraftPayload]


def _normalize_term_base_name(name: str) -> str:
    return " ".join(name.strip().split())


def _build_unique_term_base_name(db: Session, raw_name: str) -> str:
    base_name = _normalize_term_base_name(raw_name)
    if not base_name:
        raise HTTPException(status_code=400, detail="术语库名称不能为空。")
    base_name = base_name[:120].rstrip()
    candidate = base_name
    suffix_index = 2
    while db.query(TermBase.id).filter(TermBase.name == candidate).first() is not None:
        suffix = f" {suffix_index}"
        candidate = f"{base_name[:120 - len(suffix)].rstrip()}{suffix}"
        suffix_index += 1
    return candidate


def _require_term_language_pair(
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    try:
        return require_language_pair(source_language, target_language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _get_term_base_or_404(db: Session, term_base_id: UUID) -> TermBase:
    term_base = db.query(TermBase).filter(TermBase.id == term_base_id).first()
    if term_base is None:
        raise HTTPException(status_code=404, detail="术语库不存在。")
    return term_base


def _resolve_term_base_language_pair(
    term_base: TermBase,
    source_language: str | None,
    target_language: str | None,
) -> tuple[str, str]:
    if term_base.source_language and term_base.target_language:
        normalized_source_language, normalized_target_language = _require_term_language_pair(
            source_language,
            target_language,
        )
        if (
            normalized_source_language != term_base.source_language
            or normalized_target_language != term_base.target_language
        ):
            raise HTTPException(status_code=400, detail="所选术语库的语言对与本次导入不一致。")
        return normalized_source_language, normalized_target_language

    normalized_source_language, normalized_target_language = _require_term_language_pair(
        source_language,
        target_language,
    )
    term_base.source_language = normalized_source_language
    term_base.target_language = normalized_target_language
    return normalized_source_language, normalized_target_language


def _serialize_term_base(term_base: TermBase, entry_count: int = 0) -> dict:
    return {
        "id": term_base.id,
        "name": term_base.name,
        "description": term_base.description,
        "source_language": term_base.source_language,
        "target_language": term_base.target_language,
        "creator_id": str(term_base.creator_id) if term_base.creator_id else None,
        "creator_name": get_user_display_name(term_base.creator),
        "created_at": term_base.created_at.isoformat(),
        "updated_at": term_base.updated_at.isoformat(),
        "entry_count": entry_count,
    }


def _require_same_term_base_language_pair(
    term_bases: list[TermBase],
) -> tuple[str, str]:
    try:
        source_language, target_language = require_language_pair(
            term_bases[0].source_language,
            term_bases[0].target_language,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="选中的术语库缺少语言对，无法合并。") from None

    for term_base in term_bases[1:]:
        try:
            candidate_source_language, candidate_target_language = require_language_pair(
                term_base.source_language,
                term_base.target_language,
            )
        except ValueError:
            raise HTTPException(status_code=400, detail="选中的术语库缺少语言对，无法合并。") from None
        if (
            candidate_source_language != source_language
            or candidate_target_language != target_language
        ):
            raise HTTPException(status_code=400, detail="只能合并语言对完全一致的术语库。")

    return source_language, target_language


def _serialize_term_entry(entry: TermEntry) -> dict:
    creator_name = None
    if entry.creator:
        creator_name = entry.creator.nickname or entry.creator.username
    last_modified_by_name = None
    if entry.last_modified_by:
        last_modified_by_name = entry.last_modified_by.nickname or entry.last_modified_by.username
    return {
        "id": entry.id,
        "term_base_id": entry.term_base_id,
        "source_text": entry.source_text,
        "target_text": entry.target_text,
        "source_language": entry.source_language,
        "target_language": entry.target_language,
        "creator_id": entry.creator_id,
        "creator_name": creator_name,
        "last_modified_by_id": entry.last_modified_by_id,
        "last_modified_by_name": last_modified_by_name,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


def _apply_term_entry_sort(query, sort_by: str | None, sort_order: str | None):
    order = "asc" if sort_order == "asc" else "desc"
    sort_columns = {
        "source_text": TermEntry.source_text,
        "target_text": TermEntry.target_text,
        "created_at": TermEntry.created_at,
        "updated_at": TermEntry.updated_at,
    }
    if sort_by in sort_columns:
        column = sort_columns[sort_by]
        return query.order_by(column.asc() if order == "asc" else column.desc(), TermEntry.id.asc())
    if sort_by == "creator_name":
        creator = aliased(User)
        column = func.coalesce(creator.nickname, creator.username, "")
        query = query.outerjoin(creator, TermEntry.creator_id == creator.id)
        return query.order_by(column.asc() if order == "asc" else column.desc(), TermEntry.id.asc())
    if sort_by == "last_modified_by_name":
        modifier = aliased(User)
        column = func.coalesce(modifier.nickname, modifier.username, "")
        query = query.outerjoin(modifier, TermEntry.last_modified_by_id == modifier.id)
        return query.order_by(column.asc() if order == "asc" else column.desc(), TermEntry.id.asc())
    return query.order_by(TermEntry.updated_at.desc(), TermEntry.created_at.desc())


def _require_term_entry_owner_or_admin(entry: TermEntry, current_user: User) -> None:
    if can_access_all_projects(current_user):
        return
    if entry.creator_id is not None and entry.creator_id == current_user.id:
        return
    raise HTTPException(status_code=403, detail="只能编辑或删除自己添加的术语条目。")


def _validate_term_import_upload(file: UploadFile, raw_bytes: bytes | None = None) -> str:
    extension = f".{(file.filename or '').split('.')[-1].lower()}" if file.filename else ""
    if extension not in TERM_IMPORT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持上传 .tmx、.xls、.xlsx 或 .csv 文件。")
    if raw_bytes is not None and not raw_bytes:
        raise HTTPException(status_code=400, detail="上传的术语文件为空。")
    return extension


def _resource_import_preview_max_scan_rows() -> int:
    settings = get_settings()
    term_limit = int(settings.term_import_preview_max_scan_rows or 5000)
    resource_limit = int(settings.resource_import_preview_max_scan_rows or 1000)
    return max(1, min(term_limit, resource_limit, 10000))


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
            [(file.filename or "uploaded", file.file)],
            max_files=1,
            max_size_resolver=_resource_import_max_file_bytes,
            max_total_bytes=_resource_import_max_file_bytes(file.filename or "uploaded"),
        )[0]
        return task_id, staged
    except UploadLimitError as exc:
        cleanup_import_task_staging(task_id)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception:
        cleanup_import_task_staging(task_id)
        raise


async def _queue_term_resource_import_task(task_id: str, payload: dict[str, Any]) -> None:
    from app.routers.api import ARQ_IMPORT_QUEUE_NAME, _enqueue_arq_job

    if await _enqueue_arq_job(
        "term_resource_import_job",
        task_id,
        payload,
        queue_name=ARQ_IMPORT_QUEUE_NAME,
    ):
        return
    future = _TERM_RESOURCE_IMPORT_EXECUTOR.submit(_run_term_resource_import_task, task_id, payload)
    future.add_done_callback(_log_local_term_resource_import_failure(task_id))


def _log_local_term_resource_import_failure(task_id: str):
    def _callback(done: Any) -> None:
        try:
            exc = done.exception()
        except CancelledError:
            return
        if exc is not None:
            logger.error(
                "local term resource import task failed task_id=%s",
                task_id,
                exc_info=(type(exc), exc, exc.__traceback__),
            )

    return _callback


def _parse_import_row_indexes(value: str | None) -> set[int]:
    if not value:
        return set()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail="重复行处理参数格式不正确。")

    row_indexes: set[int] = set()
    for item in parsed:
        try:
            row_index = int(item)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="重复行处理参数必须是行号列表。") from exc
        if row_index > 0:
            row_indexes.add(row_index)
    return row_indexes


def _load_bound_ids(file_record: FileRecord, field_name: str) -> list[str]:
    raw_ids = getattr(file_record, field_name, "") or "[]"
    try:
        values = json.loads(raw_ids)
    except (TypeError, ValueError):
        values = []
    if not isinstance(values, list):
        values = []
    ids = [str(value) for value in values if value]
    if field_name == "term_base_ids" and not ids and file_record.term_base_id:
        ids.append(str(file_record.term_base_id))
    return list(dict.fromkeys(ids))


def _load_bound_term_base_ids(file_record: FileRecord) -> list[str]:
    return _load_bound_ids(file_record, "term_base_ids")


def _file_record_allows_term_base_write(file_record: FileRecord, term_base_id: UUID) -> bool:
    term_base_id_text = str(term_base_id)
    enabled_ids = set(_load_bound_term_base_ids(file_record))
    writable_ids = set(_load_bound_ids(file_record, "term_base_write_ids"))
    return term_base_id_text in enabled_ids and term_base_id_text in writable_ids


def _can_use_file_record_term_write_context(
    db: Session,
    file_record: FileRecord,
    current_user: User,
) -> bool:
    if can_access_all_projects(current_user):
        return True

    user_id = getattr(current_user, "id", None)
    if user_id is None or file_record.project_id is None:
        return False

    has_project_assignment = (
        db.query(ProjectAssignment.id)
        .filter(
            ProjectAssignment.project_id == file_record.project_id,
            ProjectAssignment.assignee_id == user_id,
            ProjectAssignment.status == "active",
        )
        .first()
        is not None
    )
    if not has_project_assignment:
        return False

    return (
        db.query(FileAssignment.id)
        .filter(
            FileAssignment.file_record_id == file_record.id,
            FileAssignment.assignee_id == user_id,
            FileAssignment.status == "active",
        )
        .first()
        is not None
    )


def _require_term_entry_create_access(
    db: Session,
    term_base_id: UUID,
    file_record_id: UUID | None,
    current_user: User,
) -> None:
    if is_admin_role(getattr(current_user, "role", None)):
        return

    if file_record_id is None:
        raise HTTPException(status_code=403, detail="请从已开启写入权限的项目文件中添加术语。")

    file_record = db.query(FileRecord).filter(FileRecord.id == file_record_id).first()
    if file_record is None:
        raise HTTPException(status_code=404, detail="文件不存在。")

    if not _can_use_file_record_term_write_context(db, file_record, current_user):
        raise HTTPException(status_code=403, detail="当前账号没有处理该文件的权限。")

    if not _file_record_allows_term_base_write(file_record, term_base_id):
        raise HTTPException(status_code=403, detail="目标术语库未在当前项目设置中开启写入权限。")


def _store_bound_term_base_ids(file_record: FileRecord, term_base_ids: list[str]) -> None:
    normalized_ids = list(dict.fromkeys(term_base_ids))
    file_record.term_base_id = UUID(normalized_ids[0]) if normalized_ids else None
    file_record.term_base_ids = json.dumps(normalized_ids)


def _store_bound_ids(file_record: FileRecord, field_name: str, ids: list[str]) -> None:
    if hasattr(file_record, field_name):
        setattr(file_record, field_name, json.dumps(list(dict.fromkeys(ids))))


@router.get("/term-bases")
def list_term_bases(db: Session = Depends(get_db)):
    rows = (
        db.query(TermBase, func.count(TermEntry.id).label("entry_count"))
        .outerjoin(TermEntry, TermEntry.term_base_id == TermBase.id)
        .group_by(TermBase.id)
        .order_by(TermBase.created_at.desc())
        .all()
    )
    return [
        _serialize_term_base(term_base, int(entry_count))
        for term_base, entry_count in rows
    ]


@router.get("/term-bases/{term_base_id}")
def get_term_base(
    term_base_id: UUID,
    db: Session = Depends(get_db),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    entry_count = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .count()
    )
    return _serialize_term_base(term_base, entry_count)


@router.post("/term-bases")
def create_term_base(
    payload: TermBasePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_resource_creator),
):
    name = _normalize_term_base_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="术语库名称不能为空。")
    source_language, target_language = _require_term_language_pair(
        payload.source_language,
        payload.target_language,
    )

    term_base = TermBase(
        name=name,
        description=normalize_text(payload.description or "") or None,
        source_language=source_language,
        target_language=target_language,
        creator_id=current_user.id,
    )
    db.add(term_base)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名术语库已存在。") from exc

    db.refresh(term_base)
    return _serialize_term_base(term_base)


@router.post("/term-bases/merge")
def merge_term_bases(
    payload: TermBaseMergePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    source_term_base_ids = list(dict.fromkeys(payload.source_term_base_ids))
    if len(source_term_base_ids) < 2:
        raise HTTPException(status_code=400, detail="请至少选择两个术语库进行合并。")

    term_bases = (
        db.query(TermBase)
        .filter(TermBase.id.in_(source_term_base_ids))
        .all()
    )
    term_base_by_id = {term_base.id: term_base for term_base in term_bases}
    missing_ids = [
        term_base_id
        for term_base_id in source_term_base_ids
        if term_base_id not in term_base_by_id
    ]
    if missing_ids:
        raise HTTPException(status_code=404, detail="选择的术语库不存在。")

    ordered_term_bases = [
        term_base_by_id[term_base_id]
        for term_base_id in source_term_base_ids
    ]
    source_language, target_language = _require_same_term_base_language_pair(
        ordered_term_bases,
    )
    name = _normalize_term_base_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="合并后的术语库名称不能为空。")

    target_term_base = TermBase(
        name=name,
        description=normalize_text(payload.description or "") or None,
        source_language=source_language,
        target_language=target_language,
        creator_id=current_user.id,
    )
    db.add(target_term_base)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名术语库已存在。") from exc

    created_rows = 0
    updated_rows = 0
    skipped_rows = 0
    merged_by_source_normalized: dict[str, TermEntry] = {}
    merged_by_source_text: dict[str, TermEntry] = {}

    for source_term_base_id in source_term_base_ids:
        source_entries = (
            db.query(TermEntry)
            .filter(TermEntry.term_base_id == source_term_base_id)
            .order_by(TermEntry.created_at.asc(), TermEntry.updated_at.asc())
            .all()
        )
        for entry in source_entries:
            source_text = normalize_text(entry.source_text)
            target_text = normalize_text(entry.target_text)
            if not source_text or not target_text:
                skipped_rows += 1
                continue

            source_normalized = entry.source_normalized or normalize_match_text(source_text) or source_text
            existing = (
                merged_by_source_normalized.get(source_normalized)
                or merged_by_source_text.get(source_text)
            )
            if existing is not None:
                existing.source_text = source_text
                existing.target_text = target_text
                existing.source_normalized = source_normalized
                existing.source_language = source_language
                existing.target_language = target_language
                if existing.creator_id is None:
                    existing.creator_id = entry.creator_id or current_user.id
                existing.last_modified_by_id = current_user.id
                merged_by_source_normalized[source_normalized] = existing
                merged_by_source_text[source_text] = existing
                updated_rows += 1
                continue

            merged_entry = TermEntry(
                term_base_id=target_term_base.id,
                source_text=source_text,
                target_text=target_text,
                source_normalized=source_normalized,
                source_language=source_language,
                target_language=target_language,
                creator_id=entry.creator_id or current_user.id,
                last_modified_by_id=current_user.id,
            )
            db.add(merged_entry)
            merged_by_source_normalized[source_normalized] = merged_entry
            merged_by_source_text[source_text] = merged_entry
            created_rows += 1

    db.commit()

    entry_count = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == target_term_base.id)
        .count()
    )
    db.refresh(target_term_base)
    return {
        "term_base": _serialize_term_base(target_term_base, entry_count),
        "source_count": len(source_term_base_ids),
        "created_rows": created_rows,
        "updated_rows": updated_rows,
        "skipped_rows": skipped_rows,
        "merged_rows": created_rows + updated_rows,
    }


@router.put("/term-bases/{term_base_id}")
def update_term_base(
    term_base_id: UUID,
    payload: TermBasePayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    name = _normalize_term_base_name(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="术语库名称不能为空。")
    source_language, target_language = _require_term_language_pair(
        payload.source_language,
        payload.target_language,
    )

    term_base.name = name
    term_base.description = normalize_text(payload.description or "") or None
    term_base.source_language = source_language
    term_base.target_language = target_language
    (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .update(
            {
                TermEntry.source_language: source_language,
                TermEntry.target_language: target_language,
            },
            synchronize_session=False,
        )
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="同名术语库已存在。") from exc

    db.refresh(term_base)
    entry_count = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .count()
    )
    return _serialize_term_base(term_base, entry_count)


@router.post("/term-bases/{term_base_id}/copy-language-pair")
def copy_term_base_to_language_pair(
    term_base_id: UUID,
    payload: TermBaseLanguagePairCopyPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    source_term_base = _get_term_base_or_404(db, term_base_id)
    source_language, target_language = _require_term_language_pair(
        payload.source_language,
        payload.target_language,
    )
    default_name = f"{source_term_base.name} {source_language}->{target_language}"
    name = _build_unique_term_base_name(db, payload.name or default_name)

    target_term_base = TermBase(
        name=name,
        description=normalize_text(
            payload.description if payload.description is not None else (source_term_base.description or "")
        ) or None,
        source_language=source_language,
        target_language=target_language,
        creator_id=current_user.id,
    )
    db.add(target_term_base)
    try:
        db.flush()
        source_entries = (
            db.query(TermEntry)
            .filter(TermEntry.term_base_id == source_term_base.id)
            .order_by(TermEntry.created_at.asc(), TermEntry.id.asc())
            .all()
        )
        for entry in source_entries:
            db.add(TermEntry(
                term_base_id=target_term_base.id,
                source_text=entry.source_text,
                target_text=entry.target_text,
                source_normalized=entry.source_normalized,
                source_language=source_language,
                target_language=target_language,
                creator_id=entry.creator_id or current_user.id,
                last_modified_by_id=current_user.id,
                external_tuid=entry.external_tuid,
                tmx_metadata=deepcopy(entry.tmx_metadata),
            ))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="复制术语库失败，可能存在同名库。") from exc

    db.refresh(target_term_base)
    entry_count = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == target_term_base.id)
        .count()
    )
    return _serialize_term_base(target_term_base, entry_count)


@router.delete("/term-bases/{term_base_id}")
def delete_term_base(
    term_base_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    entry_count = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .count()
    )
    (
        db.query(FileRecord)
        .filter(FileRecord.term_base_id == term_base.id)
        .update({FileRecord.term_base_id: None}, synchronize_session=False)
    )
    deleted_id = str(term_base.id)
    bound_file_records = (
        db.query(FileRecord)
        .filter(FileRecord.term_base_ids.isnot(None))
        .all()
    )
    for file_record in bound_file_records:
        current_ids = _load_bound_term_base_ids(file_record)
        next_ids = [
            term_base_id
            for term_base_id in current_ids
            if term_base_id != deleted_id
        ]
        if len(next_ids) != len(current_ids):
            _store_bound_term_base_ids(file_record, next_ids)
        for field_name in ("term_base_write_ids", "qa_term_base_ids"):
            current_extra_ids = _load_bound_ids(file_record, field_name)
            next_extra_ids = [
                term_base_id
                for term_base_id in current_extra_ids
                if term_base_id != deleted_id
            ]
            if len(next_extra_ids) != len(current_extra_ids):
                _store_bound_ids(file_record, field_name, next_extra_ids)
    (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .delete(synchronize_session=False)
    )

    db.delete(term_base)
    db.commit()
    return {"message": "术语库已删除。", "deleted_entries": entry_count}


@router.post("/term-bases/import/preview")
async def preview_term_base_xlsx(
    file: UploadFile = File(...),
    term_base_id: UUID | None = Form(default=None),
    source_language: str = Form(...),
    target_language: str = Form(...),
    preview_limit: int = Form(default=100),
    skip_header: bool = Form(default=False),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    extension = _validate_term_import_upload(file)
    task_id, staged_file = await asyncio.to_thread(_stage_resource_upload_file, file)

    term_base = _get_term_base_or_404(db, term_base_id) if term_base_id else None
    if term_base is not None:
        resolved_source_language, resolved_target_language = _resolve_term_base_language_pair(
            term_base,
            source_language,
            target_language,
        )
    else:
        resolved_source_language, resolved_target_language = _require_term_language_pair(
            source_language,
            target_language,
        )

    try:
        if extension in TBX_EXTENSIONS:
            preview = preview_terms_from_tbx_path(
                db=db,
                tbx_path=staged_file["path"],
                filename=file.filename or "uploaded.tbx",
                term_base_id=term_base_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                preview_limit=max(1, min(preview_limit, 500)),
                skip_header=skip_header,
                max_scan_rows=_resource_import_preview_max_scan_rows(),
            )
        elif extension in TMX_EXTENSIONS:
            preview = preview_terms_from_tmx_path(
                db=db,
                tmx_path=staged_file["path"],
                filename=file.filename or "uploaded.tmx",
                term_base_id=term_base_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                preview_limit=max(1, min(preview_limit, 500)),
                skip_header=skip_header,
                max_scan_rows=_resource_import_preview_max_scan_rows(),
            )
        elif extension in XLSX_EXTENSIONS:
            preview = preview_terms_from_xlsx_path(
                db=db,
                xlsx_path=staged_file["path"],
                filename=file.filename or "uploaded.xlsx",
                term_base_id=term_base_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                preview_limit=max(1, min(preview_limit, 500)),
                skip_header=skip_header,
                max_scan_rows=_resource_import_preview_max_scan_rows(),
            )
        else:
            preview = preview_terms_from_upload(
                db=db,
                raw_bytes=read_import_file_bytes(staged_file),
                filename=file.filename or "uploaded.xlsx",
                term_base_id=term_base_id,
                source_language=resolved_source_language,
                target_language=resolved_target_language,
                preview_limit=max(1, min(preview_limit, 500)),
                skip_header=skip_header,
                max_scan_rows=_resource_import_preview_max_scan_rows(),
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"术语库预览失败：{exc}") from exc
    finally:
        cleanup_import_task_staging(task_id)

    return {
        "filename": preview.filename,
        "rows": [
            {
                "row_index": row.row_index,
                "source_text": row.source_text,
                "target_text": row.target_text,
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
        "term_base_id": str(term_base.id) if term_base else None,
        "term_base_name": term_base.name if term_base else "",
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


def _build_term_import_result_payload(
    *,
    import_summary,
    term_base_response_id: UUID,
    term_base_response_name: str,
    resolved_source_language: str,
    resolved_target_language: str,
) -> dict[str, Any]:
    return {
        "filename": import_summary.filename,
        "created_rows": import_summary.created_rows,
        "updated_rows": import_summary.updated_rows,
        "skipped_duplicate_rows": import_summary.skipped_duplicate_rows,
        "skipped_empty_rows": import_summary.skipped_empty_rows,
        "skipped_header_rows": import_summary.skipped_header_rows,
        "imported_rows": import_summary.imported_rows,
        "term_base_id": str(term_base_response_id),
        "term_base_name": term_base_response_name,
        "source_language": resolved_source_language,
        "target_language": resolved_target_language,
    }


def _run_term_resource_import_task(task_id: str, payload: dict[str, Any]) -> None:
    staging_task_id = str(payload.get("staging_task_id") or task_id)
    set_import_task_status(task_id, "running", progress=5, message="术语库导入开始处理。")
    try:
        raise_if_import_task_canceled(task_id)
        with SessionLocal() as db:
            file_payload = payload["file"]
            filename = file_payload.get("filename") or "uploaded.xlsx"
            extension = str(payload.get("extension") or "")
            term_base_id = UUID(str(payload["term_base_id"]))
            creator_id = UUID(str(payload["creator_id"]))
            skipped_row_indexes = {
                int(item)
                for item in payload.get("skip_duplicate_row_indexes", [])
                if int(item) > 0
            }
            import_batch = create_resource_import_batch(
                db,
                resource_type="term",
                resource_id=term_base_id,
                filename=filename,
                file_path=file_payload["path"],
                file_format=extension,
                source_language=str(payload["source_language"]),
                target_language=str(payload["target_language"]),
                created_by_id=creator_id,
            )
            db.commit()

            def cancel_check() -> bool:
                return import_task_cancel_requested(task_id)

            try:
                set_import_task_status(task_id, "running", progress=20, message=f"正在导入 {filename}。")
                if extension in TBX_EXTENSIONS:
                    import_summary = import_terms_from_tbx_path(
                        db=db,
                        tbx_path=file_payload["path"],
                        filename=filename,
                        term_base_id=term_base_id,
                        source_language=str(payload["source_language"]),
                        target_language=str(payload["target_language"]),
                        creator_id=creator_id,
                        skip_duplicate_row_indexes=skipped_row_indexes,
                        batch_size=_resource_import_batch_size(),
                        cancel_check=cancel_check,
                        import_batch_id=import_batch.id,
                    )
                elif extension in TMX_EXTENSIONS:
                    import_summary = import_terms_from_tmx_path(
                        db=db,
                        tmx_path=file_payload["path"],
                        filename=filename,
                        term_base_id=term_base_id,
                        source_language=str(payload["source_language"]),
                        target_language=str(payload["target_language"]),
                        creator_id=creator_id,
                        skip_duplicate_row_indexes=skipped_row_indexes,
                        batch_size=_resource_import_batch_size(),
                        cancel_check=cancel_check,
                        import_batch_id=import_batch.id,
                    )
                elif extension in XLSX_EXTENSIONS:
                    import_summary = import_terms_from_xlsx_path(
                        db=db,
                        xlsx_path=file_payload["path"],
                        filename=filename,
                        term_base_id=term_base_id,
                        source_language=str(payload["source_language"]),
                        target_language=str(payload["target_language"]),
                        creator_id=creator_id,
                        skip_duplicate_row_indexes=skipped_row_indexes,
                        skip_header=bool(payload.get("skip_header")),
                        batch_size=_resource_import_batch_size(),
                        cancel_check=cancel_check,
                        import_batch_id=import_batch.id,
                    )
                else:
                    import_summary = import_terms_from_xlsx_upload(
                        db=db,
                        raw_bytes=read_import_file_bytes(file_payload),
                        filename=filename,
                        term_base_id=term_base_id,
                        source_language=str(payload["source_language"]),
                        target_language=str(payload["target_language"]),
                        creator_id=creator_id,
                        skip_duplicate_row_indexes=skipped_row_indexes,
                        skip_header=bool(payload.get("skip_header")),
                        batch_size=_resource_import_batch_size(),
                        cancel_check=cancel_check,
                        import_batch_id=import_batch.id,
                    )
            except ImportTaskCanceled:
                db.rollback()
                raise
            except Exception as exc:
                db.rollback()
                raise RuntimeError(f"术语库导入失败：{exc}") from exc
            raise_if_import_task_canceled(task_id)
            db.commit()

            try:
                set_import_task_status(task_id, "running", progress=90, message="正在写入导入通知。")
                raise_if_import_task_canceled(task_id)
                notification_title, notification_body = build_resource_import_notification(
                    resource_label="术语库",
                    resource_name=str(payload["term_base_name"]),
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
                logger.exception("Term base import post-processing failed")

            raise_if_import_task_canceled(task_id)
            result = _build_term_import_result_payload(
                import_summary=import_summary,
                term_base_response_id=term_base_id,
                term_base_response_name=str(payload["term_base_name"]),
                resolved_source_language=str(payload["source_language"]),
                resolved_target_language=str(payload["target_language"]),
            )
            set_import_task_status(
                task_id,
                "completed",
                progress=100,
                message="术语库导入完成。",
                result=result,
            )
    except ImportTaskCanceled:
        set_import_task_status(task_id, "canceled", progress=100, message="术语库导入已取消。")
    except Exception as exc:
        logger.exception("term resource import task failed task_id=%s", task_id)
        set_import_task_status(
            task_id,
            "failed",
            progress=100,
            message="术语库导入失败。",
            error=str(exc),
        )
    finally:
        cleanup_import_task_staging(staging_task_id)


@router.post("/term-bases/import-xlsx")
@router.post("/term-bases/import", include_in_schema=False)
async def import_term_base_xlsx(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    term_base_id: UUID | None = Form(default=None),
    source_language: str = Form(...),
    target_language: str = Form(...),
    skip_duplicate_row_indexes: str = Form(default="[]"),
    skip_header: bool = Form(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if term_base_id is None:
        raise HTTPException(status_code=400, detail="请先选择要导入的术语库。")

    extension = _validate_term_import_upload(file)
    task_id, staged_file = await asyncio.to_thread(_stage_resource_upload_file, file)
    try:
        term_base = _get_term_base_or_404(db, term_base_id)
        resolved_source_language, resolved_target_language = _resolve_term_base_language_pair(
            term_base,
            source_language,
            target_language,
        )
        db.commit()
        skipped_row_indexes = _parse_import_row_indexes(skip_duplicate_row_indexes)
        payload = {
            "kind": "term_resource_import",
            "staging_task_id": task_id,
            "file": staged_file,
            "extension": extension,
            "term_base_id": str(term_base.id),
            "term_base_name": term_base.name,
            "source_language": resolved_source_language,
            "target_language": resolved_target_language,
            "skip_duplicate_row_indexes": sorted(skipped_row_indexes),
            "skip_header": bool(skip_header),
            "creator_id": str(current_user.id),
        }
        set_import_task_status(task_id, "queued", progress=0, message="术语库导入任务已进入队列。")
        await _queue_term_resource_import_task(task_id, payload)
        return JSONResponse(
            status_code=202,
            content={
                "task_id": task_id,
                "status": "queued",
                "progress": 0,
                "message": "术语库导入任务已进入队列。",
            },
        )
    except Exception:
        cleanup_import_task_staging(task_id)
        raise


@router.post("/term-bases/{term_base_id}/entries/conflicts")
def check_term_base_entry_conflicts(
    term_base_id: UUID,
    payload: TermEntryConflictPayload,
    db: Session = Depends(get_db),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    items = build_term_entry_conflict_items(
        db=db,
        term_base=term_base,
        entries=[entry.model_dump() for entry in payload.entries],
    )
    return {
        "term_base_id": str(term_base.id),
        "items": items,
        "conflict_count": sum(1 for item in items if item["has_conflict"]),
    }


@router.post("/term-bases/{term_base_id}/entries/batch")
def batch_save_term_base_entries(
    term_base_id: UUID,
    payload: TermEntryBatchPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    return save_term_entries_batch(
        db=db,
        term_base=term_base,
        entries=[entry.model_dump() for entry in payload.entries],
        current_user=current_user,
    )


@router.get("/term-bases/{term_base_id}/entries")
def list_term_base_entries(
    term_base_id: UUID,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    case_sensitive: bool = False,
    sort_by: str | None = None,
    sort_order: str | None = "desc",
    db: Session = Depends(get_db),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    safe_skip = max(skip, 0)
    safe_limit = min(max(limit, 1), 200)
    query = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
    )
    normalized_search = normalize_text(search or "")
    if normalized_search:
        like_pattern = f"%{normalized_search}%"
        if case_sensitive:
            query = query.filter(
                or_(
                    TermEntry.source_text.like(like_pattern),
                    TermEntry.target_text.like(like_pattern),
                )
            )
        else:
            query = query.filter(
                or_(
                    TermEntry.source_text.ilike(like_pattern),
                    TermEntry.target_text.ilike(like_pattern),
                )
            )

    total = query.count()
    rows = (
        _apply_term_entry_sort(query, sort_by, sort_order)
        .offset(safe_skip)
        .limit(safe_limit)
        .all()
    )
    return {
        "items": [_serialize_term_entry(row) for row in rows],
        "total": total,
        "skip": safe_skip,
        "limit": safe_limit,
    }


@router.get("/term-bases/{term_base_id}/export-xlsx")
def export_term_base_entries_xlsx(
    term_base_id: UUID,
    db: Session = Depends(get_db),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    rows = (
        db.query(TermEntry)
        .filter(TermEntry.term_base_id == term_base.id)
        .order_by(TermEntry.updated_at.desc(), TermEntry.created_at.desc())
        .all()
    )
    xlsx_bytes = build_tabular_xlsx(
        sheet_title=term_base.name,
        headers=["术语原文", "术语译文", "源语言", "目标语言", "创建人", "创建时间", "最后修改人", "更新时间"],
        rows=[
            [
                entry.source_text,
                entry.target_text,
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
    return build_xlsx_download_response(f"{term_base.name}-term-base.xlsx", xlsx_bytes)


@router.post("/term-bases/{term_base_id}/exports")
def queue_term_base_export(
    term_base_id: UUID,
    format: ResourceExportFormat = Query(default="xlsx"),
    db: Session = Depends(get_db),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    return JSONResponse(
        status_code=202,
        content=queue_resource_export(
            resource_type="term",
            resource_id=term_base.id,
            export_format=format,
        ),
    )


@router.get("/term-bases/export-tasks/{task_id}")
def get_term_base_export_task(task_id: str):
    return ensure_export_task_status(task_id, expected_resource_type="term")


@router.post("/term-bases/export-tasks/{task_id}/cancel")
def cancel_term_base_export_task(task_id: str):
    return cancel_resource_export_task(task_id, expected_resource_type="term")


@router.get("/term-bases/export-tasks/{task_id}/download")
def download_term_base_export_task(task_id: str):
    return build_resource_export_download_response(task_id, expected_resource_type="term")


@router.post("/term-bases/{term_base_id}/entries")
def create_term_base_entry(
    term_base_id: UUID,
    payload: TermEntryUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    term_base = _get_term_base_or_404(db, term_base_id)
    _require_term_entry_create_access(
        db,
        term_base.id,
        payload.file_record_id,
        current_user,
    )
    source_text = normalize_text(payload.source_text)
    target_text = normalize_text(payload.target_text)
    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="术语原文和译文不能为空。")

    source_normalized = normalize_match_text(source_text) or source_text
    duplicate = (
        db.query(TermEntry)
        .filter(
            TermEntry.term_base_id == term_base.id,
            TermEntry.source_language == term_base.source_language,
            TermEntry.target_language == term_base.target_language,
            or_(
                TermEntry.source_normalized == source_normalized,
                TermEntry.source_text == source_text,
            ),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="当前术语库中已存在相同原文的术语条目。")

    entry = TermEntry(
        term_base_id=term_base.id,
        source_text=source_text,
        target_text=target_text,
        source_normalized=source_normalized,
        source_language=term_base.source_language,
        target_language=term_base.target_language,
        creator_id=current_user.id,
        last_modified_by_id=current_user.id,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _serialize_term_entry(entry)


@router.put("/term-entries/{entry_id}")
def update_term_entry(
    entry_id: UUID,
    payload: TermEntryUpdatePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry = db.query(TermEntry).filter(TermEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="术语条目不存在。")

    _require_term_entry_owner_or_admin(entry, current_user)

    source_text = normalize_text(payload.source_text)
    target_text = normalize_text(payload.target_text)
    if not source_text or not target_text:
        raise HTTPException(status_code=400, detail="术语原文和译文不能为空。")

    term_base = _get_term_base_or_404(db, entry.term_base_id)
    source_language = term_base.source_language or entry.source_language
    target_language = term_base.target_language or entry.target_language
    source_normalized = normalize_match_text(source_text) or source_text

    duplicate = (
        db.query(TermEntry)
        .filter(
            TermEntry.id != entry.id,
            TermEntry.term_base_id == entry.term_base_id,
            TermEntry.source_language == source_language,
            TermEntry.target_language == target_language,
            or_(
                TermEntry.source_normalized == source_normalized,
                TermEntry.source_text == source_text,
            ),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="当前术语库中已存在相同原文的术语条目。")

    entry.source_text = source_text
    entry.target_text = target_text
    entry.source_normalized = source_normalized
    entry.source_language = source_language
    entry.target_language = target_language
    if entry.creator_id is None:
        entry.creator_id = current_user.id
    entry.last_modified_by_id = current_user.id
    db.commit()
    db.refresh(entry)
    return _serialize_term_entry(entry)


@router.delete("/term-entries/{entry_id}")
def delete_term_entry(
    entry_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    entry = db.query(TermEntry).filter(TermEntry.id == entry_id).first()
    if entry is None:
        raise HTTPException(status_code=404, detail="术语条目不存在。")

    _require_term_entry_owner_or_admin(entry, current_user)

    db.delete(entry)
    db.commit()
    return {"message": "术语条目已删除。"}
