from __future__ import annotations

import logging
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import AutoTMOutbox, AutoTMRematchQueue, FileRecord, MemoryBase, Segment, User
from app.services.automatic_numbering import (
    is_word_document_filename,
    strip_automatic_numbering_prefix,
)
from app.services.matcher import match_sentences_with_stats
from app.services.normalizer import normalize_text
from app.services.tm_vector import sync_tm_embeddings
from app.services.translation_memory_service import TMUpsertEntry, batch_upsert_tm_entries


logger = logging.getLogger(__name__)

AUTO_TM_BATCH_SIZE = 200
AUTO_TM_REMATCH_COUNT_THRESHOLD = 200
AUTO_TM_REMATCH_AGE = timedelta(minutes=15)
AUTO_TM_REMATCH_CHUNK_SIZE = 200


@dataclass
class AutoTMEnqueueSummary:
    queued_count: int = 0
    skipped_no_collection_count: int = 0
    skipped_invalid_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "queued_count": self.queued_count,
            "skipped_no_collection_count": self.skipped_no_collection_count,
            "skipped_invalid_count": self.skipped_invalid_count,
        }


def enqueue_confirmed_segments_for_auto_tm(
    db: Session,
    *,
    file_record: FileRecord,
    segments: list[Segment],
    current_user: User | None = None,
) -> AutoTMEnqueueSummary:
    eligible_segments = [
        segment
        for segment in segments
        if segment.status == "confirmed"
        and normalize_text(segment.source_text)
        and normalize_text(segment.target_text)
    ]
    if not eligible_segments:
        return AutoTMEnqueueSummary()

    if file_record.collection_id is None:
        return AutoTMEnqueueSummary(skipped_no_collection_count=len(eligible_segments))

    collection = (
        db.query(MemoryBase)
        .filter(MemoryBase.id == file_record.collection_id)
        .first()
    )
    source_language = (file_record.source_language or (collection.source_language if collection else "") or "").strip()
    target_language = (file_record.target_language or (collection.target_language if collection else "") or "").strip()
    if collection is None or not source_language or not target_language:
        return AutoTMEnqueueSummary(skipped_invalid_count=len(eligible_segments))

    now = datetime.utcnow()
    segment_ids = [segment.id for segment in eligible_segments]
    existing_rows = (
        db.query(AutoTMOutbox)
        .filter(
            AutoTMOutbox.file_record_id == file_record.id,
            AutoTMOutbox.collection_id == file_record.collection_id,
            AutoTMOutbox.segment_id.in_(segment_ids),
        )
        .all()
    )
    existing_by_segment_id = {row.segment_id: row for row in existing_rows}

    clean_numbering = is_word_document_filename(file_record.filename)
    queued_count = 0
    skipped_invalid_count = 0
    for segment in eligible_segments:
        source_text = normalize_text(segment.source_text)
        target_text = normalize_text(
            strip_automatic_numbering_prefix(
                segment.target_text,
                source_text=segment.source_text,
                display_text=segment.display_text,
                reference_texts=[segment.matched_source_text],
            )
            if clean_numbering
            else segment.target_text
        )
        if not source_text or not target_text:
            skipped_invalid_count += 1
            continue
        existing = existing_by_segment_id.get(segment.id)
        if existing is not None:
            existing.sentence_id = segment.sentence_id
            existing.source_text = source_text
            existing.target_text = target_text
            existing.source_language = source_language
            existing.target_language = target_language
            existing.creator_id = current_user.id if current_user else existing.creator_id
            existing.status = "pending"
            existing.error_message = ""
            existing.last_enqueued_at = now
        else:
            db.add(
                AutoTMOutbox(
                    file_record_id=file_record.id,
                    segment_id=segment.id,
                    sentence_id=segment.sentence_id,
                    collection_id=file_record.collection_id,
                    source_text=source_text,
                    target_text=target_text,
                    source_language=source_language,
                    target_language=target_language,
                    creator_id=current_user.id if current_user else None,
                    last_enqueued_at=now,
                )
            )
        queued_count += 1

    return AutoTMEnqueueSummary(queued_count=queued_count, skipped_invalid_count=skipped_invalid_count)


