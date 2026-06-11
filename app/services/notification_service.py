from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Notification


def create_operation_notification(
    db: Session,
    *,
    user_id: UUID,
    notification_type: str,
    title: str,
    body: str,
    project_id: UUID | None = None,
    file_record_id: UUID | None = None,
) -> Notification:
    notification = Notification(
        user_id=user_id,
        type=notification_type,
        title=title,
        body=body,
        project_id=project_id,
        file_record_id=file_record_id,
    )
    db.add(notification)
    db.flush()
    return notification


def build_resource_import_notification(
    *,
    resource_label: str,
    resource_name: str,
    filename: str,
    imported_rows: int,
    created_rows: int,
    updated_rows: int,
    skipped_empty_rows: int,
    skipped_header_rows: int,
    source_language: str,
    target_language: str,
) -> tuple[str, str]:
    title = f"{resource_label}导入完成：{resource_name}"
    skipped_rows = skipped_empty_rows + skipped_header_rows
    body = (
        f"文件：{filename}；语言对：{source_language} → {target_language}；"
        f"写入 {imported_rows} 条，新增 {created_rows} 条，覆盖 {updated_rows} 条，跳过 {skipped_rows} 条。"
    )
    return title, body


def build_save_to_tm_notification(
    *,
    filename: str,
    collection_name: str,
    created_count: int,
    updated_count: int,
    skipped_count: int,
    refreshed_count: int,
) -> tuple[str, str]:
    title = f"已保存到记忆库：{collection_name}"
    body = (
        f"任务：{filename}；新增 {created_count} 条，覆盖 {updated_count} 条，"
        f"跳过 {skipped_count} 条，刷新匹配 {refreshed_count} 条。"
    )
    return title, body
