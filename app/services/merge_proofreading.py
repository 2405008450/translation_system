"""合并视图校对协调：逐文件调度，数据库保存任务与结果。"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth import can_access_all_projects
from app.config import get_settings
from app.database import SessionLocal
from app.models import (FileRecord, FileExportTask, ProjectMergeView, ProofreadingBatch,
                        ProofreadingColumnBinding, ProofreadingSegmentBaseline, Segment, User,
                        MergeProofreadingRun, MergeProofreadingItem)
from app.services.workflow_capabilities import is_standalone_proofreading
from app.services.workflow_proofreading import (batch_config, eligible_workflow_segments,
    prepare_workflow_proofreading, segment_already_reviewed)
from app.services.file_operation_lock_service import clear_stale_file_operation_lock
from app.services.document_alignment.export import build_export_readiness, export_batch_is_busy
from app.services.proofreading import get_latest_generation_error_segment_ids, run_generate_batch, proofreading_error_message

logger = logging.getLogger(__name__)
ACTIVE = {"queued", "running", "canceling"}
ITEM_ACTIVE = {"dispatched", "running"}
TERMINAL = {"completed", "partial_failed", "failed", "canceled", "skipped"}


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def context(db, view_id, user, file_id=None):
    from app.routers.api import _get_merge_view_context
    if not user or not user.is_active:
        raise HTTPException(403, "账号不可用，无法处理任务。")
    view, project, files = _get_merge_view_context(db, view_id, user)
    if is_standalone_proofreading(project):
        raise HTTPException(400, "独立校对项目不支持合并工作流校对。")
    if file_id:
        files = [file for file in files if file.id == file_id]
        if not files:
            raise HTTPException(404, "当前文件不在可访问视图内。")
    return view, files


def inspect_file(db, file, user, options, kind="proofreading"):
    from app.routers.api import _require_file_record_work_access
    result = {"file_record_id": str(file.id), "filename": file.filename, "batch_id": None,
              "pending": 0, "reviewed": 0, "protected": 0, "eligible": 0,
              "failed": 0, "legacy_unverified": 0, "skipped_reason": None,
              "available_formats": [], "unavailable_formats": {}, "has_warnings": False}
    try:
        if kind == "proofreading":
            _require_file_record_work_access(file, user)
        clear_stale_file_operation_lock(db, file)
        if file.active_operation:
            raise HTTPException(409, "文件正在被其他任务锁定。")
        batch = prepare_workflow_proofreading(db, file, user)
        if not batch:
            raise HTTPException(409, "文件没有处于校对阶段的句段。")
        result["batch_id"] = str(batch.id)
        if batch.status in ACTIVE:
            raise HTTPException(409, "文件已有校对任务运行。")
        if kind == "export":
            if export_batch_is_busy(batch):
                raise HTTPException(409, "文件正在处理或导出。")
            readiness = build_export_readiness(db, batch)
            result.update({key: readiness.get(key, default) for key, default in (
                ("available_formats", []), ("unavailable_formats", {}), ("has_warnings", False))})
            result["readiness"] = readiness
            export_format = options.get("format")
            if export_format and export_format not in result["available_formats"]:
                raise HTTPException(400, result["unavailable_formats"].get(export_format, "不支持所选导出格式。"))
            return result, batch, []
        eligible = eligible_workflow_segments(db, batch, user)
        with_protected = eligible_workflow_segments(db, batch, user, include_protected=True)
        result["protected"] = len([s for s in with_protected if s.auto_write_protection == "protected"])
        baselines = {row.segment_id: row for row in db.query(ProofreadingSegmentBaseline).filter_by(batch_id=batch.id)}
        reviewed = {s.id for s in eligible if s.id in baselines and segment_already_reviewed(s, baselines[s.id])}
        failed = get_latest_generation_error_segment_ids(db, batch.id)
        if batch.status in {"failed", "canceled"}:
            failed.update(s.id for s in eligible if s.id not in reviewed and str(s.id) in batch_config(batch).get("generation", {}).get("segment_ids", []))
        result.update(eligible=len(eligible), reviewed=len(reviewed), failed=len(failed & {s.id for s in eligible}))
        result["legacy_unverified"] = sum(s.source == "llm_review" and not baselines[s.id].reviewed_fingerprint for s in eligible if s.id in baselines)
        scope = options.get("retry_scope", "unreviewed")
        selected = [s for s in eligible if scope == "all" or
                    (scope == "failed_only" and s.id in failed) or
                    (scope == "unreviewed" and s.id not in reviewed)]
        result["pending"] = len(selected)
        if not selected:
            raise HTTPException(409, "没有符合所选范围的待校对句段。")
        return result, batch, selected
    except HTTPException as error:
        if error.status_code not in (400, 403, 404, 409):
            raise
        result["skipped_reason"] = str(error.detail)
        return result, None, []


def preview(db, view_id, user, options, kind="proofreading"):
    _, files = context(db, view_id, user, UUID(options["file_id"]) if options.get("file_id") else None)
    items = [inspect_file(db, file, user, options, kind)[0] for file in files]
    formats = sorted({fmt for item in items for fmt in item["available_formats"]})
    return {"files": items, "pending": sum(item["pending"] for item in items),
            "reviewed": sum(item["reviewed"] for item in items),
            "protected": sum(item["protected"] for item in items),
            "available_formats": formats,
            "format_file_counts": {fmt: sum(not item["skipped_reason"] and fmt in item["available_formats"] for item in items) for fmt in formats},
            "has_warnings": any(item["has_warnings"] for item in items)}


def create_run(db, view_id, user, options, kind="proofreading"):
    view, files = context(db, view_id, user, UUID(options["file_id"]) if options.get("file_id") else None)
    # 同一视图的重复点击由行锁串行；跨视图冲突在子任务批次锁上再次校验。
    db.query(ProjectMergeView).filter_by(id=view.id).with_for_update().one()
    if db.query(MergeProofreadingRun.id).filter_by(view_id=view.id, kind=kind).filter(MergeProofreadingRun.status.in_(ACTIVE)).first():
        raise HTTPException(409, "当前视图已有同类任务运行，请查看进度。")
    checked = [inspect_file(db, file, user, options, kind)[0] for file in files]
    if not any(not item["skipped_reason"] for item in checked):
        raise HTTPException(409, {"message": "没有可处理的文件。", "files": checked})
    if kind == "export" and options.get("format") != "proofreading_audit_xlsx" and any(
        item["has_warnings"] and not item["skipped_reason"] for item in checked
    ) and not options.get("acknowledge_warnings"):
        raise HTTPException(409, "存在未确认或待处理项目，请确认导出提示后重试。")
    if kind == "proofreading":
        from app.services.llm_service import validate_provider_choice, LLMConfigurationError
        try:
            validate_provider_choice(provider=options.get("provider", "auto"), model_override=options.get("model") or None)
        except LLMConfigurationError as error:
            raise HTTPException(400, str(error)) from error
    run = MergeProofreadingRun(view_id=view.id, created_by_id=user.id, kind=kind,
                               options_json=json.dumps(options, ensure_ascii=False), status="queued")
    db.add(run)
    db.flush()
    for index, item in enumerate(checked):
        db.add(MergeProofreadingItem(run_id=run.id, file_record_id=UUID(item["file_record_id"]),
            filename=item["filename"], sort_order=index, batch_id=UUID(item["batch_id"]) if item["batch_id"] else None,
            status="skipped" if item["skipped_reason"] else "pending",
            message=item["skipped_reason"] or "等待处理", counts_json=json.dumps(item, ensure_ascii=False)))
    db.commit()
    return run


def get_run(db, view_id, run_id, user):
    _, files = context(db, view_id, user)
    run = db.query(MergeProofreadingRun).filter_by(id=run_id, view_id=view_id).first()
    if not run or (run.created_by_id != user.id and not can_access_all_projects(user)):
        raise HTTPException(404, "任务不存在或无权访问。")
    return run, {file.id for file in files}


def serialize_run(db, run, visible_ids):
    items = db.query(MergeProofreadingItem).filter_by(run_id=run.id).order_by(MergeProofreadingItem.sort_order).all()
    files = []
    for item in items:
        if item.file_record_id not in visible_ids:
            continue
        progress = 100 if item.status in TERMINAL else 0
        if item.status == "running":
            child = db.get(ProofreadingBatch, item.batch_id) if run.kind == "proofreading" else db.get(FileExportTask, item.export_task_id) if item.export_task_id else None
            progress = int(child.progress or 0) if child else 0
        files.append({"file_record_id": str(item.file_record_id), "filename": item.filename,
                      "status": item.status, "progress": progress, "message": item.message,
                      "counts": json.loads(item.counts_json)})
    total_weight = sum(max(item["counts"].get("pending", 0), 1) for item in files)
    progress = round(sum(max(item["counts"].get("pending", 0), 1) * item["progress"] for item in files) / max(total_weight, 1))
    return {"id": str(run.id), "kind": run.kind, "status": run.status, "progress": progress,
            "cancel_requested": run.cancel_requested, "files": files,
            "created_at": run.created_at.isoformat(), "download_available": bool(run.result_path),
            "can_cancel": run.status in ACTIVE and not run.cancel_requested,
            "options": {key: value for key, value in json.loads(run.options_json).items() if key != "user_instructions"}}


def cancel_run(db, run, user):
    if run.created_by_id != user.id and not can_access_all_projects(user):
        raise HTTPException(403, "只有启动者或管理员可以取消。")
    if run.status not in ACTIVE:
        return
    run.cancel_requested = True
    run.status = "canceling"
    for item in db.query(MergeProofreadingItem).filter_by(run_id=run.id):
        if item.status == "pending":
            item.status, item.message = "canceled", "已取消，未开始处理"
        elif item.status in ITEM_ACTIVE and run.kind == "proofreading" and item.batch_id:
            batch = db.query(ProofreadingBatch).filter_by(id=item.batch_id).with_for_update().first()
            generation = batch_config(batch).get("generation", {}) if batch else {}
            if batch and generation.get("merge_run_id") == str(run.id) and generation.get("run_id") == item.child_run_id and batch.status in ACTIVE:
                batch.cancel_requested = True
                batch.status = "canceled" if batch.status == "queued" else "canceling"
                batch.message = "校对已取消。" if batch.status == "canceled" else "正在取消校对。"
    db.commit()


def build_archive(db, run):
    root = Path(get_settings().export_task_dir) / "merge-proofreading"
    root.mkdir(parents=True, exist_ok=True)
    items = db.query(MergeProofreadingItem).filter_by(run_id=run.id).order_by(MergeProofreadingItem.sort_order).all()
    successful = []
    for item in items:
        task = db.get(FileExportTask, item.export_task_id) if item.export_task_id else None
        if item.status == "completed" and task and task.result_path and Path(task.result_path).is_file():
            name = (task.filename or item.filename).replace("\\", "/").split("/")[-1]
            successful.append((name, task.result_path))
        elif item.status == "completed":
            item.status, item.message = "failed", "导出文件不存在或已过期。"
    if not successful:
        return False
    manifest = [{"filename": item.filename, "status": item.status, "message": item.message} for item in items]
    output = root / f"{run.id}.zip"
    temporary = output.with_suffix(".tmp")
    # 压缩时仅持有普通值，释放数据库事务，避免大文件打包触发空闲事务超时。
    db.commit()
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
            for index, (name, path) in enumerate(successful, 1):
                archive.write(path, f"{index:03d}_{name}")
            archive.writestr("导出结果清单.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    db.refresh(run)
    run.result_path = str(output)
    return True


def finalize(db, run):
    items = db.query(MergeProofreadingItem).filter_by(run_id=run.id).all()
    if any(item.status not in TERMINAL for item in items):
        return
    if run.kind == "export" and not run.result_path:
        if not build_archive(db, run):
            run.status = "failed"
            db.commit()
            return
    statuses = [item.status for item in items]
    if run.cancel_requested:
        run.status = "canceled"
    elif all(status == "skipped" for status in statuses):
        run.status = "failed"
    elif any(status in {"failed", "partial_failed", "skipped", "canceled"} for status in statuses):
        run.status = "partial_failed" if any(status in {"completed", "partial_failed"} for status in statuses) else "failed"
    else:
        run.status = "completed"
    db.commit()


def finish_archive(run_id):
    with SessionLocal() as db:
        run = db.query(MergeProofreadingRun).filter_by(id=UUID(run_id)).with_for_update().first()
        if not run or run.status not in ACTIVE:
            return
        try:
            finalize(db, run)
        except Exception:
            db.rollback()
            logger.exception("merge proofreading archive failed id=%s", run_id)
            run = db.get(MergeProofreadingRun, UUID(run_id))
            run.status = "failed"
            db.commit()


async def merge_proofreading_archive_job(ctx, run_id):
    await asyncio.to_thread(finish_archive, run_id)


async def dispatch_run(run_id):
    from app.routers.api import _build_arq_redis_settings, _close_arq_pool, arq_create_pool
    settings = get_settings()
    if not settings.redis_url or arq_create_pool is None:
        raise HTTPException(503, "后台任务队列不可用，请稍后重试。")
    pool = await arq_create_pool(_build_arq_redis_settings(settings.redis_url))
    try:
        await pool.enqueue_job("merge_proofreading_tick", str(run_id), _queue_name="arq:maintenance")
    finally:
        await _close_arq_pool(pool)


async def merge_proofreading_tick(ctx, run_id):
    with SessionLocal() as db:
        run = db.query(MergeProofreadingRun).filter_by(id=UUID(run_id)).with_for_update().first()
        if not run or run.status not in ACTIVE:
            return
        active = db.query(MergeProofreadingItem).filter_by(run_id=run.id).filter(MergeProofreadingItem.status.in_(ITEM_ACTIVE)).first()
        if active:
            if active.status == "dispatched":
                # 稳定任务 ID 防止重复入队；提交后进程退出也能由定时协调恢复。
                await ctx['redis'].enqueue_job("merge_proofreading_item_job", str(active.id),
                    _job_id=f"merge-proofreading-item:{active.id}", _queue_name="arq:maintenance")
            else:
                interrupted = now() - active.updated_at > timedelta(minutes=40)
                if now() - active.updated_at > timedelta(seconds=45):
                    from arq.jobs import Job, JobStatus
                    state = await Job(f"merge-proofreading-item:{active.id}", ctx['redis'], _queue_name="arq:maintenance").status()
                    interrupted = interrupted or state in {JobStatus.not_found, JobStatus.complete}
                if not interrupted:
                    return
                active.status, active.message = "failed", "任务中断或超时，请检查后重试。"
                if active.batch_id and run.kind == "proofreading":
                    batch = db.get(ProofreadingBatch, active.batch_id)
                    if batch and batch_config(batch).get('generation', {}).get('run_id') == active.child_run_id and batch.status in ACTIVE:
                        batch.status, batch.error_message = "failed", active.message
                db.commit()
            return
        item = db.query(MergeProofreadingItem).filter_by(run_id=run.id, status="pending").order_by(MergeProofreadingItem.sort_order).first()
        if not item:
            if run.kind == "export":
                await ctx['redis'].enqueue_job("merge_proofreading_archive_job", str(run.id),
                    _job_id=f"merge-proofreading-archive:{run.id}", _queue_name="arq:maintenance")
            else:
                finalize(db, run)
            return
        if run.cancel_requested:
            item.status, item.message = "canceled", "已取消"
        else:
            item.status, item.message = "dispatched", "等待后台执行"
            run.status = "running"
        item.updated_at = now()
        db.commit()
        if item.status == "dispatched":
            await ctx['redis'].enqueue_job("merge_proofreading_item_job", str(item.id),
                _job_id=f"merge-proofreading-item:{item.id}", _queue_name="arq:maintenance")


def execute_item(item_id):
    batch_args = None
    export_id = None
    with SessionLocal() as db:
        item = db.query(MergeProofreadingItem).filter_by(id=UUID(item_id)).with_for_update().first()
        if not item or item.status != "dispatched":
            return  # ARQ 重投不能重复调用 LLM。
        run = db.get(MergeProofreadingRun, item.run_id)
        if run.cancel_requested:
            item.status, item.message = "canceled", "已取消"
            db.commit()
            return
        try:
            user = db.get(User, run.created_by_id)
            options = json.loads(run.options_json)
            _, files = context(db, run.view_id, user, item.file_record_id)
            result, batch, selected = inspect_file(db, files[0], user, options, run.kind)
            item.counts_json = json.dumps(result, ensure_ascii=False)
            if result["skipped_reason"]:
                item.status, item.message = "skipped", result["skipped_reason"]
                db.commit()
                return
            if run.kind == "proofreading":
                from app.routers.proofreading import prepare_proofreading_generation, ProofreadingGenerateRequest
                batch, config = prepare_proofreading_generation(db, batch.id, ProofreadingGenerateRequest(**options),
                    user, segment_ids={str(s.id) for s in selected}, merge_run_id=run.id)
                generation = config['generation']
                item.batch_id, item.child_run_id = batch.id, generation['run_id']
                batch_args = (batch.id, user.id, generation['provider'], generation['model'] or None,
                              generation['user_instructions'], generation['retry_scope'], generation['run_id'])
            else:
                from app.services.file_export_queue import queue_file_export
                # queue_file_export 提交独立子任务；先持久化运行标记，避免崩溃后重复导出。
                item.status, item.updated_at = "running", now()
                db.commit()
                if result['has_warnings'] and options['format'] != 'proofreading_audit_xlsx' and not options.get('acknowledge_warnings'):
                    raise HTTPException(409, "文件条件变化，请确认导出提示后重试。")
                task = queue_file_export(db, file_record_id=item.file_record_id, export_type=options['format'],
                                         current_user=user, defer_execution=True)
                export_id = UUID(task['task_id'])
                item.export_task_id = export_id
            item.status, item.message, item.updated_at = "running", "正在处理", now()
            db.commit()
        except Exception as error:
            db.rollback()
            item = db.get(MergeProofreadingItem, UUID(item_id))
            item.status, item.message = "failed", proofreading_error_message(error.detail if isinstance(error, HTTPException) else str(error))
            db.commit()
            return
    try:
        if batch_args:
            run_generate_batch(*batch_args)
        else:
            from app.services.file_export_queue import run_file_export_task
            run_file_export_task(export_id)
        with SessionLocal() as db:
            item = db.get(MergeProofreadingItem, UUID(item_id))
            child = db.get(ProofreadingBatch, item.batch_id) if batch_args else db.get(FileExportTask, export_id)
            item.status = child.status if child and child.status in TERMINAL else "failed"
            item.message = (child.error_message or child.message) if batch_args and child else (child.error or child.message) if child else "子任务不存在"
            item.updated_at = now()
            db.commit()
    except Exception:
        logger.exception("merge proofreading item failed id=%s", item_id)
        with SessionLocal() as db:
            item = db.get(MergeProofreadingItem, UUID(item_id))
            item.status, item.message = "failed", "后台处理失败，请重试。"
            db.commit()


async def merge_proofreading_item_job(ctx, item_id):
    await asyncio.to_thread(execute_item, item_id)
    with SessionLocal() as db:
        item = db.get(MergeProofreadingItem, UUID(item_id))
        run_id = str(item.run_id) if item else None
    if run_id:
        await merge_proofreading_tick(ctx, run_id)


async def recover_merge_proofreading_runs(ctx):
    with SessionLocal() as db:
        ids = [str(value) for (value,) in db.query(MergeProofreadingRun.id).filter(MergeProofreadingRun.status.in_(ACTIVE)).all()]
    for run_id in ids:
        await merge_proofreading_tick(ctx, run_id)
    # 与单文件导出一致保留 24 小时，任务记录继续保留供审计。
    root = (Path(get_settings().export_task_dir) / "merge-proofreading").resolve()
    with SessionLocal() as db:
        expired = db.query(MergeProofreadingRun).filter(
            MergeProofreadingRun.kind == "export", MergeProofreadingRun.result_path.is_not(None),
            MergeProofreadingRun.created_at < now() - timedelta(hours=24),
        ).all()
        for run in expired:
            output = Path(run.result_path).resolve()
            if output.is_relative_to(root) and output.is_file():
                output.unlink()
            run.result_path = None
        db.commit()