def run_auto_tm_background_once() -> None:
    with SessionLocal() as db:
        try:
            processed_count = process_auto_tm_outbox(db)
            process_due_auto_tm_rematches(db, force=processed_count > 0)
        except Exception:
            logger.exception("auto TM background task failed")


def process_auto_tm_outbox(db: Session, *, batch_size: int = AUTO_TM_BATCH_SIZE) -> int:
    query = (
        db.query(AutoTMOutbox)
        .filter(AutoTMOutbox.status == "pending")
        .order_by(AutoTMOutbox.created_at.asc(), AutoTMOutbox.id.asc())
        .limit(batch_size)
    )
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    rows = query.all()
    if not rows:
        return 0

    row_ids = [row.id for row in rows]
    for row in rows:
        row.status = "processing"
        row.attempt_count += 1
        row.error_message = ""
    db.commit()

    try:
        entries = [
            TMUpsertEntry(
                collection_id=row.collection_id,
                source_text=row.source_text,
                target_text=row.target_text,
                source_language=row.source_language,
                target_language=row.target_language,
                creator_id=row.creator_id,
            )
            for row in rows
        ]
        summary = batch_upsert_tm_entries(db, entries)
        processed_at = datetime.utcnow()
        for row in rows:
            row.status = "completed"
            row.processed_at = processed_at
            row.error_message = ""

        _register_rematch_work(db, rows)
        db.commit()
        sync_tm_embeddings(db, summary.sync_rows or [])
        return len(rows)
    except Exception as exc:
        db.rollback()
        logger.exception("auto TM outbox processing failed")
        failed_rows = db.query(AutoTMOutbox).filter(AutoTMOutbox.id.in_(row_ids)).all()
        for row in failed_rows:
            row.status = "failed" if row.attempt_count >= 3 else "pending"
            row.error_message = str(exc)[:2000]
        db.commit()
        return 0


def process_due_auto_tm_rematches(db: Session, *, force: bool = False) -> int:
    now = datetime.utcnow()
    cutoff = now - AUTO_TM_REMATCH_AGE
    candidates = (
        db.query(AutoTMRematchQueue)
        .filter(AutoTMRematchQueue.status == "pending")
        .all()
    )
    due_queues = [
        queue
        for queue in candidates
        if (
            force
            and (
                int(queue.pending_entry_count or 0) > 0
                or queue.first_pending_at is not None
                or queue.last_pending_at is not None
            )
        )
        or queue.pending_entry_count >= AUTO_TM_REMATCH_COUNT_THRESHOLD
        or (queue.first_pending_at is not None and queue.first_pending_at <= cutoff)
    ]
    refreshed_count = 0
    for queue in due_queues:
        queue.status = "running"
        queue.error_message = ""
        db.commit()
        try:
            updated_count = refresh_unconfirmed_segment_matches(
                db,
                file_record_id=queue.file_record_id,
            )
            queue.pending_entry_count = 0
            queue.first_pending_at = None
            queue.last_pending_at = None
            queue.last_processed_at = datetime.utcnow()
            queue.status = "pending"
            queue.error_message = ""
            db.commit()
            refreshed_count += updated_count
        except Exception as exc:
            db.rollback()
            queue = db.query(AutoTMRematchQueue).filter(AutoTMRematchQueue.id == queue.id).first()
            if queue is not None:
                queue.status = "pending"
                queue.error_message = str(exc)[:2000]
                db.commit()
            logger.exception("auto TM rematch refresh failed")
    return refreshed_count


