"""修订跟踪服务

基于 segment_history 表计算修订标记，支持接受/拒绝修订操作。
"""
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Segment, SegmentHistory, User


def compute_diff_marks(old_text: str, new_text: str) -> list[dict[str, Any]]:
    """
    使用 Myers diff 算法计算两个文本之间的差异，返回修订标记列表。
    """
    if old_text == new_text:
        return []
    
    if not old_text:
        return [{
            "type": "insert",
            "text": new_text,
            "position": 0,
            "length": len(new_text),
        }]
    
    if not new_text:
        return [{
            "type": "delete",
            "text": old_text,
            "position": 0,
            "length": len(old_text),
        }]
    
    # Myers diff 算法
    old_chars = list(old_text)
    new_chars = list(new_text)
    ops = _myers_diff(old_chars, new_chars)
    
    # 转换为修订标记
    marks = []
    position = 0
    
    for op in ops:
        if op["type"] == "equal":
            position += len(op["text"])
        elif op["type"] == "insert":
            marks.append({
                "type": "insert",
                "text": op["text"],
                "position": position,
                "length": len(op["text"]),
            })
            position += len(op["text"])
        elif op["type"] == "delete":
            marks.append({
                "type": "delete",
                "text": op["text"],
                "position": position,
                "length": len(op["text"]),
            })
    
    return marks


def _myers_diff(old_arr: list[str], new_arr: list[str]) -> list[dict[str, Any]]:
    """Myers diff 算法实现"""
    n = len(old_arr)
    m = len(new_arr)
    max_d = n + m
    v: dict[int, int] = {1: 0}
    trace: list[dict[int, int]] = []
    
    for d in range(max_d + 1):
        trace.append(dict(v))
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v.get(k - 1, 0) < v.get(k + 1, 0)):
                x = v.get(k + 1, 0)
            else:
                x = v.get(k - 1, 0) + 1
            y = x - k
            while x < n and y < m and old_arr[x] == new_arr[y]:
                x += 1
                y += 1
            v[k] = x
            if x >= n and y >= m:
                return _backtrack(trace, old_arr, new_arr)
    
    return []


def _backtrack(
    trace: list[dict[int, int]],
    old_arr: list[str],
    new_arr: list[str],
) -> list[dict[str, Any]]:
    """回溯生成操作序列"""
    x = len(old_arr)
    y = len(new_arr)
    ops: list[dict[str, Any]] = []
    
    for d in range(len(trace) - 1, -1, -1):
        v = trace[d]
        k = x - y
        if k == -d or (k != d and v.get(k - 1, 0) < v.get(k + 1, 0)):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = v.get(prev_k, 0)
        prev_y = prev_x - prev_k
        
        while x > prev_x and y > prev_y:
            ops.insert(0, {"type": "equal", "text": old_arr[x - 1]})
            x -= 1
            y -= 1
        
        if d > 0:
            if x == prev_x:
                ops.insert(0, {"type": "insert", "text": new_arr[y - 1]})
                y -= 1
            else:
                ops.insert(0, {"type": "delete", "text": old_arr[x - 1]})
                x -= 1
    
    # 合并连续相同类型的操作
    merged: list[dict[str, Any]] = []
    for op in ops:
        if merged and merged[-1]["type"] == op["type"]:
            merged[-1]["text"] += op["text"]
        else:
            merged.append(dict(op))
    
    return merged


