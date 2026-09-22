"""人工修订同步：基线、关联修订和持久化任务与普通翻译同步隔离。"""
from __future__ import annotations

import logging
from time import monotonic
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.orm import Session, object_session

from app.database import SessionLocal
from app.models import FileRecord, Project, ReviewSyncGroup, ReviewSyncMember, ReviewSyncTask, Segment, SegmentRevision, User
from app.services.normalizer import build_source_hash
from app.services.segment_events import publish_segment_changes
from app.services.segment_status import apply_segment_status, resolve_unconfirmed_segment_status

logger = logging.getLogger(__name__)


def lock_project(db: Session, project_id: UUID | None) -> None:
    # 保存、传播、接受/拒绝统一先获取项目锁，避免片段锁与组锁倒序死锁。
    if project_id and db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                   {"key": f"review-sync:{project_id}"})


def lock_file_project(db: Session, file_id: UUID) -> None:
    lock_project(db, db.query(FileRecord.project_id).filter(FileRecord.id == file_id).scalar())


def active_member(db: Session, segment_id: UUID) -> ReviewSyncMember | None:
    return (db.query(ReviewSyncMember).join(ReviewSyncGroup)
            .filter(ReviewSyncMember.segment_id == segment_id, ReviewSyncMember.active.is_(True),
                    ReviewSyncGroup.status == "pending").first())


def record_edit(db: Session, segment: Segment, before_text: str, user: User | None,
                track_revision: bool, source: str) -> None:
    """由保存流程调用；不触发传播，只记录已落库版本与首次基线。"""
    file = db.get(FileRecord, segment.file_record_id)
    project = db.get(Project, file.project_id) if file and file.project_id else None
    enabled = bool(project and project.review_sync_enabled and track_revision and source == "manual" and user
                   and not segment.project_sync_disabled)
    segment._review_sync_managed = enabled
    member = active_member(db, segment.id)
    group = db.get(ReviewSyncGroup, member.group_id) if member else None
    same_origin = bool(group and group.source_segment_id == segment.id and user and group.author_id == user.id)
    if same_origin:
        same_origin = (group.source_hash == (segment.source_hash or build_source_hash(segment.source_text))
                       and member.after_text == (before_text or ""))
    if member and (not same_origin or not enabled):
        member.active = False
        if group.source_segment_id == segment.id:
            group.status = "detached"
            cancel_task(db, group.id)
        group = None
        member = None
    if not enabled or (before_text or "") == (segment.target_text or "") and group is None:
        return
    # 生产会话关闭 autoflush；先落库本次新增/合并/删除的修订，
    # 再查询基线，否则首次编辑会被分流却无法建立同步组。
    db.flush()
    revision = (db.query(SegmentRevision).filter(SegmentRevision.segment_id == segment.id,
                SegmentRevision.source == "manual", SegmentRevision.status == "pending")
                .order_by(SegmentRevision.created_at, SegmentRevision.id).first())
    if group is None:
        # 已有独立待处理修订不自动并入新组，以免拒绝时多回退一轮编辑。
        if revision is None or revision.before_text != (before_text or ""):
            return
        segment.source_hash = segment.source_hash or build_source_hash(segment.source_text)
        group = ReviewSyncGroup(project_id=project.id, source_segment_id=segment.id, author_id=user.id,
                    source_hash=segment.source_hash or build_source_hash(segment.source_text),
                    source_language=file.source_language or "", target_language=file.target_language or "",
                    before_text=before_text or "", after_text=segment.target_text or "",
                    source_version=int(segment.version or 1), generation=1)
        db.add(group)
        db.flush()
        member = ReviewSyncMember(group_id=group.id, segment_id=segment.id, before_text=before_text or "",
                                  after_text=segment.target_text or "", version=int(segment.version or 1))
        db.add(member)
    else:
        group.generation += 1
        group.after_text = segment.target_text or ""
        group.source_version = int(segment.version or 1)
        cancel_task(db, group.id)
    db.flush()
    member.revision_id = revision.id if revision else None
    member.after_text = segment.target_text or ""
    member.version = int(segment.version or 1)
    db.flush()


def segment_sync_info(segment: Segment) -> dict:
    db = object_session(segment)
    if db is None:
        return {}
    file = segment.file_record
    enabled = bool(file and file.project and file.project.review_sync_enabled)
    member = active_member(db, segment.id) if enabled else None
    group = db.get(ReviewSyncGroup, member.group_id) if member else None
    return {"review_sync_enabled": enabled,
            "review_sync_group_id": str(group.id) if group and group.source_segment_id == segment.id else None}


def revision_sync_info(revision: SegmentRevision) -> dict:
    db = object_session(revision)
    if db is None:
        return {}
    member = db.query(ReviewSyncMember).filter_by(revision_id=revision.id, active=True).first()
    if member is None:
        return {}
    group = db.get(ReviewSyncGroup, member.group_id)
    count = db.query(func.count(ReviewSyncMember.id)).filter_by(group_id=group.id, active=True).scalar()
    return {"review_sync_group_id": str(group.id), "review_sync_count": count,
            "review_sync_source_segment_id": str(group.source_segment_id)}


