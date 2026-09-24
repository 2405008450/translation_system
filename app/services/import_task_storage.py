from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable

from app.config import get_settings

logger = logging.getLogger(__name__)
IMPORT_STAGING_COPY_CHUNK_SIZE = 1024 * 1024


def get_import_task_root() -> Path:
    return Path(get_settings().import_task_dir)


def _import_task_staging_ttl_seconds() -> int:
    return max(int(get_settings().import_task_staging_ttl_seconds), 60)


def ensure_import_task_root() -> Path:
    root = get_import_task_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_import_task_staging_dir(task_id: str) -> Path:
    return ensure_import_task_root() / task_id


def _sanitize_staging_basename(filename: str) -> str:
    basename = Path(filename).name or "source.txt"
    sanitized = re.sub(r"[^\w.\- ()\u4e00-\u9fff]", "_", basename)
    return sanitized[:200] or "source.txt"


def get_seekable_stream_size(stream: BinaryIO) -> int | None:
    """返回可 seek 流的总字节数；不可 seek 时返回 None 并由流式计数兜底。"""

    try:
        original_position = stream.tell()
    except (AttributeError, OSError, TypeError, ValueError):
        return None

    size: int | None = None
    try:
        stream.seek(0, 2)
        size = int(stream.tell())
    except (AttributeError, OSError, TypeError, ValueError):
        size = None

    try:
        stream.seek(original_position)
    except (AttributeError, OSError, TypeError, ValueError):
        # 无法恢复调用方游标时，不能声称预检成功，也不擅自把游标移到 0。
        return None

    return max(size, 0) if size is not None else None


def _resolve_staged_file_path(path_value: str) -> Path:
    file_path = Path(path_value).resolve()
    root = ensure_import_task_root().resolve()
    if root not in file_path.parents:
        raise ValueError("导入暂存文件路径无效。")
    return file_path


def stage_import_file_payloads(
    task_id: str,
    files: list[tuple[str, bytes]],
) -> list[dict[str, str]]:
    task_dir = get_import_task_staging_dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    staged: list[dict[str, str]] = []
    for index, (filename, raw_bytes) in enumerate(files, start=1):
        safe_name = f"{index:04d}_{_sanitize_staging_basename(filename)}"
        file_path = task_dir / safe_name
        file_path.write_bytes(raw_bytes)
        staged.append({"filename": filename, "path": str(file_path.resolve())})
    return staged


