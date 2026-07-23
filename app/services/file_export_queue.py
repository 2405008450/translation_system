from __future__ import annotations

import logging
import mimetypes
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import FileExportTask, FileRecord, User
from app.services.adapters import export_file
from app.services.task_file_service import (
    BILINGUAL_DOCX_LAYOUT_EXPORT_ORDERS,
    BILINGUAL_PPTX_EXPORT_TYPE,
    DOCUMENT_PARSE_MODE_FULL,
    can_export_task_file,
    export_bilingual_pptx_task_file,
    export_bilingual_task_docx_with_layout,
    export_bilingual_xlsx_task_file,
    export_translated_task_file,
    get_task_file_extension,
    normalize_document_parse_options,
)
from app.services.file_record_service import (
    get_file_record as get_file_record_model,
    get_file_record_source_filename,
    list_segments_for_file_record,
    load_file_record_source,
)
from app.services.language_pairs import require_language_pair


logger = logging.getLogger(__name__)

FILE_EXPORT_TASK_TTL_SECONDS = 24 * 60 * 60
FILE_EXPORT_POLL_INTERVAL_SECONDS = 0.3
FILE_EXPORT_WAIT_TIMEOUT_SECONDS = 30 * 60
LANGUAGE_TAGGED_EXPORT_TYPES = {"tmx", "xliff", "xliff2"}

_FILE_EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="file-export")
_SCHEMA_READY = False
_SCHEMA_LOCK = Lock()

# 导出样式设置以任务 id 为键暂存在进程内（排队与执行在同一进程内的线程池中完成），
# 避免为一次性的导出参数新增数据库列。任务执行结束后即清理。
_STYLE_SETTINGS_BY_TASK: dict[str, dict[str, Any]] = {}
_STYLE_SETTINGS_LOCK = Lock()


def _store_style_settings(task_id: UUID, style_settings: dict[str, Any] | None) -> None:
    if not style_settings:
        return
    with _STYLE_SETTINGS_LOCK:
        _STYLE_SETTINGS_BY_TASK[str(task_id)] = style_settings


def _pop_style_settings(task_id: UUID) -> dict[str, Any] | None:
    with _STYLE_SETTINGS_LOCK:
        return _STYLE_SETTINGS_BY_TASK.pop(str(task_id), None)

