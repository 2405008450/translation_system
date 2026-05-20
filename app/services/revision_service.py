from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.auth import serialize_user
from app.database import engine
from app.models import Segment, SegmentRevision, User


REVISIONS_TABLE_MISSING_MESSAGE = (
    "segment_revisions table is missing. Please run scripts/create_segment_revisions.sql."
)
REVISION_SOURCE_VALUES = {"manual", "llm", "tm"}
REVISION_STATUS_VALUES = {"pending", "accepted", "rejected"}
REVISION_RESOLVED_STATUS_VALUES = {"accepted", "rejected"}


def revisions_table_exists() -> bool:
    try:
        return inspect(engine).has_table(SegmentRevision.__tablename__)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Unable to inspect segment_revisions table: {exc}",
        ) from exc


def require_revisions_table() -> None:
    if not revisions_table_exists():
        raise HTTPException(status_code=503, detail=REVISIONS_TABLE_MISSING_MESSAGE)


def serialize_segment_revision(revision: SegmentRevision) -> dict:
    return {
        "id": str(revision.id),
        "file_record_id": str(revision.file_record_id),
        "segment_id": str(revision.segment_id),
        "sentence_id": revision.sentence_id,
        "before_text": revision.before_text,
        "after_text": revision.after_text,
        "source": revision.source,
        "status": revision.status,
        "author": serialize_user(revision.author) if revision.author else None,
        "resolved_by": serialize_user(revision.resolved_by) if revision.resolved_by else None,
        "created_at": revision.created_at.isoformat(),
        "resolved_at": revision.resolved_at.isoformat() if revision.resolved_at else None,
    }


def list_revisions(
    db: Session,
    *,
    file_record_id: UUID,
    sentence_id: str | None = None,
    sentence_ids: list[str] | None = None,
) -> list[SegmentRevision]:
    require_revisions_table()
    query = (
        db.query(SegmentRevision)
        .options(
            joinedload(SegmentRevision.author),
            joinedload(SegmentRevision.resolved_by),
        )
        .filter(SegmentRevision.file_record_id == file_record_id)
    )
    if sentence_id:
        query = query.filter(SegmentRevision.sentence_id == sentence_id)
    elif sentence_ids is not None:
        if not sentence_ids:
            return []
        query = query.filter(SegmentRevision.sentence_id.in_(sentence_ids))

    revisions = (
        query
        .order_by(SegmentRevision.created_at.desc(), SegmentRevision.id.desc())
        .all()
    )
    return _collapse_pending_revisions_for_read(revisions)


def create_revision(
    db: Session,
    *,
    file_record_id: UUID,
    segment: Segment,
    before_text: str,
    after_text: str,
    source: str,
    author: User | None = None,
) -> SegmentRevision | None:
    normalized_before_text = before_text or ""
    normalized_after_text = after_text or ""
    normalized_source = _normalize_revision_source(source)
    if normalized_before_text == normalized_after_text:
        return None

    pending_revisions = _list_pending_revisions_for_segment(db, segment.id)
    if pending_revisions:
        anchor_revision, duplicate_revisions = _merge_pending_revisions(
            pending_revisions,
            after_text=normalized_after_text,
            source=normalized_source,
            author_id=author.id if author else None,
        )
        if anchor_revision.before_text == anchor_revision.after_text:
            db.delete(anchor_revision)
            return None

        for duplicate_revision in duplicate_revisions:
            db.delete(duplicate_revision)
        return anchor_revision

    revision = SegmentRevision(
        file_record_id=file_record_id,
        segment_id=segment.id,
        sentence_id=segment.sentence_id,
        before_text=normalized_before_text,
        after_text=normalized_after_text,
        source=normalized_source,
        status="pending",
        author_id=author.id if author else None,
    )
    db.add(revision)
    return revision


def accept_revision(
    db: Session,
    *,
    revision_id: UUID,
    current_user: User,
) -> SegmentRevision:
    return _resolve_revision(
        db,
        revision_id=revision_id,
        next_status="accepted",
        current_user=current_user,
    )


def reject_revision(
    db: Session,
    *,
    revision_id: UUID,
    current_user: User,
) -> SegmentRevision:
    return _resolve_revision(
        db,
        revision_id=revision_id,
        next_status="rejected",
        current_user=current_user,
    )


def batch_accept_revisions(
    db: Session,
    *,
    file_record_id: UUID,
    current_user: User,
) -> int:
    return _batch_resolve_revisions(
        db,
        file_record_id=file_record_id,
        next_status="accepted",
        current_user=current_user,
    )


def batch_reject_revisions(
    db: Session,
    *,
    file_record_id: UUID,
    current_user: User,
) -> int:
    return _batch_resolve_revisions(
        db,
        file_record_id=file_record_id,
        next_status="rejected",
        current_user=current_user,
    )


def get_revision_or_404(db: Session, revision_id: UUID) -> SegmentRevision:
    require_revisions_table()
    revision = (
        db.query(SegmentRevision)
        .options(
            joinedload(SegmentRevision.author),
            joinedload(SegmentRevision.resolved_by),
        )
        .filter(SegmentRevision.id == revision_id)
        .first()
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="Revision not found.")
    return revision


