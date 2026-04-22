"""句段历史记录服务"""
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Segment, SegmentHistory, User


# 来源到确认类型的映射
SOURCE_TO_CONFIRM_TYPE = {
    "tm": "TM填充",
    "manual": "人工输入",
    "llm": "AI修正",
}


def get_confirm_type(source: str) -> str:
    """根据来源获取确认类型"""
    return SOURCE_TO_CONFIRM_TYPE.get(source, source)


def create_segment_history(
    db: Session,
    segment: Segment,
    operator: User | None = None,
) -> SegmentHistory:
    """创建一条句段历史记录"""
    history = SegmentHistory(
        segment_id=segment.id,
        file_record_id=segment.file_record_id,
        sentence_id=segment.sentence_id,
        source_text=segment.source_text,
        target_text=segment.target_text,
        status=segment.status,
        source=segment.source,
        confirm_type=get_confirm_type(segment.source),
        operator_id=operator.id if operator else None,
    )
    db.add(history)
    db.flush()
    return history


def list_segment_history(
    db: Session,
    segment_id: UUID,
    limit: int = 50,
) -> list[SegmentHistory]:
    """获取句段的历史记录列表，按时间倒序"""
    return (
        db.query(SegmentHistory)
        .filter(SegmentHistory.segment_id == segment_id)
        .order_by(SegmentHistory.created_at.desc())
        .limit(limit)
        .all()
    )


def serialize_segment_history(history: SegmentHistory) -> dict:
    """序列化历史记录"""
    return {
        "id": str(history.id),
        "segment_id": str(history.segment_id),
        "file_record_id": str(history.file_record_id),
        "sentence_id": history.sentence_id,
        "source_text": history.source_text,
        "target_text": history.target_text,
        "status": history.status,
        "source": history.source,
        "confirm_type": history.confirm_type,
        "operator": {
            "id": str(history.operator.id),
            "username": history.operator.username,
        } if history.operator else None,
        "created_at": history.created_at.isoformat(),
    }