def stage_import_file_streams(
    task_id: str,
    files: list[tuple[str, BinaryIO]],
    *,
    max_files: int | None = None,
    max_size_resolver: Callable[[str], int] | None = None,
    max_total_bytes: int | None = None,
) -> list[dict[str, str | int]]:
    from app.services.task_file_service import (
        UploadLimitError,
        get_max_upload_size_bytes,
    )

    settings = get_settings()
    limit_files = max_files if max_files is not None else settings.upload_max_files_per_batch
    if len(files) > limit_files:
        raise UploadLimitError(
            f"文件数量超过限制，最多 {limit_files} 个。",
            status_code=400,
        )

    total_limit = max_total_bytes
    if total_limit is None:
        total_limit = settings.upload_max_total_size_mb * 1024 * 1024
    resolve_max_size = max_size_resolver or get_max_upload_size_bytes

    # FastAPI 的 UploadFile 通常是可 seek 的 SpooledTemporaryFile。先用 seek/tell
    # O(1) 获取真实大小，可避免超大文件先被复制到格式上限才收到 413。
    known_total_size = 0
    known_ai_size = 0
    for filename, stream in files:
        original_filename = filename or "source.txt"
        known_size = get_seekable_stream_size(stream)
        if known_size is None:
            continue
        if known_size <= 0:
            raise UploadLimitError(f"文件 {original_filename} 为空。", status_code=400)
        max_size = resolve_max_size(original_filename)
        if known_size > max_size:
            max_mb = round(max_size / (1024 * 1024), 2)
            raise UploadLimitError(
                f"文件 {original_filename} 超过大小限制（{max_mb} MB）。",
                status_code=413,
            )
        known_total_size += known_size
        if Path(original_filename).suffix.lower() == ".ai":
            known_ai_size += known_size
        if known_total_size > total_limit:
            max_total_mb = round(total_limit / (1024 * 1024), 2)
            raise UploadLimitError(
                f"上传总大小超过限制（{max_total_mb} MB）。",
                status_code=413,
            )

    if known_ai_size:
        reserve_bytes = max(int(settings.ai_min_free_disk_mb), 1) * 1024 * 1024
        free_bytes = shutil.disk_usage(ensure_import_task_root()).free
        required_bytes = known_ai_size + reserve_bytes
        if free_bytes < required_bytes:
            required_gib = round(required_bytes / (1024 ** 3), 2)
            raise UploadLimitError(
                f"磁盘空间不足，暂存 AI 至少需要 {required_gib} GiB 可用空间。",
                status_code=507,
            )

    task_dir = get_import_task_staging_dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    staged: list[dict[str, str | int]] = []
    total_size = 0

    for index, (filename, stream) in enumerate(files, start=1):
        original_filename = filename or "source.txt"
        safe_name = f"{index:04d}_{_sanitize_staging_basename(original_filename)}"
        file_path = task_dir / safe_name
        max_size = resolve_max_size(original_filename)
        file_size = 0

        try:
            stream.seek(0)
        except (AttributeError, OSError):
            pass

        with file_path.open("wb") as output:
            while True:
                chunk = stream.read(IMPORT_STAGING_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                file_size += len(chunk)
                total_size += len(chunk)
                if file_size > max_size:
                    max_mb = round(max_size / (1024 * 1024), 2)
                    raise UploadLimitError(
                        f"文件 {original_filename} 超过大小限制（{max_mb} MB）。",
                        status_code=413,
                    )
                if total_size > total_limit:
                    max_total_mb = round(total_limit / (1024 * 1024), 2)
                    raise UploadLimitError(
                        f"上传总大小超过限制（{max_total_mb} MB）。",
                        status_code=413,
                    )
                output.write(chunk)

        if file_size <= 0:
            raise UploadLimitError(f"文件 {original_filename} 为空。", status_code=400)
        staged.append(
            {
                "filename": original_filename,
                "path": str(file_path.resolve()),
                "size": file_size,
            }
        )

    return staged


def stage_import_file_payload(
    task_id: str,
    filename: str,
    raw_bytes: bytes,
) -> dict[str, str]:
    return stage_import_file_payloads(task_id, [(filename, raw_bytes)])[0]


def get_import_file_size(file_payload: dict[str, Any]) -> int:
    """获取暂存 payload 大小，不读取完整文件内容。"""

    content = file_payload.get("content")
    if isinstance(content, (bytes, bytearray)):
        return len(content)

    path_value = file_payload.get("path")
    if not path_value:
        raise ValueError("导入文件缺少 content 或 path。")
    file_path = _resolve_staged_file_path(str(path_value))
    if not file_path.is_file():
        raise FileNotFoundError(f"导入暂存文件不存在：{file_path}")
    return file_path.stat().st_size


def get_import_file_path(file_payload: dict[str, Any]) -> Path:
    """返回受导入暂存根目录约束的文件路径，不读取文件内容。"""

    path_value = file_payload.get("path")
    if not path_value:
        raise ValueError("导入文件缺少 path。")
    file_path = _resolve_staged_file_path(str(path_value))
    if not file_path.is_file():
        raise FileNotFoundError(f"导入暂存文件不存在：{file_path}")
    return file_path


def read_import_file_bytes(file_payload: dict[str, Any]) -> bytes:
    content = file_payload.get("content")
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)

    path_value = file_payload.get("path")
    if not path_value:
        raise ValueError("导入文件缺少 content 或 path。")

    file_path = _resolve_staged_file_path(str(path_value))
    if not file_path.is_file():
        raise FileNotFoundError(f"导入暂存文件不存在：{file_path}")
    return file_path.read_bytes()


def cleanup_import_task_staging(task_id: str) -> None:
    task_dir = get_import_task_root() / task_id
    if not task_dir.exists():
        return
    try:
        shutil.rmtree(task_dir)
    except OSError:
        logger.debug("skip cleanup for import staging dir=%s", task_dir, exc_info=True)


def cleanup_expired_import_staging() -> int:
    root = ensure_import_task_root()
    cutoff = time.time() - _import_task_staging_ttl_seconds()
    removed = 0
    for path in root.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
            elif path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            logger.debug("skip cleanup for import staging path=%s", path, exc_info=True)
    return removed


def initialize_import_task_storage() -> dict[str, int | str]:
    """启动时确保上传相关目录存在，并清理过期 import staging。"""
    settings = get_settings()
    for directory in (
        settings.file_storage_dir,
        settings.export_task_dir,
        settings.import_task_dir,
    ):
        Path(directory).mkdir(parents=True, exist_ok=True)

    removed = cleanup_expired_import_staging()
    if removed:
        logger.info("removed %s expired import staging entries from %s", removed, settings.import_task_dir)
    return {
        "import_task_dir": settings.import_task_dir,
        "expired_staging_removed": removed,
    }
