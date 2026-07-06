from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.auth import get_user_display_name, is_admin_role, serialize_user
from app.database import engine
from app.models import FileRecord, IssueMarker, Project, User
from app.services.normalizer import normalize_text_preserve_lines


ISSUE_MARKERS_TABLE_MISSING_MESSAGE = (
    "issue_markers 表不存在，请先执行 scripts/create_issue_markers.sql 或 scripts/init_db.sql。"
)

ISSUE_CATEGORY_VALUES = {"bug", "translation", "format", "performance", "data", "other"}
ISSUE_SEVERITY_VALUES = {"low", "medium", "high", "critical"}
ISSUE_STATUS_VALUES = {"open", "resolved"}


def issue_markers_table_exists() -> bool:
    try:
        return inspect(engine).has_table(IssueMarker.__tablename__)
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail=f"无法检查 issue_markers 表状态：{exc}") from exc


def require_issue_markers_table() -> None:
    if not issue_markers_table_exists():
        raise HTTPException(status_code=503, detail=ISSUE_MARKERS_TABLE_MISSING_MESSAGE)


def serialize_issue_marker(marker: IssueMarker) -> dict:
    return {
        "id": str(marker.id),
        "project_id": str(marker.project_id),
        "project_name": marker.project.name if marker.project else None,
        "file_record_id": str(marker.file_record_id) if marker.file_record_id else None,
        "file_record_name": marker.file_record.filename if marker.file_record else None,
        "title": marker.title,
        "description": marker.description,
        "category": marker.category,
        "severity": marker.severity,
        "status": marker.status,
        "page_url": marker.page_url,
        "user_agent": marker.user_agent,
        "reporter": serialize_user(marker.reporter) if marker.reporter else None,
        "reporter_name": get_user_display_name(marker.reporter),
        "resolved_by": serialize_user(marker.resolved_by) if marker.resolved_by else None,
        "resolved_by_name": get_user_display_name(marker.resolved_by),
        "created_at": marker.created_at.isoformat(),
        "updated_at": marker.updated_at.isoformat(),
        "resolved_at": marker.resolved_at.isoformat() if marker.resolved_at else None,
    }


def list_issue_markers_for_project(
    db: Session,
    project_id: UUID,
    *,
    status: str | None = None,
    file_record_id: UUID | None = None,
) -> list[IssueMarker]:
    require_issue_markers_table()
    query = (
        db.query(IssueMarker)
        .options(
            joinedload(IssueMarker.project),
            joinedload(IssueMarker.file_record),
            joinedload(IssueMarker.reporter),
            joinedload(IssueMarker.resolved_by),
        )
        .filter(IssueMarker.project_id == project_id)
    )
    if status:
        if status not in ISSUE_STATUS_VALUES:
            raise HTTPException(status_code=400, detail="不支持的问题状态。")
        query = query.filter(IssueMarker.status == status)
    if file_record_id:
        query = query.filter(IssueMarker.file_record_id == file_record_id)
    return query.order_by(IssueMarker.created_at.desc(), IssueMarker.id.desc()).all()


def get_issue_marker_or_404(db: Session, marker_id: UUID) -> IssueMarker:
    require_issue_markers_table()
    marker = (
        db.query(IssueMarker)
        .options(
            joinedload(IssueMarker.project),
            joinedload(IssueMarker.file_record),
            joinedload(IssueMarker.reporter),
            joinedload(IssueMarker.resolved_by),
        )
        .filter(IssueMarker.id == marker_id)
        .first()
    )
    if marker is None:
        raise HTTPException(status_code=404, detail="问题标记不存在。")
    return marker


def create_issue_marker(
    db: Session,
    *,
    project_id: UUID,
    file_record_id: UUID | None,
    title: str | None,
    description: str,
    category: str,
    severity: str,
    page_url: str | None,
    user_agent: str | None,
    reporter: User,
) -> IssueMarker:
    require_issue_markers_table()
    project = _get_project_or_404(db, project_id)
    file_record = _resolve_project_file_record(
        db,
        project_id=project.id,
        file_record_id=file_record_id,
    )
    normalized_description = _normalize_required_text(description, "问题描述不能为空。")
    normalized_category = _normalize_category(category)
    normalized_severity = _normalize_severity(severity)
    normalized_title = _normalize_title(title, normalized_description)

    marker = IssueMarker(
        project_id=project.id,
        file_record_id=file_record.id if file_record else None,
        title=normalized_title,
        description=normalized_description,
        category=normalized_category,
        severity=normalized_severity,
        status="open",
        page_url=_normalize_optional_text(page_url),
        user_agent=_normalize_optional_text(user_agent),
        reporter_id=reporter.id,
    )
    db.add(marker)
    db.commit()
    return get_issue_marker_or_404(db, marker.id)