def can_write(db: Session, segment: Segment, user: User | None) -> bool:
    if user is None or not user.is_active:
        return False
    # 复用文件锁、项目权限、分配范围及流程阶段权限，异步执行时再次校验。
    from app.routers.api import _require_file_record_write_access, _require_segment_work_access
    try:
        file = _require_file_record_write_access(db, segment.file_record_id, user)
        _require_segment_work_access(db, file, segment, user)
        return True
    except HTTPException as exc:
        if exc.status_code in (403, 404, 409, 423):
            return False
        raise


def cancel_task(db: Session, group_id: UUID) -> None:
    task = db.query(ReviewSyncTask).filter_by(group_id=group_id).first()
    if task:
        task.status = "cancelled"


def enqueue(db: Session, segment: Segment, group_id: UUID, version: int, user: User) -> ReviewSyncTask:
    lock_file_project(db, segment.file_record_id)
    db.refresh(segment)
    group = db.get(ReviewSyncGroup, group_id, populate_existing=True)
    if not group or group.source_segment_id != segment.id or group.author_id != user.id:
        raise HTTPException(403, "修订同步组不属于当前编辑。")
    project = db.get(Project, group.project_id, populate_existing=True)
    if not project.review_sync_enabled or segment.project_sync_disabled:
        raise HTTPException(409, "修订同步已关闭。")
    # 确认等操作只推进版本、不改译文时，重试可绑定操作者的最新版本。
    if (group.status == "pending" and group.source_version != version and segment.version == version
            and segment.target_text == group.after_text and segment.source_hash == group.source_hash
            and segment.last_modified_by_id == user.id):
        member = active_member(db, segment.id)
        if member and member.group_id == group.id:
            group.source_version = version
            group.generation += 1
            member.version = version
    if group.status != "pending" or group.source_version != version or int(segment.version or 1) != version:
        raise HTTPException(409, "句段版本已变化，请保存最新内容后重试。")
    task = db.query(ReviewSyncTask).filter_by(group_id=group.id).first()
    if task is None:
        task = ReviewSyncTask(group_id=group.id, generation=group.generation, status="pending")
        db.add(task)
    elif task.generation != group.generation or task.status in ("failed", "cancelled"):
        task.generation = group.generation
        task.status = "pending"
        task.attempts = 0
        task.error = ""
        task.result = {}
        task.updated_at = datetime.now()
    db.flush()
    return task


def _summary() -> dict:
    return {"updated_count": 0, "skipped_count": 0, "reasons": {}, "affected_file_ids": []}


def _skip(result: dict, reason: str) -> None:
    result["skipped_count"] += 1
    result["reasons"][reason] = result["reasons"].get(reason, 0) + 1


def _write_text(db: Session, segment: Segment, value: str, user: User) -> None:
    from app.services.file_record_service import set_segment_target_layout_text
    from app.services.analytics_service import record_translation_metric_event
    before = segment.target_text or ""
    segment.target_text = value
    segment.target_html = None
    set_segment_target_layout_text(segment, "")
    segment.source = "manual"
    segment.project_sync_source_segment_id = None
    segment.project_sync_source_file_record_id = None
    segment.llm_provider = None
    segment.llm_model = None
    segment.last_modified_by_id = user.id
    segment.version = int(segment.version or 1) + 1
    apply_segment_status(segment, resolve_unconfirmed_segment_status(segment))
    record_translation_metric_event(db, segment=segment, before_text=before, after_text=value,
                                    source="manual", current_user=user)


def _finish_files(db: Session, file_ids: set[UUID]) -> None:
    from app.services.file_record_service import sync_file_record_status
    for file_id in sorted(file_ids, key=str):
        sync_file_record_status(db, file_id)


