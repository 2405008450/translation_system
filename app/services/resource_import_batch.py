from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import ResourceImportBatch
from app.services.tmx_stream import TMXHeader, read_tmx_header


def create_resource_import_batch(
    db: Session,
    *,
    resource_type: str,
    resource_id: UUID | None,
    filename: str,
    file_path: str | Path,
    file_format: str,
    source_language: str,
    target_language: str,
    created_by_id: UUID | None,
) -> ResourceImportBatch:
    path = Path(file_path)
    batch = ResourceImportBatch(
        resource_type=resource_type,
        resource_id=resource_id,
        filename=filename,
        file_size_bytes=path.stat().st_size if path.is_file() else 0,
        file_format=file_format.lstrip(".").lower(),
        source_language=source_language,
        target_language=target_language,
        tmx_header_metadata=(
            _serialize_tmx_header(read_tmx_header(path))
            if file_format.lower().lstrip(".") == "tmx"
            else None
        ),
        created_by_id=created_by_id,
    )
    db.add(batch)
    db.flush()
    return batch


def _serialize_tmx_header(header: TMXHeader) -> dict[str, Any] | None:
    payload = {
        "attributes": header.attributes,
        "props": header.props,
        "notes": header.notes,
    }
    compact = {key: value for key, value in payload.items() if value not in ({}, [], None)}
    return compact or None
