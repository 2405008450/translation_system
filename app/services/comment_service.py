from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.auth import serialize_user
from app.database import engine
from app.models import Segment, SegmentComment, User
from app.services.normalizer import normalize_text_preserve_lines


COMMENTS_TABLE_MISSING_MESSAGE = (
    "segment_comments 表不存在，请先执行 scripts/create_segment_comments_table.sql。"
)

COMMENT_STATUS_VALUES = {"open", "resolved"}
COMMENT_ANCHOR_MODE_VALUES = {"sentence", "range"}


def comments_table_exists() -> bool:
    try:
        return inspect(engine).has_table(SegmentComment.__tablename__)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail=f"无法检查 segment_comments 表状态：{exc}") from exc


def require_comments_table() -> None:
    if not comments_table_exists():
        raise HTTPException(status_code=503, detail=COMMENTS_TABLE_MISSING_MESSAGE)


def serialize_segment_comment(comment: SegmentComment) -> dict:
    return {
        "id": str(comment.id),
        "file_record_id": str(comment.file_record_id),
        "segment_id": str(comment.segment_id) if comment.segment_id else None,
        "sentence_id": comment.segment.sentence_id if comment.segment else None,
        "anchor_mode": comment.anchor_mode,
        "range_start_offset": comment.range_start_offset,
        "range_end_offset": comment.range_end_offset,
        "anchor_text": comment.anchor_text,
        "body": comment.body,
        "author": serialize_user(comment.author),
        "parent_id": str(comment.parent_id) if comment.parent_id else None,
        "status": comment.status,
        "created_at": comment.created_at.isoformat(),
        "updated_at": comment.updated_at.isoformat(),
        "resolved_at": comment.resolved_at.isoformat() if comment.resolved_at else None,
    }


def list_segment_comments_for_file_record(
    db: Session,
    file_record_id: UUID,
) -> list[SegmentComment]:
    require_comments_table()
    return (
        db.query(SegmentComment)
        .options(
            joinedload(SegmentComment.author),
            joinedload(SegmentComment.segment),
        )
        .filter(SegmentComment.file_record_id == file_record_id)
        .order_by(SegmentComment.created_at.asc(), SegmentComment.id.asc())
        .all()
    )


def get_segment_comment_or_404(db: Session, comment_id: UUID) -> SegmentComment:
    require_comments_table()
    comment = (
        db.query(SegmentComment)
        .options(
            joinedload(SegmentComment.author),
            joinedload(SegmentComment.segment),
        )
        .filter(SegmentComment.id == comment_id)
        .first()
    )
    if comment is None:
        raise HTTPException(status_code=404, detail="批注不存在。")
    return comment


def create_segment_comment(
    db: Session,
    *,
    file_record_id: UUID,
    sentence_id: str | None,
    segment_id: UUID | None,
    anchor_mode: str,
    range_start_offset: int | None,
    range_end_offset: int | None,
    anchor_text: str | None,
    body: str,
    author: User,
) -> SegmentComment:
    require_comments_table()
    segment = _resolve_segment_for_anchor(
        db,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        segment_id=segment_id,
    )
    anchor_mode, range_start_offset, range_end_offset, anchor_text = _normalize_anchor(
        anchor_mode=anchor_mode,
        range_start_offset=range_start_offset,
        range_end_offset=range_end_offset,
        anchor_text=anchor_text,
    )
    normalized_body = _normalize_comment_body(body)

    comment = SegmentComment(
        file_record_id=file_record_id,
        segment_id=segment.id,
        anchor_mode=anchor_mode,
        range_start_offset=range_start_offset,
        range_end_offset=range_end_offset,
        anchor_text=anchor_text,
        body=normalized_body,
        author_id=author.id,
        status="open",
    )
    db.add(comment)
    db.commit()
    return get_segment_comment_or_404(db, comment.id)


