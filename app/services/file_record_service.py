import hashlib
from pathlib import Path
from uuid import UUID

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import FileRecord, Segment, TranslationMemory, User
from app.services.document_storage import (
    delete_source_file,
    load_source_file,
    resolve_source_file_path,
    save_source_file,
)
from app.services.revision_service import create_revision
from app.services.task_file_service import build_task_workspace


SEGMENT_ORDERING = (
    Segment.block_index.asc(),
    Segment.row_index.asc().nullsfirst(),
    Segment.cell_index.asc().nullsfirst(),
    Segment.sentence_id.asc(),
)


def calculate_file_record_progress(total_segments: int, translated_segments: int) -> int:
    return round(translated_segments / total_segments * 100) if total_segments > 0 else 0


def resolve_file_record_status(
    current_status: str | None,
    total_segments: int,
    translated_segments: int,
) -> str:
    status = current_status or "draft"
    if status == "error":
        return status
    if total_segments > 0:
        if translated_segments >= total_segments:
            return "completed"
        return "in_progress"
    return status


def get_file_record_segment_counts(db: Session, file_record_id: UUID) -> tuple[int, int]:
    total_segments, translated_segments = (
        db.query(
            func.count(Segment.id),
            func.count(case((Segment.target_text != "", 1))),
        )
        .filter(Segment.file_record_id == file_record_id)
        .one()
    )
    return int(total_segments or 0), int(translated_segments or 0)


def sync_file_record_status(db: Session, file_record_id: UUID) -> str | None:
    file_record = get_file_record(db, file_record_id)
    if not file_record:
        return None

    db.flush()
    total_segments, translated_segments = get_file_record_segment_counts(db, file_record_id)
    file_record.status = resolve_file_record_status(
        file_record.status,
        total_segments,
        translated_segments,
    )
    return file_record.status


def _count_filled_targets(items: list[dict]) -> int:
    return sum(1 for item in items if item.get("target_text", "") != "")


def create_file_record_with_segments(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float = 0.6,
    workspace_data: dict | None = None,
    collection_ids: list[UUID] | None = None,
) -> FileRecord:
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    if workspace_data is None:
        workspace_data = build_task_workspace(
            db=db,
            raw_bytes=raw_bytes,
            filename=filename,
            similarity_threshold=similarity_threshold,
            collection_ids=collection_ids,
        )

    return _create_file_record_from_workspace(
        db=db,
        filename=filename,
        file_hash=file_hash,
        workspace_data=workspace_data,
        raw_bytes=raw_bytes,
    )


def _create_file_record_from_workspace(
    db: Session,
    filename: str,
    file_hash: str,
    workspace_data: dict,
    raw_bytes: bytes | None = None,
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

    file_record.status = resolve_file_record_status(
        file_record.status,
        len(workspace_data["segments"]),
        _count_filled_targets(workspace_data["segments"]),
    )

    try:
        if raw_bytes is not None:
            save_source_file(file_record.id, filename, raw_bytes)
        db.commit()
    except Exception:
        db.rollback()
        if raw_bytes is not None:
            delete_source_file(file_record.id, filename)
        raise

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

    file_record.status = resolve_file_record_status(
        file_record.status,
        len(results),
        sum(1 for result in results if (result.target_text or "") != ""),
    )

    db.commit()
    db.refresh(file_record)
    return file_record


def get_file_record(db: Session, file_record_id: UUID) -> FileRecord | None:
    return db.query(FileRecord).filter(FileRecord.id == file_record_id).first()


def load_file_record_source(file_record: FileRecord) -> bytes | None:
    return load_source_file(file_record.id, file_record.filename)


def get_file_record_source_filename(file_record: FileRecord) -> str:
    source_path = resolve_source_file_path(file_record.id, file_record.filename)
    if source_path is None:
        return file_record.filename

    stored_extension = source_path.suffix.lower()
    filename_extension = Path(file_record.filename).suffix.lower()
    if not stored_extension or stored_extension == filename_extension:
        return file_record.filename

    base_name = (
        file_record.filename[: -len(filename_extension)]
        if filename_extension
        else file_record.filename
    )
    return f"{base_name}{stored_extension}"


def attach_source_document_to_file_record(
    db: Session,
    file_record: FileRecord,
    raw_bytes: bytes,
    source_filename: str,
    similarity_threshold: float = 0.6,
    collection_ids: list[UUID] | None = None,
) -> FileRecord:
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    workspace_data = build_task_workspace(
        db=db,
        raw_bytes=raw_bytes,
        filename=source_filename,
        similarity_threshold=similarity_threshold,
        collection_ids=collection_ids,
    )

    file_record.file_hash = file_hash
    file_record.status = "in_progress"

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

    file_record.status = resolve_file_record_status(
        file_record.status,
        len(workspace_data["segments"]),
        _count_filled_targets(workspace_data["segments"]),
    )

    try:
        save_source_file(file_record.id, source_filename, raw_bytes)
        db.commit()
    except Exception:
        db.rollback()
        delete_source_file(file_record.id, source_filename)
        raise

    db.refresh(file_record)
    return file_record


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

    segments_query = base_query.order_by(*SEGMENT_ORDERING)
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


def list_segments_for_file_record(
    db: Session,
    file_record_id: UUID,
) -> list[Segment]:
    return (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id)
        .order_by(*SEGMENT_ORDERING)
        .all()
    )


