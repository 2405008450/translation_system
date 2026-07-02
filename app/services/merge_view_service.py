"""项目"合并视图"服务。

合并视图只持久化"哪些 file_records 组成一个编辑视图"这一分组关系（project_merge_views 表），
不为合并单独存储句段——句段仍通过 file_record_id 归属各自文件，保存/导出复用按文件的现有接口。

本模块只提供与 api.py 私有句段 helper 解耦的纯数据工具：
- file_ids JSON 文本的解析与归一化；
- 视图摘要 / 详情的序列化（不含句段，句段聚合读取在 api.py 内复用单文件 helper 实现）。
"""
from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import FileRecord, ProjectMergeView, User
from app.services.file_record_service import calculate_file_record_progress


class MergeViewError(ValueError):
    """合并视图数据校验错误（文件归属不合法、file_ids 为空等）。"""


def parse_file_ids(file_ids_text: str) -> list[UUID]:
    """把 file_ids JSON 文本解析为有序 UUID 列表；忽略非法条目。"""
    if not file_ids_text:
        return []
    try:
        raw_list = json.loads(file_ids_text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(raw_list, list):
        return []
    result: list[UUID] = []
    for raw in raw_list:
        try:
            result.append(UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            continue
    return result


def normalize_file_ids(file_ids: list[UUID]) -> list[UUID]:
    """去重保序。"""
    seen: set[UUID] = set()
    ordered: list[UUID] = []
    for file_id in file_ids:
        if file_id in seen:
            continue
        seen.add(file_id)
        ordered.append(file_id)
    return ordered


def serialize_file_ids(file_ids: list[UUID]) -> str:
    """序列化为 JSON 文本（与表列风格一致）。"""
    return json.dumps([str(fid) for fid in file_ids], ensure_ascii=False)


def load_view_file_records(
    db: Session,
    view: ProjectMergeView,
) -> list[FileRecord]:
    """按 view.file_ids 顺序加载归属该项目的 file_records；跳过已删除的文件。"""
    ordered_ids = parse_file_ids(view.file_ids)
    if not ordered_ids:
        return []
    files = (
        db.query(FileRecord)
        .filter(
            FileRecord.project_id == view.project_id,
            FileRecord.id.in_(ordered_ids),
        )
        .all()
    )
    file_by_id = {f.id: f for f in files}
    return [file_by_id[fid] for fid in ordered_ids if fid in file_by_id]


def serialize_merge_view_summary(
    db: Session,
    view: ProjectMergeView,
    *,
    current_user: User | None,
) -> dict:
    """视图摘要（列表用）：含文件数、creator、时间。"""
    ordered_ids = parse_file_ids(view.file_ids)
    existing_count = (
        db.query(FileRecord)
        .filter(
            FileRecord.project_id == view.project_id,
            FileRecord.id.in_(ordered_ids),
        )
        .count()
    ) if ordered_ids else 0
    creator_name = None
    if view.creator_id is not None:
        creator = db.query(User).filter(User.id == view.creator_id).first()
        if creator is not None:
            creator_name = (creator.nickname or creator.username) if hasattr(creator, "nickname") else creator.username
    return {
        "id": str(view.id),
        "project_id": str(view.project_id),
        "name": view.name,
        "file_ids": [str(fid) for fid in ordered_ids],
        "file_count": len(ordered_ids),
        "available_file_count": existing_count,
        "creator_id": str(view.creator_id) if view.creator_id else None,
        "creator_name": creator_name,
        "created_at": view.created_at.isoformat() if view.created_at else None,
        "updated_at": view.updated_at.isoformat() if view.updated_at else None,
    }


def serialize_merge_view_detail(
    db: Session,
    view: ProjectMergeView,
    files: list[FileRecord],
) -> dict:
    """视图详情：name + 按顺序的文件元数据 + 合计。"""
    file_payloads: list[dict] = []
    total_segments = 0
    for file_record in files:
        stats = _file_status_stats(db, file_record.id)
        total = int(stats.get("total", 0))
        confirmed = int(stats.get("confirmed", 0))
        total_segments += total
        file_payloads.append({
            "id": str(file_record.id),
            "filename": file_record.filename,
            "status": file_record.status,
            "total_segments": total,
            "status_stats": stats,
            "source_language": file_record.source_language,
            "target_language": file_record.target_language,
            "progress": calculate_file_record_progress(total, confirmed),
            "is_edit_locked": bool(file_record.active_operation),
        })
    ordered_ids = parse_file_ids(view.file_ids)
    language_pairs = summarize_language_pairs(files)
    return {
        "id": str(view.id),
        "project_id": str(view.project_id),
        "name": view.name,
        "file_ids": [str(fid) for fid in ordered_ids],
        "files": file_payloads,
        "total_files": len(file_payloads),
        "total_segments": total_segments,
        "is_mixed_language_pair": len(language_pairs) > 1,
        "language_pairs": language_pairs,
        "creator_id": str(view.creator_id) if view.creator_id else None,
        "created_at": view.created_at.isoformat() if view.created_at else None,
        "updated_at": view.updated_at.isoformat() if view.updated_at else None,
    }


def summarize_language_pairs(files: list[FileRecord]) -> list[dict]:
    """按文件当前语言对统计数量；缺失语言也作为独立组返回，方便前端提示。"""
    pair_counts: dict[tuple[str | None, str | None], int] = {}
    for file_record in files:
        key = (file_record.source_language, file_record.target_language)
        pair_counts[key] = pair_counts.get(key, 0) + 1
    return [
        {
            "source_language": source_language,
            "target_language": target_language,
            "file_count": file_count,
        }
        for (source_language, target_language), file_count in pair_counts.items()
    ]


def _file_status_stats(db: Session, file_record_id: UUID) -> dict[str, int]:
    """单文件句段状态统计（与 api.py 的 _get_segment_status_stats 保持一致口径）。"""
    from sqlalchemy import case, func

    from app.models import Segment
    from app.services.segment_status import segment_effective_status_conditions

    total = db.query(func.count(Segment.id)).filter(Segment.file_record_id == file_record_id).scalar() or 0
    if total == 0:
        return {"total": 0, "exact": 0, "fuzzy": 0, "none": 0, "confirmed": 0, "empty_target": 0}
    status_conditions = segment_effective_status_conditions(Segment)
    rows = db.query(
        func.count(case((status_conditions["exact"], 1), else_=None)).label("exact"),
        func.count(case((status_conditions["fuzzy"], 1), else_=None)).label("fuzzy"),
        func.count(case((status_conditions["none"], 1), else_=None)).label("none"),
        func.count(case((status_conditions["confirmed"], 1), else_=None)).label("confirmed"),
        func.count(
            case((func.coalesce(Segment.target_text, "") == "", 1), else_=None)
        ).label("empty_target"),
    ).filter(Segment.file_record_id == file_record_id).one()
    return {
        "total": int(total),
        "exact": int(rows.exact or 0),
        "fuzzy": int(rows.fuzzy or 0),
        "none": int(rows.none or 0),
        "confirmed": int(rows.confirmed or 0),
        "empty_target": int(rows.empty_target or 0),
    }