def refresh_unconfirmed_segment_matches(
    db: Session,
    *,
    file_record_id: UUID,
    collection_id: UUID | None = None,
    collection_ids: list[UUID] | None = None,
) -> int:
    settings = get_settings()
    file_record = db.query(FileRecord).filter(FileRecord.id == file_record_id).first()
    if file_record is None:
        return 0

    selected_collection_ids = _resolve_refresh_collection_ids(
        db,
        file_record=file_record,
        collection_id=collection_id,
        collection_ids=collection_ids,
    )
    if not selected_collection_ids:
        return 0
    similarity_threshold = round(
        float(getattr(file_record, "tm_match_threshold", None) or settings.default_similarity_threshold),
        2,
    )
    similarity_threshold = min(max(similarity_threshold, 0.5), 1.0)
    clean_numbering = is_word_document_filename(file_record.filename)

    updated_count = 0
    offset = 0
    while True:
        segments = (
            db.query(Segment)
            .filter(
                Segment.file_record_id == file_record_id,
                Segment.status != "confirmed",
            )
            .order_by(
                Segment.block_index.asc(),
                Segment.row_index.asc().nullsfirst(),
                Segment.cell_index.asc().nullsfirst(),
                Segment.sentence_id.asc(),
            )
            .offset(offset)
            .limit(AUTO_TM_REMATCH_CHUNK_SIZE)
            .all()
        )
        if not segments:
            break

        source_sentences = [segment.source_text for segment in segments]
        auxiliary_sentences = [segment.display_text for segment in segments]
        matches, _ = match_sentences_with_stats(
            db=db,
            sentences=source_sentences,
            auxiliary_sentences=auxiliary_sentences,
            similarity_threshold=similarity_threshold,
            collection_ids=selected_collection_ids,
        )

        for segment, match in zip(segments, matches, strict=False):
            before = (
                segment.target_text,
                segment.target_html,
                segment.status,
                segment.source,
                segment.score,
                segment.matched_source_text,
                segment.matched_collection_name,
                segment.matched_creator_name,
                segment.matched_created_at,
                segment.matched_updated_at,
            )
            segment.score = float(match.score or 0)
            segment.matched_source_text = match.matched_source_text
            segment.matched_collection_name = match.matched_collection_name
            segment.matched_creator_name = match.matched_creator_name
            segment.matched_created_at = _parse_optional_datetime(match.matched_created_at)
            segment.matched_updated_at = _parse_optional_datetime(match.matched_updated_at)
            if (
                match.target_text is not None
                and match.status in {"exact", "fuzzy"}
                and not normalize_text(segment.target_text)
            ):
                target_text = (
                    strip_automatic_numbering_prefix(
                        match.target_text,
                        source_text=segment.source_text,
                        display_text=segment.display_text,
                        reference_texts=[match.matched_source_text],
                    )
                    if clean_numbering
                    else match.target_text
                )
                if not normalize_text(target_text):
                    continue
                segment.target_text = target_text
                segment.target_html = None
                segment.status = match.status
                segment.source = "tm"
            after = (
                segment.target_text,
                segment.target_html,
                segment.status,
                segment.source,
                segment.score,
                segment.matched_source_text,
                segment.matched_collection_name,
                segment.matched_creator_name,
                segment.matched_created_at,
                segment.matched_updated_at,
            )
            if before != after:
                segment.version = int(segment.version or 1) + 1
                updated_count += 1

        db.flush()
        offset += AUTO_TM_REMATCH_CHUNK_SIZE

    return updated_count


def _register_rematch_work(db: Session, rows: list[AutoTMOutbox]) -> None:
    grouped: dict[tuple[UUID, UUID], int] = {}
    for row in rows:
        grouped[(row.file_record_id, row.collection_id)] = grouped.get((row.file_record_id, row.collection_id), 0) + 1

    for (file_record_id, collection_id), count in grouped.items():
        register_project_collection_rematch_work(
            db,
            collection_id=collection_id,
            source_file_record_id=file_record_id,
            count=count,
        )