def list_segments_for_llm_translation(
    db: Session,
    file_record_id: UUID,
    scope: str = "all",
) -> list[Segment]:
    statuses_by_scope = {
        "fuzzy_only": ["fuzzy"],
        "none_only": ["none"],
        "all": ["fuzzy", "none"],
        "all_with_exact": ["exact", "fuzzy", "none"],
    }
    statuses = statuses_by_scope.get(scope)
    if statuses is None:
        raise ValueError(f"不支持的 scope: {scope}")

    return (
        db.query(Segment)
        .filter(
            Segment.file_record_id == file_record_id,
            Segment.status.in_(statuses),
        )
        .order_by(*SEGMENT_ORDERING)
        .all()
    )


def get_tm_target_text_map(
    db: Session,
    source_texts: list[str],
) -> dict[str, str]:
    unique_source_texts = [text for text in dict.fromkeys(source_texts) if text]
    if not unique_source_texts:
        return {}

    matches = (
        db.query(TranslationMemory)
        .filter(TranslationMemory.source_text.in_(unique_source_texts))
        .all()
    )
    return {match.source_text: match.target_text for match in matches}


def update_segment_target(
    db: Session,
    segment_id: UUID,
    target_text: str,
    source: str = "manual",
    current_user: User | None = None,
) -> Segment | None:
    segment = db.query(Segment).filter(Segment.id == segment_id).first()
    if not segment:
        return None

    before_text = segment.target_text
    segment.target_text = target_text
    segment.source = source
    if source == "manual":
        segment.status = "confirmed"
    create_revision(
        db,
        file_record_id=segment.file_record_id,
        segment=segment,
        before_text=before_text,
        after_text=target_text,
        source=source,
        author=current_user,
    )

    sync_file_record_status(db, segment.file_record_id)
    db.commit()
    db.refresh(segment)
    return segment


def update_segment_by_sentence_id(
    db: Session,
    file_record_id: UUID,
    sentence_id: str,
    target_text: str,
    source: str = "manual",
    current_user: User | None = None,
) -> Segment | None:
    segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id, Segment.sentence_id == sentence_id)
        .first()
    )
    if not segment:
        return None

    before_text = segment.target_text
    segment.target_text = target_text
    segment.source = source
    if source == "manual":
        segment.status = "confirmed"
    create_revision(
        db,
        file_record_id=file_record_id,
        segment=segment,
        before_text=before_text,
        after_text=target_text,
        source=source,
        author=current_user,
    )

    sync_file_record_status(db, segment.file_record_id)
    db.commit()
    db.refresh(segment)
    return segment


def update_segment_with_llm_result(
    db: Session,
    file_record_id: UUID,
    sentence_id: str,
    target_text: str,
    current_user: User | None = None,
) -> Segment | None:
    return update_segment_by_sentence_id(
        db=db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        target_text=target_text,
        source="llm",
        current_user=current_user,
    )


def batch_update_segments(
    db: Session,
    file_record_id: UUID,
    updates: list[dict],
    current_user: User | None = None,
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

        before_text = segment.target_text
        target_text = item.get("target_text", "")
        source = item.get("source", "manual")
        segment.target_text = target_text
        segment.source = source
        if source == "manual":
            segment.status = "confirmed"
        create_revision(
            db,
            file_record_id=file_record_id,
            segment=segment,
            before_text=before_text,
            after_text=target_text,
            source=source,
            author=current_user,
        )
        updated_count += 1

    if updated_count > 0:
        sync_file_record_status(db, file_record_id)

    db.commit()
    return updated_count


def delete_file_record(db: Session, file_record_id: UUID) -> bool:
    file_record = get_file_record(db, file_record_id)
    if not file_record:
        return False

    db.delete(file_record)
    db.commit()
    if file_record.filename:
        delete_source_file(file_record.id, file_record.filename)
    return True


def _is_docx_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() == ".docx"
