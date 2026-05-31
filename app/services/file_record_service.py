import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import case, event, func, update
from sqlalchemy.orm import Session

from app.models import FileRecord, Segment, TranslationMemory, User
from app.services.document_storage import (
    delete_source_file,
    load_source_file,
    resolve_source_file_path,
    save_source_file,
)
from app.services.document_statistics import (
    normalize_document_statistics,
    serialize_document_statistics,
)
from app.services.document_workspace import (
    DOCUMENT_PARSE_MODE_FULL,
    normalize_document_parse_options,
    normalize_document_parse_mode,
)
from app.services.analytics_service import count_source_words, record_translation_metric_event
from app.services.revision_service import create_revision
from app.services.task_file_service import build_task_workspace

logger = logging.getLogger(__name__)

# 支持通过 DOCX 专用流程处理的扩展名
_DOCX_EXTENSIONS = {".docx"}


SEGMENT_ORDERING = (
    Segment.block_index.asc(),
    Segment.row_index.asc().nullsfirst(),
    Segment.cell_index.asc().nullsfirst(),
    Segment.sentence_id.asc(),
)

_PENDING_SOURCE_FILES_KEY = "pending_source_files"


@dataclass
class SegmentUpdateConflict:
    sentence_id: str
    current_version: int
    attempted_version: int | None
    current_target_text: str


@dataclass
class SegmentBatchUpdateResult:
    updated_count: int
    updated_segments: list[Segment]
    conflicts: list[SegmentUpdateConflict]


@event.listens_for(Session, "after_commit")
def _clear_committed_source_files(session: Session) -> None:
    session.info.pop(_PENDING_SOURCE_FILES_KEY, None)


@event.listens_for(Session, "after_rollback")
def _cleanup_rolled_back_source_files(session: Session) -> None:
    pending_source_files = session.info.pop(_PENDING_SOURCE_FILES_KEY, [])
    for file_record_id, filename in pending_source_files:
        try:
            delete_source_file(file_record_id, filename)
        except Exception:
            logger.warning(
                "failed to cleanup rolled back source file file_record_id=%s filename=%s",
                file_record_id,
                filename,
                exc_info=True,
            )


def _remember_pending_source_file(db: Session, file_record_id: UUID, filename: str) -> None:
    pending_source_files = db.info.setdefault(_PENDING_SOURCE_FILES_KEY, [])
    pending_source_files.append((file_record_id, filename))


def calculate_file_record_progress(total_segments: int, translated_segments: int) -> int:
    if total_segments <= 0:
        return 0
    if translated_segments >= total_segments:
        return 100
    if translated_segments <= 0:
        return 0
    return min(99, int(translated_segments / total_segments * 100))


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
    db.flush()
    total_segments = (
        db.query(func.count(Segment.id))
        .filter(Segment.file_record_id == file_record_id)
        .scalar_subquery()
    )
    translated_segments = (
        db.query(func.count(Segment.id))
        .filter(
            Segment.file_record_id == file_record_id,
            Segment.target_text != "",
        )
        .scalar_subquery()
    )
    status_expr = case(
        (FileRecord.status == "error", FileRecord.status),
        (
            total_segments > 0,
            case(
                (translated_segments >= total_segments, "completed"),
                else_="in_progress",
            ),
        ),
        else_=FileRecord.status,
    )
    result = db.execute(
        update(FileRecord)
        .where(FileRecord.id == file_record_id)
        .values(status=status_expr)
        .returning(FileRecord.status)
        .execution_options(synchronize_session="fetch")
    )
    return result.scalar_one_or_none()


def _count_filled_targets(items: list[dict]) -> int:
    return sum(1 for item in items if item.get("target_text", "") != "")


def _record_initial_translation_events(db: Session, segments: list[Segment]) -> None:
    for segment in segments:
        if not (segment.target_text or "").strip():
            continue
        record_translation_metric_event(
            db,
            segment=segment,
            before_text="",
            after_text=segment.target_text,
            source=segment.source,
            event_key=f"initial:{segment.id}",
            created_at=segment.created_at,
        )


