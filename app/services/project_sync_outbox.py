"""项目重复句段同步 outbox。

保存/确认接口在同一事务内把 (project_id, 语言对, source_hash) 写入 outbox
（唯一键去重合并），提交后由 segment-sync worker 批量消费：对每个 hash 做一次
收敛同步，再通过 Redis 发布受影响文件的变更事件。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models import FileRecord, ProjectSegmentSyncOutbox, Segment, User
from app.services.normalizer import build_source_hash, normalize_text
from app.services.project_segment_sync import (
    ProjectSegmentSyncSummary,
    sync_project_segments_for_hash,
)
from app.services.segment_events import publish_segment_changes


logger = logging.getLogger(__name__)

PROJECT_SYNC_OUTBOX_BATCH_SIZE = 50
PROJECT_SYNC_OUTBOX_MAX_BATCHES_PER_RUN = 20
PROJECT_SYNC_OUTBOX_MAX_ATTEMPTS = 5
PROJECT_SYNC_OUTBOX_COMPLETED_RETENTION = timedelta(days=7)


def _project_sync_confirmed_only() -> bool:
    return bool(getattr(get_settings(), "project_sync_confirmed_only", True))


def select_segments_for_project_sync(segments: list[Segment]) -> list[Segment]:
    """按触发策略筛选需要项目同步的句段（默认仅确认触发）。"""
    confirmed_only = _project_sync_confirmed_only()
    selected: list[Segment] = []
    for segment in segments:
        if segment is None or segment.project_sync_disabled:
            continue
        if not normalize_text(segment.target_text):
            continue
        if confirmed_only and segment.status != "confirmed":
            continue
        selected.append(segment)
    return selected


def enqueue_project_segment_sync(
    db: Session,
    *,
    file_record: FileRecord,
    segments: list[Segment],
    current_user: User | None = None,
) -> int:
    """把句段的同步任务合并写入 outbox；须与业务改动同事务提交。"""
    if file_record.project_id is None:
        return 0
    eligible = select_segments_for_project_sync(segments)
    if not eligible:
        return 0

    source_language = (file_record.source_language or "").strip()
    target_language = (file_record.target_language or "").strip()
    now = datetime.now()
    rows: dict[str, dict] = {}
    for segment in eligible:
        source_hash = segment.source_hash or build_source_hash(segment.source_text)
        if not source_hash:
            continue
        segment.source_hash = segment.source_hash or source_hash
        rows[source_hash] = {
            "project_id": file_record.project_id,
            "source_language": source_language,
            "target_language": target_language,
            "source_hash": source_hash,
            "source_file_record_id": file_record.id,
            "source_segment_id": segment.id,
            "requested_by_id": current_user.id if current_user else None,
            "status": "pending",
            "attempt_count": 0,
            "error_message": "",
            "last_enqueued_at": now,
            "updated_at": now,
        }
    if not rows:
        return 0

    if db.get_bind().dialect.name == "postgresql":
        stmt = pg_insert(ProjectSegmentSyncOutbox).values(list(rows.values()))
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                ProjectSegmentSyncOutbox.project_id,
                ProjectSegmentSyncOutbox.source_language,
                ProjectSegmentSyncOutbox.target_language,
                ProjectSegmentSyncOutbox.source_hash,
            ],
            set_={
                "status": "pending",
                "attempt_count": 0,
                "error_message": "",
                "source_file_record_id": stmt.excluded.source_file_record_id,
                "source_segment_id": stmt.excluded.source_segment_id,
                "requested_by_id": stmt.excluded.requested_by_id,
                "last_enqueued_at": stmt.excluded.last_enqueued_at,
                "processed_at": None,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        db.execute(stmt)
    else:  # 测试环境等非 PostgreSQL 数据库使用等价 ORM 更新。
        for row_values in rows.values():
            existing = (
                db.query(ProjectSegmentSyncOutbox)
                .filter(
                    ProjectSegmentSyncOutbox.project_id == row_values["project_id"],
                    ProjectSegmentSyncOutbox.source_language == row_values["source_language"],
                    ProjectSegmentSyncOutbox.target_language == row_values["target_language"],
                    ProjectSegmentSyncOutbox.source_hash == row_values["source_hash"],
                )
                .first()
            )
            if existing is None:
                db.add(ProjectSegmentSyncOutbox(**row_values))
                continue
            for field_name, value in row_values.items():
                setattr(existing, field_name, value)
            existing.processed_at = None
    return len(rows)


def process_project_sync_outbox(db: Session, *, batch_size: int = PROJECT_SYNC_OUTBOX_BATCH_SIZE) -> int:
    """处理一批 outbox 任务并提交；返回处理行数。"""
    rows = (
        db.query(ProjectSegmentSyncOutbox)
        .filter(ProjectSegmentSyncOutbox.status == "pending")
        .order_by(ProjectSegmentSyncOutbox.last_enqueued_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .all()
    )
    if not rows:
        return 0

    affected_file_ids: set[UUID] = set()
    now = datetime.now()
    for row in rows:
        current_user = (
            db.query(User).filter(User.id == row.requested_by_id).first()
            if row.requested_by_id is not None
            else None
        )
        try:
            # SAVEPOINT 隔离单条失败，避免中止整批事务。
            with db.begin_nested():
                summary: ProjectSegmentSyncSummary = sync_project_segments_for_hash(
                    db,
                    project_id=row.project_id,
                    source_language=row.source_language or "",
                    target_language=row.target_language or "",
                    source_hash=row.source_hash,
                    current_user=current_user,
                )
            affected_file_ids.update(summary.affected_file_ids)
            row.status = "completed"
            row.error_message = ""
            row.processed_at = now
            if summary.filled_count or summary.updated_count or summary.conflict_count:
                logger.info(
                    "project sync outbox processed project=%s hash=%s filled=%s updated=%s conflicts=%s",
                    row.project_id,
                    row.source_hash[:12],
                    summary.filled_count,
                    summary.updated_count,
                    summary.conflict_count,
                )
        except Exception as exc:
            row.attempt_count = int(row.attempt_count or 0) + 1
            row.error_message = str(exc)[:2000]
            row.status = (
                "failed"
                if row.attempt_count >= PROJECT_SYNC_OUTBOX_MAX_ATTEMPTS
                else "pending"
            )
            logger.exception(
                "project sync outbox item failed project=%s hash=%s attempt=%s",
                row.project_id,
                row.source_hash[:12],
                row.attempt_count,
            )

    db.commit()
    if affected_file_ids:
        publish_segment_changes(affected_file_ids)
    return len(rows)


def _prune_completed_outbox_rows(db: Session) -> None:
    cutoff = datetime.now() - PROJECT_SYNC_OUTBOX_COMPLETED_RETENTION
    deleted = (
        db.query(ProjectSegmentSyncOutbox)
        .filter(
            ProjectSegmentSyncOutbox.status == "completed",
            ProjectSegmentSyncOutbox.processed_at.isnot(None),
            ProjectSegmentSyncOutbox.processed_at < cutoff,
        )
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()


def run_project_sync_outbox_once() -> None:
    """后台 worker 入口：循环消费直到清空或达到批次上限。"""
    with SessionLocal() as db:
        try:
            for _ in range(PROJECT_SYNC_OUTBOX_MAX_BATCHES_PER_RUN):
                processed = process_project_sync_outbox(db)
                if processed < PROJECT_SYNC_OUTBOX_BATCH_SIZE:
                    break
            _prune_completed_outbox_rows(db)
        except Exception:
            db.rollback()
            logger.exception("project sync outbox run failed")