def update_issue_marker(
    db: Session,
    *,
    marker_id: UUID,
    title: str | None,
    description: str | None,
    category: str | None,
    severity: str | None,
    status: str | None,
    current_user: User,
) -> IssueMarker:
    marker = get_issue_marker_or_404(db, marker_id)
    _require_marker_write_access(marker, current_user)

    changed = False

    if description is not None:
        marker.description = _normalize_required_text(description, "问题描述不能为空。")
        changed = True

    if title is not None:
        marker.title = _normalize_title(title, marker.description)
        changed = True

    if category is not None:
        marker.category = _normalize_category(category)
        changed = True

    if severity is not None:
        marker.severity = _normalize_severity(severity)
        changed = True

    if status is not None:
        normalized_status = _normalize_status(status)
        marker.status = normalized_status
        if normalized_status == "resolved":
            marker.resolved_at = datetime.now()
            marker.resolved_by_id = current_user.id
        else:
            marker.resolved_at = None
            marker.resolved_by_id = None
        changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="没有可更新的问题标记内容。")

    db.commit()
    return get_issue_marker_or_404(db, marker.id)


def delete_issue_marker(
    db: Session,
    *,
    marker_id: UUID,
    current_user: User,
) -> None:
    marker = get_issue_marker_or_404(db, marker_id)
    _require_marker_write_access(marker, current_user)
    db.delete(marker)
    db.commit()


def _get_project_or_404(db: Session, project_id: UUID) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在。")
    return project


def _resolve_project_file_record(
    db: Session,
    *,
    project_id: UUID,
    file_record_id: UUID | None,
) -> FileRecord | None:
    if file_record_id is None:
        return None
    file_record = (
        db.query(FileRecord)
        .filter(
            FileRecord.id == file_record_id,
            FileRecord.project_id == project_id,
        )
        .first()
    )
    if file_record is None:
        raise HTTPException(status_code=404, detail="要标记的任务不属于当前项目。")
    return file_record


def _normalize_required_text(value: str, empty_message: str) -> str:
    normalized = normalize_text_preserve_lines(value or "")
    if not normalized:
        raise HTTPException(status_code=400, detail=empty_message)
    return normalized


def _normalize_optional_text(value: str | None) -> str | None:
    normalized = normalize_text_preserve_lines(value or "")
    return normalized or None


def _normalize_title(title: str | None, description: str) -> str:
    normalized_title = normalize_text_preserve_lines(title or "").replace("\n", " ").strip()
    if normalized_title:
        return normalized_title[:160]
    first_line = description.splitlines()[0].strip()
    return first_line[:80] or "未命名问题"


def _normalize_category(category: str | None) -> str:
    normalized = (category or "other").strip().lower()
    if normalized not in ISSUE_CATEGORY_VALUES:
        raise HTTPException(status_code=400, detail="不支持的问题类型。")
    return normalized


def _normalize_severity(severity: str | None) -> str:
    normalized = (severity or "medium").strip().lower()
    if normalized not in ISSUE_SEVERITY_VALUES:
        raise HTTPException(status_code=400, detail="不支持的问题严重程度。")
    return normalized


def _normalize_status(status: str | None) -> str:
    normalized = (status or "open").strip().lower()
    if normalized not in ISSUE_STATUS_VALUES:
        raise HTTPException(status_code=400, detail="不支持的问题状态。")
    return normalized


def _require_marker_write_access(marker: IssueMarker, current_user: User) -> None:
    if is_admin_role(current_user.role) or marker.reporter_id == current_user.id:
        return
    raise HTTPException(status_code=403, detail="只能修改自己创建的问题标记。")