def create_file_record_with_segments(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float = 0.6,
    workspace_data: dict | None = None,
    collection_ids: list[UUID] | None = None,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: dict[str, object] | str | None = None,
) -> FileRecord:
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)

    if workspace_data is None:
        workspace_data = build_task_workspace(
            db=db,
            raw_bytes=raw_bytes,
            filename=filename,
            similarity_threshold=similarity_threshold,
            collection_ids=collection_ids,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        )

    return _create_file_record_from_workspace(
        db=db,
        filename=filename,
        file_hash=file_hash,
        workspace_data=workspace_data,
        raw_bytes=raw_bytes,
        document_parse_mode=document_parse_mode,
        document_parse_options=document_parse_options,
    )


def build_duplicate_filename(
    db: Session,
    filename: str,
    project_id: UUID | None,
) -> str:
    path = Path(filename or "untitled.txt")
    suffix = path.suffix
    stem = path.name[: -len(suffix)] if suffix else path.name
    stem = stem or "untitled"

    base_name = f"{stem} - 副本{suffix}"
    query = db.query(FileRecord.filename)
    if project_id is None:
        query = query.filter(FileRecord.project_id.is_(None))
    else:
        query = query.filter(FileRecord.project_id == project_id)
    existing_names = {row.filename for row in query.all()}
    if base_name not in existing_names:
        return base_name

    index = 2
    while True:
        candidate = f"{stem} - 副本 {index}{suffix}"
        if candidate not in existing_names:
            return candidate
        index += 1


def duplicate_file_record(
    db: Session,
    file_record_id: UUID,
    *,
    current_user: User | None = None,
    filename: str | None = None,
) -> FileRecord | None:
    source_record = get_file_record(db, file_record_id)
    if source_record is None:
        return None

    source_bytes = load_file_record_source(source_record)
    next_filename = (filename or "").strip() or build_duplicate_filename(
        db,
        source_record.filename,
        source_record.project_id,
    )
    duplicate = FileRecord(
        project_id=source_record.project_id,
        filename=next_filename,
        file_hash=source_record.file_hash,
        status=source_record.status,
        document_parse_mode=source_record.document_parse_mode,
        document_parse_options=source_record.document_parse_options,
        document_statistics=source_record.document_statistics,
        source_language=source_record.source_language,
        target_language=source_record.target_language,
        creator_id=current_user.id if current_user is not None else source_record.creator_id,
        collection_id=source_record.collection_id,
        term_base_id=source_record.term_base_id,
        term_base_ids=source_record.term_base_ids,
        deadline=source_record.deadline,
        access_level=source_record.access_level,
    )
    db.add(duplicate)
    db.flush()

    source_segments = list_segments_for_file_record(db, source_record.id)
    for segment in source_segments:
        db.add(
            Segment(
                file_record_id=duplicate.id,
                sentence_id=segment.sentence_id,
                source_text=segment.source_text,
                display_text=segment.display_text,
                target_text=segment.target_text,
                status=segment.status,
                score=segment.score,
                matched_source_text=segment.matched_source_text,
                matched_collection_name=segment.matched_collection_name,
                matched_creator_name=segment.matched_creator_name,
                matched_created_at=segment.matched_created_at,
                matched_updated_at=segment.matched_updated_at,
                source=segment.source,
                source_word_count=segment.source_word_count,
                llm_provider=segment.llm_provider,
                llm_model=segment.llm_model,
                block_type=segment.block_type,
                block_index=segment.block_index,
                row_index=segment.row_index,
                cell_index=segment.cell_index,
            )
        )

    if source_bytes is not None:
        save_source_file(duplicate.id, next_filename, source_bytes)
        _remember_pending_source_file(db, duplicate.id, next_filename)

    db.flush()
    return duplicate