def process_task(db: Session, task_id: UUID) -> dict | None:
    started_at = monotonic()
    task = db.get(ReviewSyncTask, task_id)
    if not task:
        return None
    group = db.get(ReviewSyncGroup, task.group_id)
    lock_project(db, group.project_id)
    db.refresh(task)
    db.refresh(group)
    if task.status != "pending":
        return None
    result = _summary()
    result["queue_delay_ms"] = max(0, int((datetime.now() - task.updated_at).total_seconds() * 1000))
    project = db.get(Project, group.project_id, populate_existing=True)
    user = db.get(User, group.author_id)
    rows = (db.query(Segment).join(FileRecord, Segment.file_record_id == FileRecord.id).filter(FileRecord.project_id == group.project_id,
            func.coalesce(FileRecord.source_language, "") == group.source_language,
            func.coalesce(FileRecord.target_language, "") == group.target_language,
            Segment.source_hash == group.source_hash).order_by(Segment.id)
            .with_for_update(of=Segment).populate_existing().all())
    source = next((row for row in rows if row.id == group.source_segment_id), None)
    if (not project.review_sync_enabled or group.status != "pending" or task.generation != group.generation
            or not source or int(source.version or 1) != group.source_version
            or source.target_text != group.after_text or source.project_sync_disabled or not can_write(db, source, user)):
        task.status = "cancelled"
        _skip(result, "source_changed_or_disabled")
        task.result = result
        db.commit()
        logger.info("review sync cancelled group=%s generation=%s result=%s", group.id, task.generation, result)
        return result
    members = {m.segment_id: m for m in db.query(ReviewSyncMember).filter_by(group_id=group.id).all()}
    file_ids = set()
    reverting = group.before_text == group.after_text
    from app.services.revision_service import create_revision
    for segment in rows:
        if segment.id == source.id:
            continue
        member = members.get(segment.id)
        revision = db.get(SegmentRevision, member.revision_id) if member and member.revision_id else None
        if segment.project_sync_disabled:
            _skip(result, "sync_disabled")
            continue
        if not can_write(db, segment, user):
            _skip(result, "no_write_access")
            continue
        if member:
            if (not member.active or int(segment.version or 1) != member.version
                    or segment.target_text != member.after_text or not revision or revision.status != "pending"):
                member.active = False
                _skip(result, "independently_modified")
                continue
        else:
            if reverting:
                continue
            if (segment.target_text or "") != group.before_text:
                _skip(result, "different_translation")
                continue
            if db.query(SegmentRevision.id).filter_by(segment_id=segment.id, status="pending").first() or active_member(db, segment.id):
                _skip(result, "independent_revision")
                continue
            member = ReviewSyncMember(group_id=group.id, segment_id=segment.id, before_text=segment.target_text or "",
                                      after_text=group.after_text, version=int(segment.version or 1))
            db.add(member)
        before = segment.target_text or ""
        _write_text(db, segment, group.after_text, user)
        if reverting:
            revision.status = "rejected"
            revision.resolved_by_id = user.id
            revision.resolved_at = datetime.now()
        else:
            revision = create_revision(db, file_record_id=segment.file_record_id, segment=segment,
                                       before_text=before, after_text=group.after_text, source="manual", author=user)
            db.flush()
        member.revision_id = revision.id if revision else None
        member.version = int(segment.version or 1)
        member.after_text = group.after_text
        result["updated_count"] += 1
        file_ids.add(segment.file_record_id)
    if reverting:
        group.status = "reverted"
    task.status = "completed"
    result["affected_file_ids"] = [str(value) for value in file_ids]
    result["execution_ms"] = int((monotonic() - started_at) * 1000)
    task.result = result
    task.error = ""
    _finish_files(db, file_ids)
    db.commit()
    publish_segment_changes(list(file_ids | {source.file_record_id}))
    logger.info("review sync group=%s generation=%s result=%s", group.id, group.generation, result)
    return result


def run_review_sync_once() -> None:
    with SessionLocal() as db:
        ids = [row[0] for row in db.query(ReviewSyncTask.id).filter_by(status="pending").limit(100).all()]
        for task_id in ids:
            task = db.get(ReviewSyncTask, task_id)
            generation = task.generation
            try:
                process_task(db, task_id)
            except Exception:
                db.rollback()
                task = db.get(ReviewSyncTask, task_id)
                group = db.get(ReviewSyncGroup, task.group_id)
                lock_project(db, group.project_id)
                db.refresh(task)
                if task.generation == generation and task.status == "pending":
                    task.attempts += 1
                    task.status = "failed" if task.attempts >= 3 else "pending"
                    task.error = "修订同步失败，可重试；当前编辑已保留。"
                    db.commit()
                logger.exception("review sync failed task=%s generation=%s", task_id, generation)


def resolve_group(db: Session, revision: SegmentRevision, status: str, user: User) -> dict | None:
    lock_file_project(db, revision.file_record_id)
    db.refresh(revision)
    member = db.query(ReviewSyncMember).filter_by(revision_id=revision.id, active=True).first()
    if member is None:
        return None
    group = db.get(ReviewSyncGroup, member.group_id, populate_existing=True)
    if group.status != "pending":
        return None
    result = _summary()
    cancel_task(db, group.id)
    members = db.query(ReviewSyncMember).filter_by(group_id=group.id, active=True).all()
    segments = {row.id: row for row in db.query(Segment).filter(Segment.id.in_([m.segment_id for m in members]))
                .order_by(Segment.id).with_for_update().populate_existing().all()}
    file_ids = set()
    for item in members:
        segment = segments.get(item.segment_id)
        entry = db.get(SegmentRevision, item.revision_id, populate_existing=True) if item.revision_id else None
        if not segment or not entry or entry.status != "pending" or segment.version != item.version or segment.target_text != item.after_text:
            item.active = False
            _skip(result, "independently_modified")
            continue
        if not can_write(db, segment, user) or segment.project_sync_disabled:
            item.active = False
            _skip(result, "no_write_access_or_disabled")
            continue
        _write_text(db, segment, item.before_text if status == "rejected" else item.after_text, user)
        entry.status = status
        entry.resolved_by_id = user.id
        entry.resolved_at = datetime.now()
        item.version = segment.version
        result["updated_count"] += 1
        file_ids.add(segment.file_record_id)
    group.status = status
    result["affected_file_ids"] = [str(value) for value in file_ids]
    _finish_files(db, file_ids)
    db.flush()
    return result
