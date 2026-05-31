from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import AutoTMOutbox, AutoTMRematchQueue, FileRecord, MemoryBase, Segment, User
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

    queued_count = 0
    for segment in eligible_segments:
        source_text = normalize_text(segment.source_text)
        target_text = normalize_text(segment.target_text)
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

    return AutoTMEnqueueSummary(queued_count=queued_count)


def run_auto_tm_background_once() -> None:
    with SessionLocal() as db:
        try:
            process_auto_tm_outbox(db)
            process_due_auto_tm_rematches(db)
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


def process_due_auto_tm_rematches(db: Session) -> int:
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
        if queue.pending_entry_count >= AUTO_TM_REMATCH_COUNT_THRESHOLD
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
                collection_id=queue.collection_id,
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
    collection_id: UUID,
) -> int:
    settings = get_settings()
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
            similarity_threshold=settings.default_similarity_threshold,
            collection_ids=[collection_id],
        )

        for segment, match in zip(segments, matches, strict=False):
            before = (
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
            after = (
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
    now = datetime.utcnow()
    grouped: dict[tuple[UUID, UUID], int] = {}
    for row in rows:
        grouped[(row.file_record_id, row.collection_id)] = grouped.get((row.file_record_id, row.collection_id), 0) + 1

    for (file_record_id, collection_id), count in grouped.items():
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
            continue

        queue.collection_id = collection_id
        queue.pending_entry_count = int(queue.pending_entry_count or 0) + count
        queue.status = "pending"
        if queue.first_pending_at is None:
            queue.first_pending_at = now
        queue.last_pending_at = now


def _parse_optional_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