def create_segment_comment_reply(
    db: Session,
    *,
    comment_id: UUID,
    body: str,
    author: User,
) -> SegmentComment:
    parent = get_segment_comment_or_404(db, comment_id)
    normalized_body = _normalize_comment_body(body)

    comment = SegmentComment(
        file_record_id=parent.file_record_id,
        segment_id=parent.segment_id,
        anchor_mode=parent.anchor_mode,
        range_start_offset=parent.range_start_offset,
        range_end_offset=parent.range_end_offset,
        anchor_text=parent.anchor_text,
        body=normalized_body,
        author_id=author.id,
        parent_id=parent.id,
        status="open",
    )
    db.add(comment)
    db.commit()
    return get_segment_comment_or_404(db, comment.id)


def update_segment_comment(
    db: Session,
    *,
    comment_id: UUID,
    body: str | None,
    status: str | None,
    current_user: User,
) -> SegmentComment:
    comment = get_segment_comment_or_404(db, comment_id)
    _require_comment_write_access(comment, current_user)

    changed = False

    if body is not None:
        comment.body = _normalize_comment_body(body)
        changed = True

    if status is not None:
        if status not in COMMENT_STATUS_VALUES:
            raise HTTPException(status_code=400, detail="不支持的批注状态。")
        comment.status = status
        comment.resolved_at = datetime.utcnow() if status == "resolved" else None
        changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="没有可更新的批注内容。")

    db.commit()
    return get_segment_comment_or_404(db, comment.id)


def delete_segment_comment(
    db: Session,
    *,
    comment_id: UUID,
    current_user: User,
) -> None:
    comment = get_segment_comment_or_404(db, comment_id)
    _require_comment_write_access(comment, current_user)
    db.delete(comment)
    db.commit()


def _resolve_segment_for_anchor(
    db: Session,
    *,
    file_record_id: UUID,
    sentence_id: str | None,
    segment_id: UUID | None,
) -> Segment:
    query = db.query(Segment).filter(Segment.file_record_id == file_record_id)
    if segment_id is not None:
        query = query.filter(Segment.id == segment_id)
    elif sentence_id:
        query = query.filter(Segment.sentence_id == sentence_id)
    else:
        raise HTTPException(status_code=400, detail="创建批注时必须提供 sentence_id 或 segment_id。")

    segment = query.first()
    if segment is None:
        raise HTTPException(status_code=404, detail="批注锚点对应的句段不存在。")
    return segment


def _normalize_anchor(
    *,
    anchor_mode: str,
    range_start_offset: int | None,
    range_end_offset: int | None,
    anchor_text: str | None,
) -> tuple[str, int | None, int | None, str | None]:
    normalized_anchor_mode = (anchor_mode or "").strip().lower()
    if normalized_anchor_mode not in COMMENT_ANCHOR_MODE_VALUES:
        raise HTTPException(status_code=400, detail="不支持的批注锚点模式。")

    if normalized_anchor_mode == "sentence":
        return "sentence", None, None, None

    if range_start_offset is None or range_end_offset is None:
        raise HTTPException(status_code=400, detail="范围批注必须提供起止偏移。")
    if range_start_offset < 0 or range_end_offset <= range_start_offset:
        raise HTTPException(status_code=400, detail="范围批注偏移无效。")

    normalized_anchor_text = normalize_text_preserve_lines(anchor_text or "")
    if not normalized_anchor_text:
        raise HTTPException(status_code=400, detail="范围批注必须提供选中文本。")

    return "range", range_start_offset, range_end_offset, normalized_anchor_text


def _normalize_comment_body(body: str) -> str:
    normalized_body = normalize_text_preserve_lines(body or "")
    if not normalized_body:
        raise HTTPException(status_code=400, detail="批注内容不能为空。")
    return normalized_body


def _require_comment_write_access(comment: SegmentComment, current_user: User) -> None:
    if current_user.role == "admin" or comment.author_id == current_user.id:
        return
    raise HTTPException(status_code=403, detail="只能修改自己创建的批注。")