def _create_file_record_from_workspace(
    db: Session,
    filename: str,
    file_hash: str,
    workspace_data: dict,
    raw_bytes: bytes | None = None,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: dict[str, object] | str | None = None,
) -> FileRecord:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)
    file_record = FileRecord(
        filename=filename,
        file_hash=file_hash,
        status="in_progress",
        document_parse_mode=document_parse_mode,
        document_parse_options=json.dumps(document_parse_options, ensure_ascii=False, sort_keys=True),
        document_statistics=serialize_document_statistics(workspace_data.get("document_statistics")),
    )
    db.add(file_record)
    db.flush()

    created_segments: list[Segment] = []
    for seg in workspace_data["segments"]:
        segment = Segment(
            file_record_id=file_record.id,
            sentence_id=seg["sentence_id"],
            source_text=seg["source_text"],
            display_text=seg["display_text"],
            target_text=seg["target_text"],
            status=seg["status"],
            score=seg["score"],
            matched_source_text=seg["matched_source_text"],
            matched_collection_name=seg.get("matched_collection_name"),
            matched_creator_name=seg.get("matched_creator_name"),
            matched_created_at=datetime.fromisoformat(seg["matched_created_at"]) if seg.get("matched_created_at") else None,
            matched_updated_at=datetime.fromisoformat(seg["matched_updated_at"]) if seg.get("matched_updated_at") else None,
            source="tm" if seg["status"] in ("exact", "fuzzy") else "none",
            source_word_count=count_source_words(seg["source_text"]),
            block_type=seg["block_type"],
            block_index=seg["block_index"],
            row_index=seg.get("row_index"),
            cell_index=seg.get("cell_index"),
        )
        db.add(segment)
        created_segments.append(segment)

    file_record.status = resolve_file_record_status(
        file_record.status,
        len(workspace_data["segments"]),
        _count_filled_targets(workspace_data["segments"]),
    )

    if raw_bytes is not None:
        save_source_file(file_record.id, filename, raw_bytes)
        _remember_pending_source_file(db, file_record.id, filename)
    db.flush()
    _record_initial_translation_events(db, created_segments)
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

    created_segments: list[Segment] = []
    for index, result in enumerate(results):
        segment = Segment(
            file_record_id=file_record.id,
            sentence_id=f"sent-{index + 1:05d}",
            source_text=result.source_sentence,
            display_text=result.source_sentence,
            target_text=result.target_text or "",
            status=result.status,
            score=result.score,
            matched_source_text=result.matched_source_text,
            matched_collection_name=getattr(result, "matched_collection_name", None),
            source="tm" if result.status in ("exact", "fuzzy") else "none",
            source_word_count=count_source_words(result.source_sentence),
            block_type="paragraph",
            block_index=index,
        )
        db.add(segment)
        created_segments.append(segment)

    file_record.status = resolve_file_record_status(
        file_record.status,
        len(results),
        sum(1 for result in results if (result.target_text or "") != ""),
    )

    db.flush()
    _record_initial_translation_events(db, created_segments)
    db.commit()
    db.refresh(file_record)
    return file_record


