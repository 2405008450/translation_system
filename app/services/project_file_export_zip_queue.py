from __future__ import annotations

import logging
import time
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import FileRecord, Project
from app.services.cache import delete as cache_delete
from app.services.cache import get_json as cache_get_json
from app.services.cache import set_json as cache_set_json
from app.services.file_export_queue import (
    FILE_EXPORT_TASK_TTL_SECONDS,
    build_file_record_exported_file,
)


logger = logging.getLogger(__name__)

PROJECT_FILE_ZIP_EXPORT_MEDIA_TYPE = "application/zip"
PROJECT_FILE_ZIP_EXPORT_TASK_TTL_SECONDS = FILE_EXPORT_TASK_TTL_SECONDS

_PROJECT_FILE_ZIP_EXPORT_EXECUTOR = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="project-file-zip-export",
)


def queue_project_file_zip_export(
    *,
    project_id: UUID,
    project_name: str,
    file_ids: list[UUID],
    requested_by_id: UUID | None = None,
    requested_by_name: str | None = None,
) -> dict[str, Any]:
    normalized_file_ids = [str(file_id) for file_id in file_ids]
    active_key = _project_file_zip_export_active_cache_key(project_id, requested_by_id)
    active_pointer = cache_get_json(active_key)
    if isinstance(active_pointer, dict):
        active_task_id = str(active_pointer.get("task_id") or "")
        active_task = get_project_file_zip_export_task_status(active_task_id) if active_task_id else None
        if (
            active_task
            and active_task.get("status") in {"queued", "running"}
            and active_task.get("file_ids") == normalized_file_ids
        ):
            logger.info(
                "reuse active project zip export task task_id=%s project_id=%s requested_by_id=%s",
                active_task_id,
                project_id,
                requested_by_id,
            )
            return active_task

    task_id = str(uuid4())
    now = _local_now()
    filename = build_project_file_zip_filename(project_name)
    payload: dict[str, Any] = {
        "task_id": task_id,
        "project_id": str(project_id),
        "file_ids": normalized_file_ids,
        "requested_by_id": str(requested_by_id) if requested_by_id else None,
        "requested_by_name": requested_by_name or None,
        "requested_file_count": len(normalized_file_ids),
        "status": "queued",
        "progress": 0,
        "message": "压缩包导出任务已进入队列。",
        "filename": filename,
        "size_bytes": None,
        "error": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=PROJECT_FILE_ZIP_EXPORT_TASK_TTL_SECONDS)).isoformat(),
    }
    _set_project_file_zip_export_task_status(
        task_id,
        "queued",
        progress=0,
        message="压缩包导出任务已进入队列。",
        payload=payload,
    )
    cache_set_json(
        active_key,
        {"task_id": task_id},
        ttl_seconds=PROJECT_FILE_ZIP_EXPORT_TASK_TTL_SECONDS,
    )
    logger.info(
        "queue project zip export task_id=%s project_id=%s files=%d requested_by_id=%s requested_by_name=%s",
        task_id,
        project_id,
        len(normalized_file_ids),
        requested_by_id,
        requested_by_name,
    )
    future = _PROJECT_FILE_ZIP_EXPORT_EXECUTOR.submit(
        _run_project_file_zip_export_task,
        task_id,
        str(project_id),
        [str(file_id) for file_id in file_ids],
        filename,
    )
    future.add_done_callback(_log_project_file_zip_export_task_failure)
    return get_project_file_zip_export_task_status(task_id) or payload


def get_project_file_zip_export_task_status(task_id: str) -> dict[str, Any] | None:
    payload = cache_get_json(_project_file_zip_export_task_cache_key(task_id))
    return payload if isinstance(payload, dict) else None


def ensure_project_file_zip_export_task_status(task_id: str) -> dict[str, Any]:
    status = get_project_file_zip_export_task_status(task_id)
    if not status:
        raise HTTPException(status_code=404, detail="压缩包导出任务不存在。")
    return status