_FILE_EXPORT_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS file_export_tasks (
        id UUID PRIMARY KEY DEFAULT (
            lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
            lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
            '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
            substr('89ab', floor(random() * 4)::int + 1, 1) ||
            substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
            lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
        )::uuid,
        file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
        export_type VARCHAR(40) NOT NULL DEFAULT 'original',
        status VARCHAR(20) NOT NULL DEFAULT 'queued',
        progress INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '',
        result_path TEXT,
        filename VARCHAR(255),
        media_type VARCHAR(120),
        size_bytes INTEGER,
        error TEXT,
        created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMP NOT NULL
    )
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS export_type VARCHAR(40) NOT NULL DEFAULT 'original'
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'queued'
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS progress INTEGER NOT NULL DEFAULT 0
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT ''
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS result_path TEXT
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS filename VARCHAR(255)
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS media_type VARCHAR(120)
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS size_bytes INTEGER
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS error TEXT
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS created_by_id UUID REFERENCES users(id) ON DELETE SET NULL
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW()
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    """,
    """
    ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '24 hours')
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_file_export_tasks_file_record_type
    ON file_export_tasks (file_record_id, export_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_file_export_tasks_status
    ON file_export_tasks (status)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_file_export_tasks_expires_at
    ON file_export_tasks (expires_at)
    """,
]

_FILE_EXPORT_REQUIRED_COLUMNS = {
    "id",
    "file_record_id",
    "export_type",
    "status",
    "progress",
    "message",
    "result_path",
    "filename",
    "media_type",
    "size_bytes",
    "error",
    "created_by_id",
    "created_at",
    "updated_at",
    "expires_at",
}
_FILE_EXPORT_REQUIRED_INDEXES = {
    "ix_file_export_tasks_file_record_type",
    "ix_file_export_tasks_status",
    "ix_file_export_tasks_expires_at",
}


def local_now() -> datetime:
    return datetime.now()


def utcnow() -> datetime:
    return local_now()


def queue_file_export(
    db: Session,
    *,
    file_record_id: UUID,
    export_type: str,
    current_user: User | None,
    style_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_file_export_tasks_schema()
    export_type = normalize_file_export_type(export_type)
    now = local_now()
    expires_at = now + timedelta(seconds=FILE_EXPORT_TASK_TTL_SECONDS)

    file_record = (
        db.query(FileRecord)
        .filter(FileRecord.id == file_record_id)
        .with_for_update()
        .first()
    )
    if file_record is None:
        raise HTTPException(status_code=404, detail="File record not found.")

    task = FileExportTask(
        file_record_id=file_record_id,
        export_type=export_type,
        status="queued",
        progress=0,
        message="导出任务已进入队列。",
        created_by_id=current_user.id if current_user else None,
        expires_at=expires_at,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    _store_style_settings(task.id, style_settings)

    _cleanup_expired_file_exports(db)
    future = _FILE_EXPORT_EXECUTOR.submit(_run_file_export_task, task.id)
    future.add_done_callback(_log_file_export_task_failure)
    return serialize_file_export_task(task)


def get_file_export_task(db: Session, task_id: UUID) -> FileExportTask:
    ensure_file_export_tasks_schema()
    task = db.query(FileExportTask).filter(FileExportTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="导出任务不存在。")
    return task


def wait_for_file_export_task(task_id: UUID, timeout_seconds: int = FILE_EXPORT_WAIT_TIMEOUT_SECONDS) -> FileExportTask:
    ensure_file_export_tasks_schema()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            task = get_file_export_task(db, task_id)
            if task.status == "completed":
                return task
            if task.status == "failed":
                raise HTTPException(status_code=500, detail=task.error or task.message or "导出失败。")
        time.sleep(FILE_EXPORT_POLL_INTERVAL_SECONDS)
    raise HTTPException(status_code=504, detail="导出任务处理超时，请稍后重试。")


def build_file_export_download_response(task: FileExportTask) -> FileResponse:
    if task.status != "completed":
        raise HTTPException(status_code=409, detail="导出任务尚未完成。")
    if not task.result_path:
        raise HTTPException(status_code=404, detail="导出文件不存在。")

    file_path = Path(task.result_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期。")

    filename = task.filename or file_path.name
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii").strip() or file_path.name
    ascii_filename = ascii_filename.replace('"', "")
    quoted_filename = quote(filename)
    return FileResponse(
        file_path,
        media_type=task.media_type or "application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{quoted_filename}"
            )
        },
    )


def serialize_file_export_task(task: FileExportTask) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "file_record_id": str(task.file_record_id),
        "export_type": task.export_type,
        "status": task.status,
        "progress": task.progress,
        "message": task.message,
        "filename": task.filename,
        "size_bytes": task.size_bytes,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "expires_at": task.expires_at.isoformat() if task.expires_at else None,
    }


def ensure_file_export_tasks_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        with engine.connect() as connection:
            missing_items = _collect_file_export_schema_missing_items(connection)
            if not missing_items:
                _SCHEMA_READY = True
                return

        try:
            with engine.begin() as connection:
                for statement in _FILE_EXPORT_SCHEMA_STATEMENTS:
                    connection.execute(text(statement))
        except ProgrammingError as exc:
            missing_text = ", ".join(missing_items)
            raise RuntimeError(
                "数据库账号缺少导出任务表结构升级权限，请使用有权限的账号执行 "
                "scripts/add_file_export_tasks.sql 后再导出。"
                f" 当前缺失项: {missing_text}"
            ) from exc
        _SCHEMA_READY = True


def _collect_file_export_schema_missing_items(connection) -> list[str]:
    inspector = inspect(connection)
    table_name = "file_export_tasks"
    if not inspector.has_table(table_name):
        return [table_name]

    existing_columns = {
        column["name"]
        for column in inspector.get_columns(table_name)
    }
    missing_items = [
        f"{table_name}.{column_name}"
        for column_name in sorted(_FILE_EXPORT_REQUIRED_COLUMNS - existing_columns)
    ]

    existing_indexes = {
        index["name"]
        for index in inspector.get_indexes(table_name)
    }
    missing_items.extend(
        f"{table_name}.{index_name}"
        for index_name in sorted(_FILE_EXPORT_REQUIRED_INDEXES - existing_indexes)
    )
    return missing_items


def normalize_file_export_type(export_type: str | None) -> str:
    value = (export_type or "original").strip()
    return value or "original"


def _resolve_file_record_export_language_pair(file_record: FileRecord) -> tuple[str, str]:
    source_language = getattr(file_record, "source_language", None)
    target_language = getattr(file_record, "target_language", None)
    collection = getattr(file_record, "collection", None)

    if (not source_language or not target_language) and collection is not None:
        source_language = source_language or getattr(collection, "source_language", None)
        target_language = target_language or getattr(collection, "target_language", None)

    return require_language_pair(source_language, target_language)


def _run_file_export_task(task_id: UUID) -> None:
    style_settings = _pop_style_settings(task_id)
    try:
        with SessionLocal() as db:
            task = get_file_export_task(db, task_id)
            _set_file_export_task_status(db, task, "running", progress=5, message="导出任务开始处理。")

            file_record = get_file_record_model(db, task.file_record_id)
            if file_record is None:
                raise ValueError("File record not found.")

            _set_file_export_task_status(db, task, "running", progress=20, message="正在读取文件和句段。")
            report_context = {
                "file_record_id": task.file_record_id,
                "export_task_id": task.id,
                "created_by_id": task.created_by_id,
                "export_type": task.export_type,
                "filename": file_record.filename,
            }
            exported_file = build_file_record_exported_file(
                db,
                file_record,
                task.export_type,
                style_settings=style_settings,
                report_context=report_context,
            )

            output_dir = _ensure_export_dir()
            _cleanup_expired_export_files(output_dir)
            suffix = Path(exported_file.filename).suffix or ".bin"
            output_path = output_dir / f"{task.id}{suffix}"
            output_path.write_bytes(exported_file.content)

            task.result_path = str(output_path)
            task.filename = exported_file.filename
            task.media_type = exported_file.media_type
            task.size_bytes = output_path.stat().st_size
            _set_file_export_task_status(db, task, "completed", progress=100, message="导出完成。")
    except Exception as exc:
        logger.exception("file export task failed task_id=%s", task_id)
        with SessionLocal() as db:
            task = db.query(FileExportTask).filter(FileExportTask.id == task_id).first()
            if task is not None:
                task.error = str(exc)
                _set_file_export_task_status(db, task, "failed", progress=100, message="导出失败。")


def build_file_record_exported_file(
    db: Session,
    file_record: FileRecord,
    export_type: str,
    style_settings: dict[str, Any] | None = None,
    report_context: dict[str, Any] | None = None,
):
    raw_bytes = load_file_record_source(file_record)
    source_filename = get_file_record_source_filename(file_record)

    if export_type == "source":
        if raw_bytes is None:
            raise ValueError("The source file is unavailable.")
        return _GenericExportedFile(
            content=raw_bytes,
            media_type=mimetypes.guess_type(source_filename)[0] or "application/octet-stream",
            filename=source_filename,
        )

    segments = list_segments_for_file_record(db, file_record.id)
    document_parse_mode = getattr(file_record, "document_parse_mode", DOCUMENT_PARSE_MODE_FULL)
    document_parse_options = normalize_document_parse_options(
        getattr(file_record, "document_parse_options", None),
        document_parse_mode,
    )

    if export_type == "original":
        if not can_export_task_file(source_filename, has_source_file=raw_bytes is not None):
            raise ValueError("Current file format does not support original export yet.")
        return _apply_style_settings_to_export(
            export_translated_task_file(
                raw_bytes=raw_bytes,
                filename=source_filename,
                segments=segments,
                document_parse_mode=document_parse_mode,
                document_parse_options=document_parse_options,
                target_language=getattr(file_record, "target_language", None),
            ),
            style_settings,
            report_context,
        )

    if export_type in BILINGUAL_DOCX_LAYOUT_EXPORT_ORDERS:
        if get_task_file_extension(source_filename) != ".docx":
            raise ValueError("Only DOCX source files support layout-preserving bilingual Word export.")
        return _apply_style_settings_to_export(
            export_bilingual_task_docx_with_layout(
                raw_bytes=raw_bytes,
                filename=source_filename,
                segments=segments,
                order=BILINGUAL_DOCX_LAYOUT_EXPORT_ORDERS[export_type],
                document_parse_mode=document_parse_mode,
                document_parse_options=document_parse_options,
                target_language=getattr(file_record, "target_language", None),
            ),
            style_settings,
            report_context,
        )

    if export_type == "bilingual_excel_original":
        if get_task_file_extension(source_filename) != ".xlsx":
            raise ValueError("Only XLSX source files support original-format bilingual Excel export.")
        return export_bilingual_xlsx_task_file(
            raw_bytes=raw_bytes,
            filename=source_filename,
            segments=segments,
            document_parse_options=document_parse_options,
        )

    if export_type == BILINGUAL_PPTX_EXPORT_TYPE:
        if get_task_file_extension(source_filename) != ".pptx":
            raise ValueError("Only PPTX source files support original-format bilingual PPTX export.")
        return _apply_style_settings_to_export(
            export_bilingual_pptx_task_file(
                raw_bytes=raw_bytes,
                filename=source_filename,
                segments=segments,
                document_parse_options=document_parse_options,
            ),
            style_settings,
            report_context,
        )

    segment_dicts = [
        {
            "segment_id": seg.sentence_id,
            "source_text": seg.source_text,
            "target_text": seg.target_text,
            "status": seg.status,
            "matched_source_text": seg.matched_source_text,
            "sequence_index": getattr(seg, "sequence_index", None),
        }
        for seg in segments
    ]
    export_kwargs = {
        "export_type": export_type,
        "segments": segment_dicts,
        "filename": file_record.filename,
        "original_bytes": raw_bytes,
    }
    if export_type in LANGUAGE_TAGGED_EXPORT_TYPES:
        source_language, target_language = _resolve_file_record_export_language_pair(file_record)
        export_kwargs["source_lang"] = source_language
        export_kwargs["target_lang"] = target_language

    exported_bytes, media_type, export_filename = export_file(**export_kwargs)
    return _apply_style_settings_to_export(
        _GenericExportedFile(
            content=exported_bytes,
            media_type=media_type,
            filename=export_filename,
        ),
        style_settings,
        report_context,
    )


def _apply_style_settings_to_export(
    exported_file,
    style_settings: dict[str, Any] | None,
    report_context: dict[str, Any] | None = None,
):
    """
    按导出结果扩展名分派样式后处理：.docx 走文字样式调整、.pptx 走版式优化。
    其余格式或未启用设置时原样返回；调整失败会在内部记录日志并回退到原始内容，
    绝不影响导出成功。
    """
    if not style_settings:
        return exported_file
    filename = getattr(exported_file, "filename", "") or ""
    lowered = filename.lower()
    if lowered.endswith(".docx"):
        return _apply_docx_style_settings(exported_file, style_settings, filename)
    if lowered.endswith(".pptx"):
        return _apply_pptx_layout_settings(exported_file, style_settings, filename, report_context)
    return exported_file


def _apply_docx_style_settings(exported_file, style_settings: dict[str, Any], filename: str):
    from app.services.export_settings.style_export_integration import (
        apply_export_style_settings,
        style_settings_enabled,
    )

    if not style_settings_enabled(style_settings):
        return exported_file

    adjusted = apply_export_style_settings(exported_file.content, style_settings)
    if adjusted is exported_file.content or adjusted == exported_file.content:
        return exported_file
    return _GenericExportedFile(
        content=adjusted,
        media_type=getattr(exported_file, "media_type", None)
        or "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )


def _apply_pptx_layout_settings(
    exported_file,
    style_settings: dict[str, Any],
    filename: str,
    report_context: dict[str, Any] | None = None,
):
    from app.services.export_settings.pptx_layout import (
        apply_pptx_layout_settings,
        pptx_layout_settings_enabled,
    )

    if not pptx_layout_settings_enabled(style_settings):
        return exported_file

    adjusted = apply_pptx_layout_settings(
        exported_file.content, style_settings, report_context=report_context
    )
    if adjusted is exported_file.content or adjusted == exported_file.content:
        return exported_file
    return _GenericExportedFile(
        content=adjusted,
        media_type=getattr(exported_file, "media_type", None)
        or "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )


class _GenericExportedFile:
    def __init__(self, *, content: bytes, media_type: str, filename: str) -> None:
        self.content = content
        self.media_type = media_type
        self.filename = filename


def _set_file_export_task_status(
    db: Session,
    task: FileExportTask,
    status: str,
    *,
    progress: int,
    message: str,
) -> None:
    task.status = status
    task.progress = max(0, min(100, int(progress)))
    task.message = message
    task.updated_at = local_now()
    db.commit()
    db.refresh(task)


def _ensure_export_dir() -> Path:
    output_dir = Path(get_settings().export_task_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _cleanup_expired_file_exports(db: Session) -> None:
    now = local_now()
    expired_tasks = db.query(FileExportTask).filter(FileExportTask.expires_at <= now).all()
    for task in expired_tasks:
        if task.result_path:
            try:
                Path(task.result_path).unlink(missing_ok=True)
            except OSError:
                logger.debug("skip cleanup for file export path=%s", task.result_path, exc_info=True)
        db.delete(task)
    db.commit()


def _cleanup_expired_export_files(output_dir: Path) -> None:
    cutoff = time.time() - FILE_EXPORT_TASK_TTL_SECONDS
    for path in output_dir.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            logger.debug("skip cleanup for export file path=%s", path, exc_info=True)


def _log_file_export_task_failure(future: Future) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("file export worker crashed")