def create_file_record_via_adapter(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float = 0.6,
    collection_ids: list[UUID] | None = None,
) -> FileRecord:
    """通过适配器系统创建文件记录（支持多格式）"""
    from app.services.adapters import get_registry, extract_segments
    from app.services.matcher import match_sentences_with_stats

    registry = get_registry()
    adapter = registry.get_adapter(filename)
    result = adapter.parse_with_validation(raw_bytes, filename)

    segments_data = []
    source_texts = []
    for i, seg in enumerate(result.segments):
        segments_data.append({
            "sentence_id": seg.segment_id or f"sent-{i + 1:05d}",
            "source_text": seg.source_text,
            "display_text": seg.display_text,
            "target_text": "",
            "status": "none",
            "score": 0.0,
            "matched_source_text": None,
            "block_type": "paragraph",
            "block_index": i,
            "row_index": None,
            "cell_index": None,
        })
        source_texts.append(seg.source_text)

    if source_texts:
        match_results, _ = match_sentences_with_stats(
            db=db,
            sentences=source_texts,
            auxiliary_sentences=source_texts,
            similarity_threshold=similarity_threshold,
            collection_ids=collection_ids,
        )
        for seg_data, match in zip(segments_data, match_results):
            seg_data["status"] = match.status
            seg_data["score"] = match.score
            seg_data["matched_source_text"] = match.matched_source_text
            seg_data["target_text"] = match.target_text or ""

    workspace_data = {
        "document_html": "",
        "segments": segments_data,
    }
    try:
        file_record = create_file_record_with_segments(
            db=db,
            raw_bytes=raw_bytes,
            filename=filename,
            similarity_threshold=similarity_threshold,
            workspace_data=workspace_data,
            collection_ids=collection_ids,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
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


def get_file_record_document_statistics(file_record: FileRecord) -> dict:
    return normalize_document_statistics(getattr(file_record, "document_statistics", None))


def attach_source_document_to_file_record(
    db: Session,
    file_record: FileRecord,
    raw_bytes: bytes,
    source_filename: str,
    similarity_threshold: float = 0.6,
    collection_ids: list[UUID] | None = None,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: dict[str, object] | str | None = None,
) -> FileRecord:
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)
    workspace_data = build_task_workspace(
        db=db,
        raw_bytes=raw_bytes,
        filename=source_filename,
        similarity_threshold=similarity_threshold,
        collection_ids=collection_ids,
        document_parse_mode=document_parse_mode,
        document_parse_options=document_parse_options,
    )

    file_record.file_hash = file_hash
    file_record.status = "in_progress"
    file_record.document_parse_mode = document_parse_mode
    file_record.document_parse_options = json.dumps(document_parse_options, ensure_ascii=False, sort_keys=True)
    file_record.document_statistics = serialize_document_statistics(workspace_data.get("document_statistics"))

    created_segments: list[Segment] = []
    for seg in workspace_data["segments"]:
        segment = Segment(
            file_record_id=file_record.id,
            sentence_id=seg["sentence_id"],
            source_text=seg["source_text"],
            display_text=seg["display_text"],
            target_text=seg["target_text"],
            status=seg["status"],
            score=seg["score"],
            matched_source_text=seg["matched_source_text"],
            matched_collection_name=seg.get("matched_collection_name"),
            matched_creator_name=seg.get("matched_creator_name"),
            matched_created_at=datetime.fromisoformat(seg["matched_created_at"]) if seg.get("matched_created_at") else None,
            matched_updated_at=datetime.fromisoformat(seg["matched_updated_at"]) if seg.get("matched_updated_at") else None,
            source="tm" if seg["status"] in ("exact", "fuzzy") else "none",
            source_word_count=count_source_words(seg["source_text"]),
            block_type=seg["block_type"],
            block_index=seg["block_index"],
            row_index=seg.get("row_index"),
            cell_index=seg.get("cell_index"),
        )
        db.add(segment)
        created_segments.append(segment)

    file_record.status = resolve_file_record_status(
        file_record.status,
        len(workspace_data["segments"]),
        _count_filled_targets(workspace_data["segments"]),
    )

    try:
        save_source_file(file_record.id, source_filename, raw_bytes)
        db.flush()
        _record_initial_translation_events(db, created_segments)
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
    if scope == "empty_target_only":
        return (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                func.trim(Segment.target_text) == "",
            )
            .order_by(*SEGMENT_ORDERING)
            .all()
        )

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
    collection_id: UUID | None = None,
    source_language: str | None = None,
    target_language: str | None = None,
) -> dict[str, str]:
    unique_source_texts = [text for text in dict.fromkeys(source_texts) if text]
    if not unique_source_texts:
        return {}

    query = db.query(TranslationMemory).filter(TranslationMemory.source_text.in_(unique_source_texts))
    if collection_id is not None:
        query = query.filter(TranslationMemory.collection_id == collection_id)
    if source_language:
        query = query.filter(TranslationMemory.source_language == source_language)
    if target_language:
        query = query.filter(TranslationMemory.target_language == target_language)

    matches = query.all()
    return {match.source_text: match.target_text for match in matches}