def build_project_file_zip_export_download_response(task_id: str) -> FileResponse:
    status = ensure_project_file_zip_export_task_status(task_id)
    if status.get("status") != "completed":
        raise HTTPException(status_code=409, detail="压缩包导出任务尚未完成。")

    result = status.get("result")
    if not isinstance(result, dict):
        raise HTTPException(status_code=404, detail="压缩包文件不存在。")

    file_path = Path(str(result.get("file_path") or ""))
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="压缩包文件不存在或已过期。")

    filename = str(result.get("filename") or status.get("filename") or file_path.name)
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii").strip() or file_path.name
    ascii_filename = ascii_filename.replace('"', "")
    quoted_filename = quote(filename)
    return FileResponse(
        file_path,
        media_type=str(result.get("media_type") or PROJECT_FILE_ZIP_EXPORT_MEDIA_TYPE),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{quoted_filename}"
            ),
            "X-Accel-Buffering": "no",
        },
    )


def build_project_file_zip_filename(project_name: str | None) -> str:
    name = " ".join((project_name or "").strip().split()) or "项目"
    for char in '<>:"/\\|?*':
        name = name.replace(char, "_")
    return f"{name}-目标文件.zip"


def _run_project_file_zip_export_task(
    task_id: str,
    project_id: str,
    file_ids: list[str],
    filename: str,
) -> None:
    output_path: Path | None = None
    try:
        _set_project_file_zip_export_task_status(
            task_id,
            "running",
            progress=5,
            message="压缩包导出任务开始处理。",
        )

        output_dir = _ensure_export_dir()
        _cleanup_expired_export_files(output_dir)
        output_path = output_dir / f"{task_id}.zip"

        with SessionLocal(expire_on_commit=False) as db:
            project_uuid = UUID(project_id)
            file_uuid_order = [UUID(file_id) for file_id in file_ids]
            project = db.query(Project).filter(Project.id == project_uuid).first()
            if project is None:
                raise ValueError("项目不存在。")

            ordered_files = _load_ordered_project_files(
                db,
                project_id=project_uuid,
                file_ids=file_uuid_order,
            )
            if len(ordered_files) < 2:
                raise ValueError("请至少选择两个文件导出为压缩包。")

            # 初始查询只负责校验和排序，立即关闭会话，不跨文件导出持有事务。
            ordered_file_refs = [
                (file_record.id, file_record.filename)
                for file_record in ordered_files
            ]

        used_names: set[str] = set()
        total = len(ordered_file_refs)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for index, (file_record_id, source_filename) in enumerate(ordered_file_refs, start=1):
                _set_project_file_zip_export_task_status(
                    task_id,
                    "running",
                    progress=10 + int(((index - 1) / total) * 80),
                    message=f"正在导出 {index}/{total}：{source_filename}",
                )
                # 每个文件使用独立短会话；CAD 渲染开始前会主动释放本次只读事务。
                with SessionLocal(expire_on_commit=False) as db:
                    file_record = (
                        db.query(FileRecord)
                        .filter(FileRecord.id == file_record_id)
                        .first()
                    )
                    if file_record is None:
                        raise ValueError(f"文件不存在：{source_filename}")
                    exported_file = build_file_record_exported_file(
                        db,
                        file_record,
                        "original",
                        release_transaction_before_render=True,
                    )
                archive_name = _deduplicate_archive_name(exported_file.filename, used_names)
                archive.writestr(archive_name, exported_file.content)
                _set_project_file_zip_export_task_status(
                    task_id,
                    "running",
                    progress=10 + int((index / total) * 80),
                    message=f"已写入 {index}/{total} 个目标文件。",
                )

        size_bytes = output_path.stat().st_size
        _set_project_file_zip_export_task_status(
            task_id,
            "completed",
            progress=100,
            message="压缩包导出完成。",
            result={
                "file_path": str(output_path),
                "filename": filename,
                "media_type": PROJECT_FILE_ZIP_EXPORT_MEDIA_TYPE,
                "size_bytes": size_bytes,
            },
            filename=filename,
            size_bytes=size_bytes,
        )
        _clear_project_file_zip_export_active_pointer(task_id)
    except Exception as exc:
        logger.exception("project file zip export task failed task_id=%s", task_id)
        if output_path is not None:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("skip cleanup for failed zip export path=%s", output_path, exc_info=True)
        _set_project_file_zip_export_task_status(
            task_id,
            "failed",
            progress=100,
            message="压缩包导出失败。",
            error=str(exc),
        )
        _clear_project_file_zip_export_active_pointer(task_id)


