import hashlib
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import FileRecord, Segment
from app.services.document_workspace import build_docx_workspace


def create_file_record_with_segments(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float = 0.6,
    workspace_data: dict | None = None,
) -> FileRecord:
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    if workspace_data is None:
        workspace_data = build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=similarity_threshold,
        )

    return _create_file_record_from_workspace(
        db=db,
        filename=filename,
        file_hash=file_hash,
        workspace_data=workspace_data,
    )


def _create_file_record_from_workspace(
    db: Session,
    filename: str,
    file_hash: str,
    workspace_data: dict,
) -> FileRecord:
    file_record = FileRecord(
        filename=filename,
        file_hash=file_hash,
        status="in_progress",
    )
    db.add(file_record)
    db.flush()

    for seg in workspace_data["segments"]:
        db.add(
            Segment(
                file_record_id=file_record.id,
                sentence_id=seg["sentence_id"],
                source_text=seg["source_text"],
                display_text=seg["display_text"],
                target_text=seg["target_text"],
                status=seg["status"],
                score=seg["score"],
                matched_source_text=seg["matched_source_text"],
                source="tm" if seg["status"] in ("exact", "fuzzy") else "none",
                block_type=seg["block_type"],
                block_index=seg["block_index"],
                row_index=seg.get("row_index"),
                cell_index=seg.get("cell_index"),
            )
        )

    db.commit()
    db.refresh(file_record)
    return file_record


def create_txt_file_record_with_segments(
    db: Session,
    content: str,
    filename: str,
    results: list,
) -> FileRecord:
    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    file_record = FileRecord(
        filename=filename,
        file_hash=file_hash,
        status="in_progress",
    )
    db.add(file_record)
    db.flush()

    for index, result in enumerate(results):
        db.add(
            Segment(
                file_record_id=file_record.id,
                sentence_id=f"sent-{index + 1:05d}",
                source_text=result.source_sentence,
                display_text=result.source_sentence,
                target_text=result.target_text or "",
                status=result.status,
                score=result.score,
                matched_source_text=result.matched_source_text,
                source="tm" if result.status in ("exact", "fuzzy") else "none",
                block_type="paragraph",
                block_index=index,
            )
        )

    db.commit()
    db.refresh(file_record)
    return file_record


def get_file_record(db: Session, file_record_id: UUID) -> FileRecord | None:
    return db.query(FileRecord).filter(FileRecord.id == file_record_id).first()


def get_file_record_with_segments(
    db: Session,
    file_record_id: UUID,
    skip: int = 0,
    limit: int | None = None,
) -> dict | None:
    file_record = get_file_record(db, file_record_id)
    if not file_record:
        return None

    safe_skip = max(skip, 0)
    base_query = db.query(Segment).filter(Segment.file_record_id == file_record_id)
    total_segments = base_query.count()

    segments_query = base_query.order_by(
        Segment.block_index.asc(),
        Segment.row_index.asc().nullsfirst(),
        Segment.cell_index.asc().nullsfirst(),
        Segment.sentence_id.asc(),
    )
    if safe_skip:
        segments_query = segments_query.offset(safe_skip)
    if limit is not None:
        segments_query = segments_query.limit(max(limit, 0))

    return {
        "file_record": file_record,
        "segments": segments_query.all(),
        "total_segments": total_segments,
        "skip": safe_skip,
        "limit": limit,
    }


def list_file_records(db: Session, skip: int = 0, limit: int = 50) -> list[FileRecord]:
    return (
        db.query(FileRecord)
        .order_by(FileRecord.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def count_file_records(db: Session) -> int:
    return db.query(FileRecord).count()


def update_segment_target(
    db: Session,
    segment_id: UUID,
    target_text: str,
    source: str = "manual",
) -> Segment | None:
    segment = db.query(Segment).filter(Segment.id == segment_id).first()
    if not segment:
        return None

    segment.target_text = target_text
    segment.source = source
    if source == "manual":
        segment.status = "confirmed"

    db.commit()
    db.refresh(segment)
    return segment


def update_segment_by_sentence_id(
    db: Session,
    file_record_id: UUID,
    sentence_id: str,
    target_text: str,
    source: str = "manual",
) -> Segment | None:
    segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not segment:
        return None

    segment.target_text = target_text
    segment.source = source
    if source == "manual":
        segment.status = "confirmed"

    db.commit()
    db.refresh(segment)
    return segment


def batch_update_segments(
    db: Session,
    file_record_id: UUID,
    updates: list[dict],
) -> int:
    updates_by_sentence_id: dict[str, dict] = {}
    for item in updates:
        sentence_id = item.get("sentence_id")
        if sentence_id:
            updates_by_sentence_id[sentence_id] = item

    if not updates_by_sentence_id:
        return 0

    segments = (
        db.query(Segment)
        .filter(
            Segment.file_record_id == file_record_id,
            Segment.sentence_id.in_(list(updates_by_sentence_id.keys())),
        )
        .all()
    )

    updated_count = 0
    for segment in segments:
        item = updates_by_sentence_id.get(segment.sentence_id)
        if item is None:
            continue

        target_text = item.get("target_text", "")
        source = item.get("source", "manual")
        segment.target_text = target_text
        segment.source = source
        if source == "manual":
            segment.status = "confirmed"
        updated_count += 1

    db.commit()
    return updated_count


def delete_file_record(db: Session, file_record_id: UUID) -> bool:
    file_record = get_file_record(db, file_record_id)
    if not file_record:
        return False

    db.delete(file_record)
    db.commit()
    return True