def get_segment_revisions(
    db: Session,
    file_record_id: UUID,
) -> dict[str, dict[str, Any]]:
    """
    获取文件所有句段的修订标记。
    
    对于每个句段，比较最近一次历史记录和当前文本的差异。
    """
    # 获取所有句段
    segments = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id)
        .all()
    )
    
    result: dict[str, dict[str, Any]] = {}
    
    for segment in segments:
        # 获取最近一条历史记录
        latest_history = (
            db.query(SegmentHistory)
            .filter(SegmentHistory.segment_id == segment.id)
            .order_by(SegmentHistory.created_at.desc())
            .first()
        )
        
        if not latest_history:
            continue
        
        # 获取上一条历史记录（用于对比）
        previous_history = (
            db.query(SegmentHistory)
            .filter(SegmentHistory.segment_id == segment.id)
            .filter(SegmentHistory.id != latest_history.id)
            .order_by(SegmentHistory.created_at.desc())
            .first()
        )
        
        if not previous_history:
            # 没有上一条记录，跳过
            continue
        
        # 计算差异
        marks = compute_diff_marks(
            previous_history.target_text,
            latest_history.target_text,
        )
        
        if not marks:
            continue
        
        # 添加作者和时间信息
        for i, mark in enumerate(marks):
            mark["id"] = str(uuid.uuid4())
            mark["author_id"] = str(latest_history.operator_id) if latest_history.operator_id else None
            mark["author_username"] = latest_history.operator.username if latest_history.operator else None
            mark["created_at"] = latest_history.created_at.isoformat()
        
        result[segment.sentence_id] = {
            "sentence_id": segment.sentence_id,
            "current_text": latest_history.target_text,
            "previous_text": previous_history.target_text,
            "marks": marks,
        }
    
    return result


def accept_revision(
    db: Session,
    file_record_id: UUID,
    sentence_id: str,
    mark_id: str,
    operator: User | None = None,
) -> bool:
    """
    接受单个修订。
    
    接受修订意味着将修订标记移除，保留当前文本。
    实际上就是创建一条新的历史记录，标记为"已接受修订"。
    """
    segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id)
        .filter(Segment.sentence_id == sentence_id)
        .first()
    )
    
    if not segment:
        return False
    
    # 创建新的历史记录，表示接受了修订
    history = SegmentHistory(
        segment_id=segment.id,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        source_text=segment.source_text,
        target_text=segment.target_text,
        status=segment.status,
        source="manual",
        confirm_type="接受修订",
        operator_id=operator.id if operator else None,
    )
    db.add(history)
    db.commit()
    
    return True


def reject_revision(
    db: Session,
    file_record_id: UUID,
    sentence_id: str,
    mark_id: str,
    operator: User | None = None,
) -> bool:
    """
    拒绝单个修订。
    
    拒绝修订意味着回退到上一个版本的文本。
    """
    segment = (
        db.query(Segment)
        .filter(Segment.file_record_id == file_record_id)
        .filter(Segment.sentence_id == sentence_id)
        .first()
    )
    
    if not segment:
        return False
    
    # 获取上一条历史记录
    histories = (
        db.query(SegmentHistory)
        .filter(SegmentHistory.segment_id == segment.id)
        .order_by(SegmentHistory.created_at.desc())
        .limit(2)
        .all()
    )
    
    if len(histories) < 2:
        return False
    
    previous_text = histories[1].target_text
    
    # 更新句段文本
    segment.target_text = previous_text
    segment.source = "manual"
    
    # 创建新的历史记录
    history = SegmentHistory(
        segment_id=segment.id,
        file_record_id=file_record_id,
        sentence_id=sentence_id,
        source_text=segment.source_text,
        target_text=previous_text,
        status=segment.status,
        source="manual",
        confirm_type="拒绝修订",
        operator_id=operator.id if operator else None,
    )
    db.add(history)
    db.commit()
    
    return True


def accept_all_revisions(
    db: Session,
    file_record_id: UUID,
    author_id: str | None = None,
    operator: User | None = None,
) -> int:
    """
    接受所有修订（可选按作者筛选）。
    返回接受的修订数量。
    """
    revisions = get_segment_revisions(db, file_record_id)
    count = 0
    
    for sentence_id, revision in revisions.items():
        for mark in revision["marks"]:
            if author_id and mark.get("author_id") != author_id:
                continue
            if accept_revision(db, file_record_id, sentence_id, mark["id"], operator):
                count += 1
                break  # 每个句段只需要接受一次
    
    return count


def reject_all_revisions(
    db: Session,
    file_record_id: UUID,
    author_id: str | None = None,
    operator: User | None = None,
) -> int:
    """
    拒绝所有修订（可选按作者筛选）。
    返回拒绝的修订数量。
    """
    revisions = get_segment_revisions(db, file_record_id)
    count = 0
    
    for sentence_id, revision in revisions.items():
        for mark in revision["marks"]:
            if author_id and mark.get("author_id") != author_id:
                continue
            if reject_revision(db, file_record_id, sentence_id, mark["id"], operator):
                count += 1
                break  # 每个句段只需要拒绝一次
    
    return count