def _load_ordered_project_files(
    db: Session,
    *,
    project_id: UUID,
    file_ids: list[UUID],
) -> list[FileRecord]:
    files = (
        db.query(FileRecord)
        .filter(FileRecord.project_id == project_id, FileRecord.id.in_(file_ids))
        .all()
    )
    file_by_id = {file_record.id: file_record for file_record in files}
    ordered_files: list[FileRecord] = []
    for file_id in file_ids:
        file_record = file_by_id.get(file_id)
        if file_record is None:
            raise ValueError("部分文件不存在或不属于当前项目。")
        ordered_files.append(file_record)
    return ordered_files


def _deduplicate_archive_name(filename: str | None, used_names: set[str]) -> str:
    safe_name = _safe_archive_name(filename)
    suffix = Path(safe_name).suffix
    stem = safe_name[: -len(suffix)] if suffix else safe_name
    stem = stem or "export"
    candidate = f"{stem}{suffix}"
    index = 2
    while candidate.casefold() in used_names:
        candidate = f"{stem} ({index}){suffix}"
        index += 1
    used_names.add(candidate.casefold())
    return candidate


def _safe_archive_name(filename: str | None) -> str:
    name = str(filename or "").strip().replace("\\", "/")
    name = Path(name).name
    return name or "export.bin"


def _set_project_file_zip_export_task_status(
    task_id: str,
    status: str,
    *,
    progress: int,
    message: str,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    filename: str | None = None,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    task_payload = payload or get_project_file_zip_export_task_status(task_id) or {"task_id": task_id}
    task_payload["status"] = status
    task_payload["progress"] = max(0, min(100, int(progress)))
    task_payload["message"] = message
    task_payload["updated_at"] = _local_now().isoformat()
    if error is not None:
        task_payload["error"] = error
    elif status in {"queued", "running", "completed"}:
        task_payload["error"] = None
    if result is not None:
        task_payload["result"] = result
    if filename is not None:
        task_payload["filename"] = filename
    if size_bytes is not None:
        task_payload["size_bytes"] = size_bytes
    cache_set_json(
        _project_file_zip_export_task_cache_key(task_id),
        task_payload,
        ttl_seconds=PROJECT_FILE_ZIP_EXPORT_TASK_TTL_SECONDS,
    )
    return task_payload


def _ensure_export_dir() -> Path:
    output_dir = Path(get_settings().export_task_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _cleanup_expired_export_files(output_dir: Path) -> None:
    cutoff = time.time() - PROJECT_FILE_ZIP_EXPORT_TASK_TTL_SECONDS
    for path in output_dir.glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            logger.debug("skip cleanup for project zip export file path=%s", path, exc_info=True)


def _project_file_zip_export_task_cache_key(task_id: str) -> str:
    return f"project-file-zip-export:{task_id}"


def _project_file_zip_export_active_cache_key(
    project_id: UUID,
    requested_by_id: UUID | None,
) -> str:
    requester = str(requested_by_id) if requested_by_id else "anonymous"
    return f"project-file-zip-export-active:{requester}:{project_id}"


def _clear_project_file_zip_export_active_pointer(task_id: str) -> None:
    task = get_project_file_zip_export_task_status(task_id)
    if not task:
        return
    try:
        project_id = UUID(str(task.get("project_id") or ""))
        requested_by_raw = task.get("requested_by_id")
        requested_by_id = UUID(str(requested_by_raw)) if requested_by_raw else None
    except ValueError:
        return
    active_key = _project_file_zip_export_active_cache_key(project_id, requested_by_id)
    pointer = cache_get_json(active_key)
    if isinstance(pointer, dict) and str(pointer.get("task_id") or "") == task_id:
        cache_delete(active_key)


def _local_now() -> datetime:
    return datetime.now()


def _log_project_file_zip_export_task_failure(future: Future) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("project file zip export worker crashed")
