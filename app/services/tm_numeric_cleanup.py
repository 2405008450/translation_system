from __future__ import annotations

from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models import MemoryEntry


# 仅匹配由 ASCII 阿拉伯数字和常见数字格式字符组成的源文。
# 只要出现中文、英文字母或其他语言文字，就不会命中清理规则。
TM_NUMERIC_ONLY_SOURCE_PATTERN = (
    r"^[[:space:]0-9,，.．+＋−–—()（）%％/／:：'’$＄¥￥€£-]+$"
)
TM_NUMERIC_ONLY_ALLOWED_CHARACTERS = frozenset(
    ",，.．+＋-−–—()（）%％/／:：'’$＄¥￥€£"
)


def is_numeric_only_tm_source(source_text: str | None) -> bool:
    """判断源文是否只包含阿拉伯数字及常见数字格式字符。"""
    if not source_text or not any("0" <= char <= "9" for char in source_text):
        return False
    return all(
        char.isspace()
        or "0" <= char <= "9"
        or char in TM_NUMERIC_ONLY_ALLOWED_CHARACTERS
        for char in source_text
    )


def _numeric_only_tm_source_condition():
    return and_(
        MemoryEntry.source_text.op("~")(r"[0-9]"),
        MemoryEntry.source_text.op("~")(TM_NUMERIC_ONLY_SOURCE_PATTERN),
    )


def count_numeric_only_tm_entries(db: Session, collection_id) -> int:
    return int(
        db.query(MemoryEntry.id)
        .filter(
            MemoryEntry.collection_id == collection_id,
            _numeric_only_tm_source_condition(),
        )
        .count()
    )


def list_numeric_only_tm_entry_examples(
    db: Session,
    collection_id,
    *,
    limit: int = 5,
) -> list[str]:
    safe_limit = min(max(limit, 0), 20)
    if safe_limit == 0:
        return []
    rows = (
        db.query(MemoryEntry.source_text)
        .filter(
            MemoryEntry.collection_id == collection_id,
            _numeric_only_tm_source_condition(),
        )
        .order_by(MemoryEntry.created_at.asc(), MemoryEntry.id.asc())
        .limit(safe_limit)
        .all()
    )
    return [row.source_text for row in rows]


def delete_numeric_only_tm_entries(db: Session, collection_id) -> int:
    return int(
        db.query(MemoryEntry)
        .filter(
            MemoryEntry.collection_id == collection_id,
            _numeric_only_tm_source_condition(),
        )
        .delete(synchronize_session=False)
    )
