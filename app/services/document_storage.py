from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import UUID

from app.config import get_settings


def save_source_file(file_record_id: UUID, filename: str, raw_bytes: bytes) -> Path:
    path = get_source_file_path(file_record_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw_bytes)
    return path


def save_source_file_from_path(
    file_record_id: UUID,
    filename: str,
    source_path: str | Path,
) -> Path:
    """以固定大小缓冲把暂存源文件持久化，避免大型文件整体进入内存。"""

    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"源文件不存在：{source}")

    destination = get_source_file_path(file_record_id, filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if Path(filename).suffix.lower() == ".ai":
        source_size = source.stat().st_size
        reserve_bytes = max(int(get_settings().ai_min_free_disk_mb), 1) * 1024 * 1024
        required_bytes = source_size + reserve_bytes
        free_bytes = shutil.disk_usage(destination.parent).free
        if free_bytes < required_bytes:
            required_gib = round(required_bytes / (1024 ** 3), 2)
            raise OSError(f"持久化 AI 的磁盘空间不足，至少需要 {required_gib} GiB 可用空间。")
    temporary = destination.with_name(f".{destination.name}.part")
    try:
        with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def resolve_source_file_size(file_record_id: UUID, filename: str) -> int | None:
    path = resolve_source_file_path(file_record_id, filename)
    if path is None:
        return None
    return path.stat().st_size


def load_source_file(file_record_id: UUID, filename: str) -> bytes | None:
    path = resolve_source_file_path(file_record_id, filename)
    if path is None:
        return None
    return path.read_bytes()


def delete_source_file(file_record_id: UUID, filename: str) -> None:
    candidates = {get_source_file_path(file_record_id, filename), *iter_source_file_paths(file_record_id)}
    for path in candidates:
        if path.exists():
            path.unlink()


def get_source_file_path(file_record_id: UUID, filename: str) -> Path:
    suffix = Path(filename).suffix.lower() or ".bin"
    return _get_storage_root() / f"{file_record_id}{suffix}"


def resolve_source_file_path(file_record_id: UUID, filename: str) -> Path | None:
    path = get_source_file_path(file_record_id, filename)
    if path.exists():
        return path
    return _find_any_source_file(file_record_id)


def _get_storage_root() -> Path:
    settings = get_settings()
    return Path(settings.file_storage_dir)


def iter_source_file_paths(file_record_id: UUID) -> set[Path]:
    return set(_get_storage_root().glob(f"{file_record_id}.*"))


def _find_any_source_file(file_record_id: UUID) -> Path | None:
    for path in iter_source_file_paths(file_record_id):
        return path
    return None