def register_project_collection_rematch_work(
    db: Session,
    *,
    collection_id: UUID,
    source_file_record_id: UUID | None = None,
    count: int = 1,
) -> int:
    collection = db.query(MemoryBase).filter(MemoryBase.id == collection_id).first()
    if collection is None or not collection.source_language or not collection.target_language:
        return 0

    source_file_record = None
    if source_file_record_id is not None:
        source_file_record = (
            db.query(FileRecord)
            .filter(FileRecord.id == source_file_record_id)
            .first()
        )

    query = db.query(FileRecord).filter(
        FileRecord.source_language == collection.source_language,
        FileRecord.target_language == collection.target_language,
    )
    if source_file_record and source_file_record.project_id:
        query = query.filter(FileRecord.project_id == source_file_record.project_id)
    elif source_file_record:
        query = query.filter(FileRecord.id == source_file_record.id)

    queued_count = 0
    for file_record in query.all():
        if collection.id not in _load_file_record_collection_ids(file_record):
            continue
        _upsert_rematch_queue(
            db,
            file_record_id=file_record.id,
            collection_id=collection.id,
            count=count,
        )
        queued_count += 1

    return queued_count


def register_project_collections_rematch_work(
    db: Session,
    *,
    collection_ids: list[UUID],
    source_file_record_id: UUID | None = None,
    count: int = 1,
) -> int:
    queued_count = 0
    for collection_id in list(dict.fromkeys(collection_ids)):
        queued_count += register_project_collection_rematch_work(
            db,
            collection_id=collection_id,
            source_file_record_id=source_file_record_id,
            count=count,
        )
    return queued_count


def _upsert_rematch_queue(
    db: Session,
    *,
    file_record_id: UUID,
    collection_id: UUID,
    count: int,
) -> None:
    now = datetime.utcnow()
    queue = (
        db.query(AutoTMRematchQueue)
        .filter(AutoTMRematchQueue.file_record_id == file_record_id)
        .first()
    )
    if queue is None:
        db.add(
            AutoTMRematchQueue(
                file_record_id=file_record_id,
                collection_id=collection_id,
                pending_entry_count=count,
                first_pending_at=now,
                last_pending_at=now,
            )
        )
        return

    queue.collection_id = collection_id
    queue.pending_entry_count = int(queue.pending_entry_count or 0) + count
    queue.status = "pending"
    if queue.first_pending_at is None:
        queue.first_pending_at = now
    queue.last_pending_at = now


def _resolve_refresh_collection_ids(
    db: Session,
    *,
    file_record: FileRecord,
    collection_id: UUID | None,
    collection_ids: list[UUID] | None,
) -> list[UUID]:
    selected_ids = list(dict.fromkeys(collection_ids or ([] if collection_id is None else [collection_id])))
    if not selected_ids:
        selected_ids = _load_file_record_collection_ids(file_record)
    if not selected_ids or not file_record.source_language or not file_record.target_language:
        return []

    collections = (
        db.query(MemoryBase)
        .filter(
            MemoryBase.id.in_(selected_ids),
            or_(MemoryBase.source_language.is_(None), MemoryBase.source_language == file_record.source_language),
            or_(MemoryBase.target_language.is_(None), MemoryBase.target_language == file_record.target_language),
        )
        .all()
    )
    existing_ids = {collection.id for collection in collections}
    return [collection_id for collection_id in selected_ids if collection_id in existing_ids]


def _load_file_record_collection_ids(file_record: FileRecord) -> list[UUID]:
    raw_ids = getattr(file_record, "collection_ids_json", "") or "[]"
    parsed_ids: list[UUID] = []
    try:
        values = json.loads(raw_ids)
    except (TypeError, ValueError):
        values = []
    if isinstance(values, list):
        for value in values:
            try:
                parsed_ids.append(value if isinstance(value, UUID) else UUID(str(value)))
            except (TypeError, ValueError):
                continue
    if not parsed_ids and file_record.collection_id:
        parsed_ids.append(file_record.collection_id)
    return list(dict.fromkeys(parsed_ids))


def _parse_optional_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
