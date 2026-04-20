from __future__ import annotations

from pathlib import Path
from uuid import UUID

from app.config import get_settings


def save_source_file(file_record_id: UUID, filename: str, raw_bytes: bytes) -> Path:
    path = get_source_file_path(file_record_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw_bytes)
    return path


def load_source_file(file_record_id: UUID, filename: str) -> bytes | None:
    path = get_source_file_path(file_record_id, filename)
    if not path.exists():
        return None
    return path.read_bytes()


def delete_source_file(file_record_id: UUID, filename: str) -> None:
    path = get_source_file_path(file_record_id, filename)
    if path.exists():
        path.unlink()


def get_source_file_path(file_record_id: UUID, filename: str) -> Path:
    suffix = Path(filename).suffix.lower() or ".bin"
    return _get_storage_root() / f"{file_record_id}{suffix}"


def _get_storage_root() -> Path:
    settings = get_settings()
    return Path(settings.file_storage_dir)