def update_segment_target(
    db: Session,
    segment_id: UUID,
    target_text: str,
    source: str = "manual",
    current_user: User | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    track_revision: bool = True,
) -> Segment | None:
    segment = db.query(Segment).filter(Segment.id == segment_id).first()
    if not segment:
        return None

    before_text = segment.target_text
    segment.target_text = target_text
    segment.source = source
    segment.version = int(segment.version or 1) + 1
    segment.source_word_count = segment.source_word_count or count_source_words(segment.source_text)
    if source == "llm":
        segment.llm_provider = llm_provider
        segment.llm_model = llm_model
    else:
        segment.llm_provider = None
        segment.llm_model = None
    if source == "manual":
        segment.status = "confirmed"
    if track_revision:
        create_revision(
            db,
            file_record_id=segment.file_record_id,
            segment=segment,
            before_text=before_text,
            after_text=target_text,
            source=source,
            author=current_user,
        )
    record_translation_metric_event(
        db,
        segment=segment,
        before_text=before_text,
        after_text=target_text,
        source=source,
        current_user=current_user,
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
    llm_provider: str | None = None,
    llm_model: str | None = None,
    track_revision: bool = True,
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
    segment.version = int(segment.version or 1) + 1
    segment.source_word_count = segment.source_word_count or count_source_words(segment.source_text)
    if source == "llm":
        segment.llm_provider = llm_provider
        segment.llm_model = llm_model
    else:
        segment.llm_provider = None
        segment.llm_model = None
    if source == "manual":
        segment.status = "confirmed"
    if track_revision:
        create_revision(
            db,
            file_record_id=file_record_id,
            segment=segment,
            before_text=before_text,
            after_text=target_text,
            source=source,
            author=current_user,
        )
    record_translation_metric_event(
        db,
        segment=segment,
        before_text=before_text,
        after_text=target_text,
        source=source,
        current_user=current_user,
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
    llm_provider: str | None = None,
    llm_model: str | None = None,
) -> Segment | None:
    return update_segment_by_sentence_id(
        db=db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        target_text=target_text,
        source="llm",
        current_user=current_user,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )


def batch_update_segments(
    db: Session,
    file_record_id: UUID,
    updates: list[dict],
    current_user: User | None = None,
    *,
    return_result: bool = False,
) -> int | SegmentBatchUpdateResult:
    updates_by_sentence_id: dict[str, dict] = {}
    for item in updates:
        sentence_id = item.get("sentence_id")
        if sentence_id:
            updates_by_sentence_id[sentence_id] = item

    if not updates_by_sentence_id:
        empty_result = SegmentBatchUpdateResult(updated_count=0, updated_segments=[], conflicts=[])
        return empty_result if return_result else 0

    segments = (
        db.query(Segment)
        .filter(
            Segment.file_record_id == file_record_id,
            Segment.sentence_id.in_(list(updates_by_sentence_id.keys())),
        )
        .all()
    )

    updated_segments: list[Segment] = []
    conflicts: list[SegmentUpdateConflict] = []
    for segment in segments:
        item = updates_by_sentence_id.get(segment.sentence_id)
        if item is None:
            continue

        base_version = item.get("base_version")
        attempted_version = int(base_version) if base_version is not None else None
        current_version = int(segment.version or 1)
        if attempted_version is not None and attempted_version != current_version:
            conflicts.append(
                SegmentUpdateConflict(
                    sentence_id=segment.sentence_id,
                    current_version=current_version,
                    attempted_version=attempted_version,
                    current_target_text=segment.target_text or "",
                )
            )
            continue

        before_text = segment.target_text
        target_text = item.get("target_text", "")
        source = item.get("source", "manual")
        track_revision = bool(item.get("track_revision", True))
        segment.target_text = target_text
        segment.source = source
        segment.version = current_version + 1
        segment.source_word_count = segment.source_word_count or count_source_words(segment.source_text)
        if source == "llm":
            segment.llm_provider = item.get("llm_provider")
            segment.llm_model = item.get("llm_model")
        else:
            segment.llm_provider = None
            segment.llm_model = None
        if source == "manual":
            segment.status = "confirmed"
        if track_revision:
            create_revision(
                db,
                file_record_id=file_record_id,
                segment=segment,
                before_text=before_text,
                after_text=target_text,
                source=source,
                author=current_user,
            )
        record_translation_metric_event(
            db,
            segment=segment,
            before_text=before_text,
            after_text=target_text,
            source=source,
            current_user=current_user,
        )
        updated_segments.append(segment)

    updated_count = len(updated_segments)
    if updated_count > 0:
        sync_file_record_status(db, file_record_id)

    db.commit()
    result = SegmentBatchUpdateResult(
        updated_count=updated_count,
        updated_segments=updated_segments,
        conflicts=conflicts,
    )
    return result if return_result else updated_count


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