def _resolve_revision(
    db: Session,
    *,
    revision_id: UUID,
    next_status: str,
    current_user: User,
) -> SegmentRevision:
    normalized_status = _normalize_resolved_status(next_status)
    revision = get_revision_or_404(db, revision_id)
    if revision.status != "pending":
        raise HTTPException(status_code=409, detail="Revision has already been resolved.")

    pending_revisions = _list_pending_revisions_for_segment(db, revision.segment_id)
    anchor_revision, duplicate_revisions = _merge_pending_revisions(pending_revisions)

    for duplicate_revision in duplicate_revisions:
        db.delete(duplicate_revision)

    anchor_revision.status = normalized_status
    anchor_revision.resolved_by_id = current_user.id
    anchor_revision.resolved_at = datetime.utcnow()

    # 同步更新 segment 的 target_text：
    # 接受 → 保留 after_text；拒绝 → 恢复 before_text
    segment = db.query(Segment).filter(Segment.id == anchor_revision.segment_id).first()
    if segment is not None:
        if normalized_status == "accepted":
            segment.target_text = anchor_revision.after_text
        else:
            segment.target_text = anchor_revision.before_text

    db.commit()
    return get_revision_or_404(db, anchor_revision.id)


def _batch_resolve_revisions(
    db: Session,
    *,
    file_record_id: UUID,
    next_status: str,
    current_user: User,
) -> int:
    normalized_status = _normalize_resolved_status(next_status)
    require_revisions_table()

    pending_revisions = (
        db.query(SegmentRevision)
        .filter(
            SegmentRevision.file_record_id == file_record_id,
            SegmentRevision.status == "pending",
        )
        .order_by(
            SegmentRevision.segment_id.asc(),
            SegmentRevision.created_at.asc(),
            SegmentRevision.id.asc(),
        )
        .all()
    )
    if not pending_revisions:
        return 0

    resolved_at = datetime.utcnow()
    resolved_count = 0
    grouped_revisions: dict[UUID, list[SegmentRevision]] = {}
    for revision in pending_revisions:
        grouped_revisions.setdefault(revision.segment_id, []).append(revision)

    for segment_revisions in grouped_revisions.values():
        anchor_revision, duplicate_revisions = _merge_pending_revisions(segment_revisions)
        for duplicate_revision in duplicate_revisions:
            db.delete(duplicate_revision)

        anchor_revision.status = normalized_status
        anchor_revision.resolved_by_id = current_user.id
        anchor_revision.resolved_at = resolved_at

        # 同步更新 segment 的 target_text
        segment = db.query(Segment).filter(Segment.id == anchor_revision.segment_id).first()
        if segment is not None:
            if normalized_status == "accepted":
                segment.target_text = anchor_revision.after_text
            else:
                segment.target_text = anchor_revision.before_text

        resolved_count += 1

    db.commit()
    return resolved_count


def _list_pending_revisions_for_segment(
    db: Session,
    segment_id: UUID,
) -> list[SegmentRevision]:
    return (
        db.query(SegmentRevision)
        .filter(
            SegmentRevision.segment_id == segment_id,
            SegmentRevision.status == "pending",
        )
        .order_by(SegmentRevision.created_at.asc(), SegmentRevision.id.asc())
        .all()
    )


def _merge_pending_revisions(
    pending_revisions: list[SegmentRevision],
    *,
    after_text: str | None = None,
    source: str | None = None,
    author_id: UUID | None = None,
) -> tuple[SegmentRevision, list[SegmentRevision]]:
    if not pending_revisions:
        raise HTTPException(status_code=404, detail="Pending revision not found.")

    anchor_revision = pending_revisions[0]
    latest_revision = pending_revisions[-1]
    anchor_revision.after_text = latest_revision.after_text if after_text is None else after_text
    anchor_revision.source = latest_revision.source if source is None else source
    if author_id is not None:
        anchor_revision.author_id = author_id
    elif latest_revision.author_id is not None:
        anchor_revision.author_id = latest_revision.author_id
    return anchor_revision, pending_revisions[1:]


def _collapse_pending_revisions_for_read(
    revisions: list[SegmentRevision],
) -> list[SegmentRevision]:
    pending_groups: dict[UUID, list[SegmentRevision]] = {}
    for revision in revisions:
        if revision.status == "pending":
            pending_groups.setdefault(revision.segment_id, []).append(revision)

    hidden_revision_ids: set[UUID] = set()
    collapsed_revision_by_id: dict[UUID, SegmentRevision] = {}
    for group in pending_groups.values():
        if len(group) <= 1:
            continue

        ordered_group = sorted(group, key=lambda item: (item.created_at, item.id))
        anchor_revision, duplicate_revisions = _merge_pending_revisions(ordered_group)
        collapsed_revision_by_id[anchor_revision.id] = anchor_revision
        hidden_revision_ids.update(revision.id for revision in duplicate_revisions)

    collapsed_revisions: list[SegmentRevision] = []
    for revision in revisions:
        if revision.id in hidden_revision_ids:
            continue
        collapsed_revisions.append(collapsed_revision_by_id.get(revision.id, revision))

    return collapsed_revisions


def _normalize_revision_source(source: str) -> str:
    normalized_source = (source or "").strip().lower()
    if normalized_source in REVISION_SOURCE_VALUES:
        return normalized_source
    return "manual"


def _normalize_resolved_status(status: str) -> str:
    normalized_status = (status or "").strip().lower()
    if normalized_status not in REVISION_RESOLVED_STATUS_VALUES:
        raise HTTPException(status_code=400, detail="Unsupported revision status.")
    return normalized_status
