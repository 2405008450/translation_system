from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import FileRecord, MemoryBase, MemoryEntry, Segment
from app.services.normalizer import build_source_hash


def normalize_tm_match_threshold(value: float | int | str | None, default: float = 0.75) -> float:
    try:
        numeric = float(value if value is not None else default)
    except (TypeError, ValueError):
        numeric = default
    return round(min(max(numeric, 0.5), 1.0), 2)


def build_tm_match_signature(
    db: Session,
    *,
    file_record_id: UUID,
    collection_ids: list[UUID],
    threshold: float,
    skip_confirmed: bool,
    overwrite_fuzzy: bool,
    auto_confirm_exact: bool,
) -> str:
    selected_collection_ids = [str(item) for item in list(dict.fromkeys(collection_ids))]
    payload = {
        "version": 1,
        "file_record_id": str(file_record_id),
        "source_fingerprint": _build_file_source_fingerprint(db, file_record_id),
        "collections": _build_collection_fingerprints(db, collection_ids),
        "collection_ids": selected_collection_ids,
        "threshold": normalize_tm_match_threshold(threshold),
        "skip_confirmed": bool(skip_confirmed),
        "overwrite_fuzzy": bool(overwrite_fuzzy),
        "auto_confirm_exact": bool(auto_confirm_exact),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_tm_match_signature_current(file_record: FileRecord | None, signature: str) -> bool:
    return bool(file_record is not None and signature and file_record.tm_match_signature == signature)


def mark_tm_match_signature_current(file_record: FileRecord, signature: str) -> None:
    file_record.tm_match_signature = signature
    file_record.tm_last_matched_at = datetime.now()


def clear_tm_match_signature(file_record: FileRecord) -> None:
    file_record.tm_match_signature = None
    file_record.tm_last_matched_at = None


def _build_file_source_fingerprint(db: Session, file_record_id: UUID) -> dict[str, object]:
    rows = (
        db.query(Segment.sentence_id, Segment.source_hash, Segment.source_text)
        .filter(Segment.file_record_id == file_record_id)
        .order_by(
            Segment.block_index.asc(),
            Segment.row_index.asc().nullsfirst(),
            Segment.cell_index.asc().nullsfirst(),
            Segment.sentence_id.asc(),
            Segment.id.asc(),
        )
        .all()
    )
    digest = hashlib.sha256()
    for sentence_id, source_hash, source_text in rows:
        digest.update(str(sentence_id or "").encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(source_hash or build_source_hash(source_text or "")).encode("utf-8"))
        digest.update(b"\x00")
    return {
        "segment_count": len(rows),
        "hash": digest.hexdigest(),
    }


def _build_collection_fingerprints(db: Session, collection_ids: list[UUID]) -> list[dict[str, object]]:
    fingerprints: list[dict[str, object]] = []
    for collection_id in list(dict.fromkeys(collection_ids)):
        collection = db.query(MemoryBase).filter(MemoryBase.id == collection_id).first()
        entry_count, last_entry_updated_at = (
            db.query(func.count(MemoryEntry.id), func.max(MemoryEntry.updated_at))
            .filter(MemoryEntry.collection_id == collection_id)
            .one()
        )
        fingerprints.append(
            {
                "id": str(collection_id),
                "base_updated_at": _format_dt(collection.updated_at if collection else None),
                "entry_count": int(entry_count or 0),
                "last_entry_updated_at": _format_dt(last_entry_updated_at),
            }
        )
    return fingerprints


def _format_dt(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None
