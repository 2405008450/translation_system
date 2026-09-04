"""项目重复句段同步 outbox。

保存/确认接口在同一事务内把 (project_id, 语言对, source_hash) 写入 outbox
（唯一键去重合并），提交后由 segment-sync worker 批量消费：对每个 hash 做一次
收敛同步，再通过 Redis 发布受影响文件的变更事件。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import FileRecord, ProjectSegmentSyncOutbox, Segment, User
from app.services.normalizer import build_source_hash, normalize_text
from app.services.project_segment_sync import (
    ProjectSegmentSyncSummary,
    sync_project_segments_for_hash,
    sync_project_segments_from_source,
)
from app.services.segment_events import publish_segment_changes


logger = logging.getLogger(__name__)

PROJECT_SYNC_OUTBOX_BATCH_SIZE = 50
PROJECT_SYNC_OUTBOX_MAX_BATCHES_PER_RUN = 20
PROJECT_SYNC_OUTBOX_MAX_ATTEMPTS = 5
PROJECT_SYNC_OUTBOX_COMPLETED_RETENTION = timedelta(days=7)
PROJECT_SYNC_OUTBOX_PROCESSING_LEASE = timedelta(minutes=5)
PROJECT_SYNC_TRIGGER_CONFIRMED = "confirmed"
PROJECT_SYNC_TRIGGER_BLUR = "blur"


@dataclass(frozen=True)
class _ProjectSyncOutboxClaim:
    id: UUID
    project_id: UUID
    source_language: str
    target_language: str
    source_hash: str
    source_segment_id: UUID | None
    source_version: int | None
    trigger_kind: str
    generation: int
    requested_by_id: UUID | None
    claimed_at: datetime
    last_enqueued_at: datetime


def _project_sync_confirmed_only() -> bool:
    return bool(getattr(get_settings(), "project_sync_confirmed_only", True))


def project_sync_blur_enabled_for_project(project_id: UUID | None) -> bool:
    """判断项目是否在显式失焦同步白名单中。"""
    if project_id is None:
        return False
    configured = str(getattr(get_settings(), "project_sync_blur_project_ids", "") or "")
    allowed = {item.strip().lower() for item in configured.split(",") if item.strip()}
    return "*" in allowed or str(project_id).lower() in allowed


def select_segments_for_project_sync(
    segments: list[Segment],
    *,
    trigger_kind: str = PROJECT_SYNC_TRIGGER_CONFIRMED,
) -> list[Segment]:
    """按触发策略筛选需要项目同步的句段（默认仅确认触发）。"""
    confirmed_only = _project_sync_confirmed_only()
    selected: list[Segment] = []
    for segment in segments:
        if segment is None or segment.project_sync_disabled:
            continue
        if not normalize_text(segment.target_text):
            continue
        if trigger_kind != PROJECT_SYNC_TRIGGER_BLUR and confirmed_only and segment.status != "confirmed":
            continue
        selected.append(segment)
    return selected


def enqueue_project_segment_sync(
    db: Session,
    *,
    file_record: FileRecord,
    segments: list[Segment],
    current_user: User | None = None,
    trigger_kind: str = PROJECT_SYNC_TRIGGER_CONFIRMED,
) -> int:
    """把句段的同步任务合并写入 outbox；须与业务改动同事务提交。"""
    if file_record.project_id is None:
        return 0
    if trigger_kind not in {PROJECT_SYNC_TRIGGER_CONFIRMED, PROJECT_SYNC_TRIGGER_BLUR}:
        raise ValueError(f"unsupported project sync trigger: {trigger_kind}")
    if trigger_kind == PROJECT_SYNC_TRIGGER_BLUR and not project_sync_blur_enabled_for_project(file_record.project_id):
        return 0
    eligible = select_segments_for_project_sync(segments, trigger_kind=trigger_kind)
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
            "source_version": int(segment.version or 1),
            "trigger_kind": trigger_kind,
            "generation": 1,
            "requested_by_id": current_user.id if current_user else None,
            "status": "pending",
            "attempt_count": 0,
            "error_message": "",
            "last_enqueued_at": now,
            "updated_at": now,
        }
    if not rows:
        return 0

    # 多个确认事务可能同时写入相同项目的重复句段。统一按唯一键排序，
    # 让 PostgreSQL 以相同顺序获取 ON CONFLICT 对应的索引/行锁。
    ordered_rows = [rows[source_hash] for source_hash in sorted(rows)]

    if db.get_bind().dialect.name == "postgresql":
        stmt = pg_insert(ProjectSegmentSyncOutbox).values(ordered_rows)
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
                "source_version": stmt.excluded.source_version,
                "trigger_kind": stmt.excluded.trigger_kind,
                "generation": ProjectSegmentSyncOutbox.generation + 1,
                "requested_by_id": stmt.excluded.requested_by_id,
                "last_enqueued_at": stmt.excluded.last_enqueued_at,
                "processed_at": None,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        db.execute(stmt)
    else:  # 测试环境等非 PostgreSQL 数据库使用等价 ORM 更新。
        for row_values in ordered_rows:
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
            next_generation = int(existing.generation or 1) + 1
            for field_name, value in row_values.items():
                setattr(existing, field_name, value)
            existing.generation = next_generation
            existing.processed_at = None
    return len(rows)


def _claim_project_sync_outbox_rows(
    db: Session,
    *,
    batch_size: int,
) -> list[_ProjectSyncOutboxClaim]:
    """短事务认领任务并立即释放 outbox 行锁。"""
    claimed_at = datetime.now()
    stale_before = claimed_at - PROJECT_SYNC_OUTBOX_PROCESSING_LEASE
    rows = (
        db.query(ProjectSegmentSyncOutbox)
        .filter(
            or_(
                ProjectSegmentSyncOutbox.status == "pending",
                and_(
                    ProjectSegmentSyncOutbox.status == "processing",
                    ProjectSegmentSyncOutbox.updated_at < stale_before,
                ),
            )
        )
        .order_by(
            ProjectSegmentSyncOutbox.last_enqueued_at.asc(),
            ProjectSegmentSyncOutbox.id.asc(),
        )
        .limit(batch_size)
        .with_for_update(skip_locked=True)
        .all()
    )
    if not rows:
        db.rollback()
        return []

    claims: list[_ProjectSyncOutboxClaim] = []
    for row in rows:
        row.status = "processing"
        row.updated_at = claimed_at
        claims.append(
            _ProjectSyncOutboxClaim(
                id=row.id,
                project_id=row.project_id,
                source_language=row.source_language or "",
                target_language=row.target_language or "",
                source_hash=row.source_hash,
                source_segment_id=row.source_segment_id,
                source_version=row.source_version,
                trigger_kind=row.trigger_kind or PROJECT_SYNC_TRIGGER_CONFIRMED,
                generation=int(row.generation or 1),
                requested_by_id=row.requested_by_id,
                claimed_at=claimed_at,
                last_enqueued_at=row.last_enqueued_at,
            )
        )
    db.commit()
    return claims


def _claim_filter(db: Session, claim: _ProjectSyncOutboxClaim):
    return db.query(ProjectSegmentSyncOutbox).filter(
        ProjectSegmentSyncOutbox.id == claim.id,
        ProjectSegmentSyncOutbox.status == "processing",
        ProjectSegmentSyncOutbox.generation == claim.generation,
    )


def process_project_sync_outbox(db: Session, *, batch_size: int = PROJECT_SYNC_OUTBOX_BATCH_SIZE) -> int:
    """处理一批 outbox 任务并提交；返回认领的任务数。"""
    claims = _claim_project_sync_outbox_rows(db, batch_size=batch_size)
    if not claims:
        return 0

    affected_file_ids: set[UUID] = set()
    for claim in claims:
        # enqueue 的 upsert 会把 processing 重新置为 pending。若认领后又有新事件，
        # 跳过旧快照，让下一轮按最新来源重新处理，避免覆盖新任务状态。
        if _claim_filter(db, claim).with_entities(ProjectSegmentSyncOutbox.id).first() is None:
            db.rollback()
            continue

        started_at = perf_counter()
        savepoint = None
        try:
            current_user = (
                db.query(User).filter(User.id == claim.requested_by_id).first()
                if claim.requested_by_id is not None
                else None
            )
            # 同步写入和 claim 完成标记共用 SAVEPOINT；claim 过期时一起回滚，旧结果不会短暂提交。
            savepoint = db.begin_nested()
            if (
                claim.trigger_kind == PROJECT_SYNC_TRIGGER_BLUR
                and claim.source_segment_id is not None
                and claim.source_version is not None
            ):
                summary = sync_project_segments_from_source(
                    db,
                    project_id=claim.project_id,
                    source_language=claim.source_language,
                    target_language=claim.target_language,
                    source_hash=claim.source_hash,
                    source_segment_id=claim.source_segment_id,
                    source_version=claim.source_version,
                    current_user=current_user,
                )
            else:
                summary = sync_project_segments_for_hash(
                    db,
                    project_id=claim.project_id,
                    source_language=claim.source_language,
                    target_language=claim.target_language,
                    source_hash=claim.source_hash,
                    current_user=current_user,
                )
            completed_at = datetime.now()
            finalized = _claim_filter(db, claim).update(
                {
                    "status": "completed",
                    "error_message": "",
                    "processed_at": completed_at,
                    "updated_at": completed_at,
                },
                synchronize_session=False,
            )
            if not finalized:
                savepoint.rollback()
                db.rollback()
                logger.info(
                    "project sync outbox discarded stale claim project=%s hash=%s generation=%s",
                    claim.project_id,
                    claim.source_hash[:12],
                    claim.generation,
                )
                continue
            savepoint.commit()
            db.commit()

            affected_file_ids.update(summary.affected_file_ids)
            logger.info(
                "project sync outbox processed project=%s hash=%s trigger=%s generation=%s "
                "queue_ms=%s duration_ms=%s filled=%s updated=%s protected=%s stale_sources=%s conflicts=%s",
                claim.project_id,
                claim.source_hash[:12],
                claim.trigger_kind,
                claim.generation,
                max(0, int((claim.claimed_at - claim.last_enqueued_at).total_seconds() * 1000)),
                int((perf_counter() - started_at) * 1000),
                summary.filled_count,
                summary.updated_count,
                summary.protected_count,
                summary.stale_source_count,
                summary.conflict_count,
            )
        except Exception as exc:
            if savepoint is not None and savepoint.is_active:
                savepoint.rollback()
            db.rollback()
            current_attempt = (
                _claim_filter(db, claim)
                .with_entities(ProjectSegmentSyncOutbox.attempt_count)
                .scalar()
            )
            attempt_count = int(current_attempt or 0) + 1
            failed_at = datetime.now()
            _claim_filter(db, claim).update(
                {
                    "attempt_count": attempt_count,
                    "error_message": str(exc)[:2000],
                    "status": "failed" if attempt_count >= PROJECT_SYNC_OUTBOX_MAX_ATTEMPTS else "pending",
                    "updated_at": failed_at,
                },
                synchronize_session=False,
            )
            db.commit()
            logger.exception(
                "project sync outbox item failed project=%s hash=%s attempt=%s",
                claim.project_id,
                claim.source_hash[:12],
                attempt_count,
            )

    if affected_file_ids:
        publish_segment_changes(affected_file_ids)
    return len(claims)


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
            pool = engine.pool
            if all(hasattr(pool, name) for name in ("size", "checkedout", "overflow")):
                logger.info(
                    "project sync db pool size=%s checked_out=%s overflow=%s",
                    pool.size(),
                    pool.checkedout(),
                    pool.overflow(),
                )
        except Exception:
            db.rollback()
            logger.exception("project sync outbox run failed")
