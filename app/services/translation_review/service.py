"""
翻译内容校对 — 核心服务层（基于项目规则文件 + LLM 统一检查）

新架构：
- 规则文件存在 projects.translation_rules（纯文本）
- 检查时把规则 + 批次句段（50条）一起喂给 LLM
- LLM 自行判断违规类别、返回问题列表
- 程序层做锚点定位、去重、落库

公开函数：
    upload_project_rules      上传并存储项目规则文件
    get_project_rules         读取项目规则
    create_review_report      建 report 行
    run_review_with_rules     主检查逻辑（批次 LLM）
    get_report / list_*       查询
    apply_item / restore_item / reject_item / set_items_ignored
    apply_batch / undo_batch
    serialize_report / serialize_item
    translation_review_job    arq worker 入口
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    FileRecord,
    Project,
    ProjectMergeView,
    Segment,
    TranslationReviewAgentRun,
    TranslationReviewReport,
    TranslationReviewReportItem,
    User,
)
from app.services.file_record_service import (
    list_segments_for_file_record,
    update_segment_by_sentence_id,
)
from app.services.merge_view_service import load_view_file_records, serialize_file_ids

logger = logging.getLogger(__name__)

_STATUS_RUNNING   = "running"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED    = "failed"

_ITEM_OPEN     = "open"
_ITEM_APPLIED  = "applied"
_ITEM_REJECTED = "rejected"
_ITEM_IGNORED  = "ignored"
_ITEM_STALE    = "stale"

_LOCATE_OK           = "ok"
_LOCATE_NORM         = "normalized"
_LOCATE_UNAPPLICABLE = ("unlocatable", "ambiguous")

REVIEW_BATCH_SIZE = 50   # 每次发给 LLM 的句段数


# ─────────────────────────────────────────────────────────────
# 规则文件管理
# ─────────────────────────────────────────────────────────────

def upload_project_rules(
    db: Session,
    project_id: UUID,
    rules_text: str,
    filename: str,
) -> Project:
    """把提取好的纯文本规则存入 project.translation_rules。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise ValueError("项目不存在")
    project.translation_rules = rules_text.strip()
    project.translation_rules_filename = filename or ""
    project.translation_rules_updated_at = datetime.utcnow()
    db.flush()
    db.commit()
    return project


def get_project_rules(db: Session, project_id: UUID) -> dict[str, Any]:
    """返回项目规则信息。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return {"rules": "", "filename": "", "updated_at": None}
    return {
        "rules": project.translation_rules or "",
        "filename": project.translation_rules_filename or "",
        "updated_at": project.translation_rules_updated_at.isoformat()
                      if project.translation_rules_updated_at else None,
    }


def extract_text_from_upload(filename: str, raw_bytes: bytes) -> str:
    """从上传文件中提取纯文本（支持 .docx / .txt / .md）。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "docx":
        from io import BytesIO
        from docx import Document
        doc = Document(BytesIO(raw_bytes))
        lines = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                lines.append(text)
        return "\n".join(lines)

    # txt / md / 其他文本格式
    for encoding in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────
# 建报告
# ─────────────────────────────────────────────────────────────

def create_review_report(
    db: Session,
    *,
    project: Project | None,
    files: list[FileRecord],
    file_order_map: dict[UUID, int],
    merge_view: ProjectMergeView | None,
    current_user: User,
    segment_scope: str = "all",
    provider: str = "auto",
    model: str = "",
    web_verify_provider: str = "none",
) -> TranslationReviewReport:
    file_ids_text = serialize_file_ids([f.id for f in files])
    report = TranslationReviewReport(
        project_id=project.id if project else None,
        file_record_id=(files[0].id if len(files) == 1 and not merge_view else None),
        merge_view_id=merge_view.id if merge_view else None,
        created_by_id=current_user.id,
        scope="merge_view" if merge_view else "file",
        segment_scope=segment_scope,
        enabled_categories="[]",  # 不再预设，由 LLM 自动分类
        file_ids=file_ids_text,
        total_files=len(files),
        provider=provider,
        model=model,
        web_verify_provider=web_verify_provider,
        status=_STATUS_RUNNING,
        progress="{}",
        category_counts="{}",
        file_counts="{}",
        failed_categories="[]",
    )
    db.add(report)
    db.flush()
    return report


# ─────────────────────────────────────────────────────────────
# 主检查逻辑
# ─────────────────────────────────────────────────────────────

async def run_review_with_rules(
    db: Session,
    report: TranslationReviewReport,
    files: list[FileRecord],
    file_order_map: dict[UUID, int],
    rules_text: str,
    *,
    segment_scope: str = "all",
    provider: str = "auto",
    model: str | None = None,
) -> None:
    """
    核心检查：把规则文本 + 批次句段喂给 LLM，落库问题条目。
    按文件逐批处理（不跨文件混批）。
    """
    from app.services.translation_review.checker import run_llm_check_batch

    if not rules_text.strip():
        _finalize_report(db, report, status=_STATUS_FAILED,
                         error="项目没有上传翻译规则文件，请先上传。")
        return

    all_items: list[TranslationReviewReportItem] = []
    category_counts: dict[str, int] = {}
    file_counts: dict[str, int] = {}
    checked_segs = 0
    failed = False

    # 先确定总句段数，保证前端在整个任务期间都能显示稳定的 N / total。
    prepared_files: list[tuple[FileRecord, int, list[Segment]]] = []
    total_segs = 0
    for file_record in files:
        segments = _load_segments_for_scope(db, file_record, segment_scope)
        prepared_files.append((file_record, file_order_map.get(file_record.id, 0), segments))
        total_segs += len(segments)

    report.total_segments = total_segs
    report.checked_segments = 0
    _update_progress(db, report, "", checked_segs, total_segs)
    db.commit()

    for file_record, file_order, segments in prepared_files:
        # 按 REVIEW_BATCH_SIZE 分批
        for batch_start in range(0, len(segments), REVIEW_BATCH_SIZE):
            batch = segments[batch_start: batch_start + REVIEW_BATCH_SIZE]
            _update_progress(db, report, file_record.filename or "", checked_segs, total_segs)
            db.commit()

            try:
                findings = await run_llm_check_batch(
                    rules_text=rules_text,
                    segments=batch,
                    provider=provider,
                    model=model,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("translation_review llm batch failed: %s", exc)
                failed = True
                findings = []

            for finding in findings:
                sid = finding.get("sid", "")
                segment = next((s for s in batch if s.sentence_id == sid), None)
                if not segment:
                    continue
                item = _finding_to_item(
                    report=report,
                    segment=segment,
                    file_record=file_record,
                    file_order=file_order,
                    finding=finding,
                )
                _locate_anchor(item)
                db.add(item)
                all_items.append(item)
                cat = (item.category_key or "其他").strip()
                category_counts[cat] = category_counts.get(cat, 0) + 1
                fid = str(item.file_record_id)
                file_counts[fid] = file_counts.get(fid, 0) + 1

            checked_segs += len(batch)
            _update_progress(db, report, file_record.filename or "", checked_segs, total_segs)
            db.commit()

    # 汇总统计
    _update_report_counts(db, report, category_counts, file_counts, all_items)
    report.total_segments = total_segs
    report.checked_segments = checked_segs
    # 把 LLM 发现的所有类别回写到 enabled_categories，供前端筛选
    report.enabled_categories = json.dumps(
        sorted(set(category_counts.keys())), ensure_ascii=False
    )
    _finalize_report(db, report, status=_STATUS_FAILED if failed else _STATUS_COMPLETED)
    db.commit()


# ─────────────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────────────

def _load_segments_for_scope(
    db: Session,
    file_record: FileRecord,
    segment_scope: str,
) -> list[Segment]:
    segments = list_segments_for_file_record(db, file_record.id)
    if segment_scope == "translated_only":
        return [s for s in segments if (s.target_text or "").strip()]
    if segment_scope == "unconfirmed_only":
        return [s for s in segments if s.status != "confirmed" and (s.target_text or "").strip()]
    if segment_scope == "confirmed_only":
        return [s for s in segments if s.status == "confirmed"]
    return segments


def _finding_to_item(
    *,
    report: TranslationReviewReport,
    segment: Segment,
    file_record: FileRecord,
    file_order: int,
    finding: dict,
) -> TranslationReviewReportItem:
    severity = finding.get("severity", "warning")
    if severity not in ("error", "warning", "suggestion"):
        severity = "warning"
    confidence = finding.get("confidence", "medium")
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    return TranslationReviewReportItem(
        report_id=report.id,
        project_id=report.project_id,
        file_record_id=file_record.id,
        segment_id=segment.id,
        sentence_id=segment.sentence_id,
        file_name=file_record.filename or "",
        file_order=file_order,
        display_index=getattr(segment, "display_index", -1) or -1,
        sequence_index=getattr(segment, "sequence_index", -1) or -1,
        category_key=(finding.get("category") or "其他")[:40],
        category_index=0,
        rule_ref=(finding.get("rule_ref") or "")[:20],
        severity=severity,
        origin="ai",
        source_text=(segment.source_text or "")[:4000],
        target_text=(segment.target_text or "")[:4000],
        quote=(finding.get("quote") or "")[:500],
        replace_anchor=(finding.get("replace_anchor") or "")[:500],
        suggested_value=(finding.get("suggested_value") or "")[:2000],
        suggested_target_text=(finding.get("suggested_target_text") or "")[:4000],
        reason=(finding.get("reason") or "")[:1000],
        confidence=confidence,
        apply_mode="anchor" if (finding.get("replace_anchor") and finding.get("suggested_value")) else "manual",
        locate_status=_LOCATE_OK,
        block_index=getattr(segment, "block_index", 0) or 0,
        row_index=getattr(segment, "row_index", None),
        cell_index=getattr(segment, "cell_index", None),
        status=_ITEM_OPEN,
    )


def _locate_anchor(item: TranslationReviewReportItem) -> None:
    """定位 replace_anchor 在 target_text 中的位置。"""
    target = item.target_text or ""
    anchor = item.replace_anchor or ""
    if not anchor:
        item.locate_status = "unlocatable"
        item.apply_mode = "manual"
        return
    count = target.count(anchor)
    if count == 1:
        start = target.index(anchor)
        item.quote_start = start
        item.quote_end = start + len(anchor)
        item.locate_status = _LOCATE_OK
        return
    if count > 1:
        item.locate_status = "ambiguous"
        item.apply_mode = "manual"
        return
    # 归一化匹配
    import re
    def _n(s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip()
        for a, b in [("\u2018", "'"), ("\u2019", "'"), ("\u201c", '"'), ("\u201d", '"'),
                     ("\u2013", "-"), ("\u2014", "-")]:
            s = s.replace(a, b)
        return s.lower()
    norm_t = _n(target)
    norm_a = _n(anchor)
    if norm_t.count(norm_a) == 1:
        approx = target.lower().find(anchor.lower())
        if approx >= 0:
            item.quote_start = approx
            item.quote_end = approx + len(anchor)
        item.locate_status = _LOCATE_NORM
        return
    item.locate_status = "unlocatable"
    item.apply_mode = "manual"


def _update_report_counts(
    db: Session,
    report: TranslationReviewReport,
    category_counts: dict[str, int],
    file_counts: dict[str, int],
    all_items: list,
) -> None:
    from collections import Counter
    issue_count = len(all_items)
    active_count = sum(1 for i in all_items if i.status == _ITEM_OPEN)
    seg_counter: Counter = Counter(i.sentence_id for i in all_items)
    multi_count = sum(1 for c in seg_counter.values() if c >= 2)
    report.issue_count = issue_count
    report.active_issue_count = active_count
    report.multi_category_segment_count = multi_count
    report.category_counts = json.dumps(category_counts, ensure_ascii=False)
    report.file_counts = json.dumps(file_counts, ensure_ascii=False)
    db.flush()


def _update_progress(
    db: Session,
    report: TranslationReviewReport,
    current_file: str,
    checked: int,
    total: int,
) -> None:
    pct = round(checked / max(total, 1) * 100)
    report.checked_segments = checked
    report.total_segments = total
    progress = {
        "overall_percent": pct,
        "checked_segments": checked,
        "total_segments": total,
        "current_file_name": current_file,
        "updated_at": datetime.utcnow().isoformat(),
    }
    report.progress = json.dumps(progress, ensure_ascii=False)
    db.flush()


def _finalize_report(
    db: Session,
    report: TranslationReviewReport,
    status: str = _STATUS_COMPLETED,
    error: str = "",
) -> None:
    report.status = status
    report.finished_at = datetime.utcnow()
    if error:
        report.error_message = error
    db.flush()


# ─────────────────────────────────────────────────────────────
# 查询
# ─────────────────────────────────────────────────────────────

def get_report(db: Session, report_id: UUID) -> TranslationReviewReport | None:
    return db.query(TranslationReviewReport).filter(TranslationReviewReport.id == report_id).first()


def list_file_reports(db: Session, file_record_id: UUID, limit: int = 1) -> list[TranslationReviewReport]:
    return (
        db.query(TranslationReviewReport)
        .filter(TranslationReviewReport.scope == "file",
                TranslationReviewReport.file_record_id == file_record_id)
        .order_by(TranslationReviewReport.created_at.desc())
        .limit(min(max(limit, 1), 20)).all()
    )


def list_merge_view_reports(db: Session, merge_view_id: UUID, limit: int = 1) -> list[TranslationReviewReport]:
    return (
        db.query(TranslationReviewReport)
        .filter(TranslationReviewReport.scope == "merge_view",
                TranslationReviewReport.merge_view_id == merge_view_id)
        .order_by(TranslationReviewReport.created_at.desc())
        .limit(min(max(limit, 1), 20)).all()
    )


def load_report_items(db: Session, report_id: UUID) -> list[TranslationReviewReportItem]:
    """加载报告条目，并同步当前句段的文档显示序号。

    报告条目保存的是生成报告时的序号快照；句段拆分、合并或重新解析后，
    编辑器使用的 Segment.display_index 可能已经变化。用外连接读取最新序号，
    同时保留已删除句段的历史快照，避免审校列表编号与编辑器不一致。
    """
    rows = (
        db.query(TranslationReviewReportItem, Segment.display_index)
        .outerjoin(
            Segment,
            (Segment.file_record_id == TranslationReviewReportItem.file_record_id)
            & (Segment.sentence_id == TranslationReviewReportItem.sentence_id),
        )
        .filter(TranslationReviewReportItem.report_id == report_id)
        .order_by(
            TranslationReviewReportItem.file_order,
            TranslationReviewReportItem.block_index,
            TranslationReviewReportItem.row_index.nullsfirst(),
            TranslationReviewReportItem.cell_index.nullsfirst(),
            TranslationReviewReportItem.sequence_index,
            TranslationReviewReportItem.sentence_id,
            TranslationReviewReportItem.category_index,
        )
        .all()
    )

    merge_display_offsets: dict[UUID, int] = {}
    report = db.query(TranslationReviewReport).filter(
        TranslationReviewReport.id == report_id,
    ).first()
    if report and report.scope == "merge_view" and report.merge_view_id:
        view = db.query(ProjectMergeView).filter(
            ProjectMergeView.id == report.merge_view_id,
        ).first()
        if view:
            view_files = load_view_file_records(db, view)
            file_ids = [file_record.id for file_record in view_files]
            segment_counts = {}
            if file_ids:
                segment_counts = {
                    file_id: int(count or 0)
                    for file_id, count in db.query(
                        Segment.file_record_id,
                        func.count(Segment.id),
                    ).filter(
                        Segment.file_record_id.in_(file_ids),
                    ).group_by(Segment.file_record_id).all()
                }
            offset = 0
            for file_record in view_files:
                merge_display_offsets[file_record.id] = offset
                offset += segment_counts.get(file_record.id, 0)

    items: list[TranslationReviewReportItem] = []
    for item, current_display_index in rows:
        local_display_index = current_display_index
        if local_display_index is not None:
            local_display_index = int(local_display_index)
            item.display_index = local_display_index
        if item.file_record_id in merge_display_offsets and (local_display_index is not None):
            item.display_index = (
                merge_display_offsets[item.file_record_id] + local_display_index
                if local_display_index >= 0
                else -1
            )
        items.append(item)
    return items



def load_agent_runs(db: Session, report_id: UUID) -> list[TranslationReviewAgentRun]:
    return (
        db.query(TranslationReviewAgentRun)
        .filter(TranslationReviewAgentRun.report_id == report_id)
        .order_by(TranslationReviewAgentRun.category_index).all()
    )


# ─────────────────────────────────────────────────────────────
# 应用 / 恢复 / 拒绝 / 忽略
# ─────────────────────────────────────────────────────────────

def apply_item(
    db: Session,
    item: TranslationReviewReportItem,
    current_user: User,
    apply_batch_id: UUID | None = None,
) -> str:
    if item.apply_mode == "manual":
        return "manual_only"
    if item.locate_status in _LOCATE_UNAPPLICABLE:
        return "manual_only"
    segment = db.query(Segment).filter(
        Segment.file_record_id == item.file_record_id,
        Segment.sentence_id == item.sentence_id,
    ).first()
    if not segment:
        item.status = _ITEM_STALE
        db.flush()
        return "stale"
    latest_target = segment.target_text or ""
    if item.apply_mode == "anchor":
        anchor = item.replace_anchor or ""
        if not anchor or latest_target.count(anchor) != 1:
            item.status = _ITEM_STALE
            db.flush()
            return "stale"
        new_target = latest_target.replace(anchor, item.suggested_value or "", 1)
    else:
        new_target = item.suggested_target_text or ""
        if not new_target:
            return "manual_only"
    item.original_target_text = latest_target
    item.applied = True
    item.applied_at = datetime.utcnow()
    item.apply_batch_id = apply_batch_id
    item.status = _ITEM_APPLIED
    update_segment_by_sentence_id(
        db, file_record_id=item.file_record_id, sentence_id=item.sentence_id,
        target_text=new_target, source="manual", current_user=current_user,
        track_revision=True, defer_commit=True,
    )
    db.flush()
    return "applied"


def restore_item(db: Session, item: TranslationReviewReportItem, current_user: User) -> bool:
    if not item.applied or not item.original_target_text:
        return False
    update_segment_by_sentence_id(
        db, file_record_id=item.file_record_id, sentence_id=item.sentence_id,
        target_text=item.original_target_text, source="manual", current_user=current_user,
        track_revision=True, defer_commit=True,
    )
    item.applied = False
    item.applied_at = None
    item.apply_batch_id = None
    item.status = _ITEM_OPEN
    db.flush()
    return True


def reject_item(db: Session, item: TranslationReviewReportItem) -> None:
    item.status = _ITEM_REJECTED
    db.flush()


def set_items_ignored(
    db: Session, item_ids: list[UUID], ignored: bool, current_user: User
) -> int:
    now = datetime.utcnow()
    items = db.query(TranslationReviewReportItem).filter(
        TranslationReviewReportItem.id.in_(item_ids)
    ).all()
    changed = 0
    for item in items:
        new_status = _ITEM_IGNORED if ignored else _ITEM_OPEN
        if item.status == new_status:
            continue
        item.status = new_status
        item.ignored_by_id = current_user.id if ignored else None
        item.ignored_at = now if ignored else None
        changed += 1
    if changed:
        db.flush()
    return changed


# ─────────────────────────────────────────────────────────────
# 批量应用 / 撤销
# ─────────────────────────────────────────────────────────────

def apply_batch(
    db: Session, report_id: UUID, mode: str, current_user: User, *,
    category_key: str | None = None, item_ids: list[UUID] | None = None,
) -> dict[str, Any]:
    batch_id = uuid4()
    items = _select_batch_items(db, report_id, mode, category_key=category_key, item_ids=item_ids)
    applied = stale = skipped = 0
    for item in items:
        result = apply_item(db, item, current_user, apply_batch_id=batch_id)
        if result == "applied":
            applied += 1
        elif result == "stale":
            stale += 1
        else:
            skipped += 1
    if applied > 0:
        _refresh_active_counts(db, report_id)
    db.commit()
    return {"applied_count": applied, "stale_count": stale, "skipped_count": skipped,
            "apply_batch_id": str(batch_id)}


def _select_batch_items(
    db: Session, report_id: UUID, mode: str, *,
    category_key: str | None, item_ids: list[UUID] | None,
) -> list[TranslationReviewReportItem]:
    q = db.query(TranslationReviewReportItem).filter(
        TranslationReviewReportItem.report_id == report_id,
        TranslationReviewReportItem.status == _ITEM_OPEN,
        TranslationReviewReportItem.apply_mode != "manual",
        TranslationReviewReportItem.locate_status.in_([_LOCATE_OK, _LOCATE_NORM]),
        TranslationReviewReportItem.severity != "suggestion",
    )
    if mode == "high_confidence":
        q = q.filter(TranslationReviewReportItem.confidence == "high",
                     TranslationReviewReportItem.apply_mode == "anchor")
    elif mode == "category":
        if not category_key:
            return []
        q = q.filter(TranslationReviewReportItem.category_key == category_key,
                     TranslationReviewReportItem.confidence == "high",
                     TranslationReviewReportItem.apply_mode == "anchor")
    elif mode == "selected":
        if not item_ids:
            return []
        q = q.filter(TranslationReviewReportItem.id.in_(item_ids))
    else:
        return []
    return q.all()


def undo_batch(
    db: Session, report_id: UUID, current_user: User, *, apply_batch_id: UUID | None = None,
) -> dict[str, int]:
    q = db.query(TranslationReviewReportItem).filter(
        TranslationReviewReportItem.report_id == report_id,
        TranslationReviewReportItem.status == _ITEM_APPLIED,
        TranslationReviewReportItem.applied == True,  # noqa: E712
    )
    if apply_batch_id:
        q = q.filter(TranslationReviewReportItem.apply_batch_id == apply_batch_id)
    else:
        latest = (
            db.query(TranslationReviewReportItem.apply_batch_id)
            .filter(TranslationReviewReportItem.report_id == report_id,
                    TranslationReviewReportItem.apply_batch_id.isnot(None))
            .order_by(TranslationReviewReportItem.applied_at.desc())
            .first()
        )
        if not latest or not latest[0]:
            return {"restored_count": 0}
        apply_batch_id = latest[0]
        q = q.filter(TranslationReviewReportItem.apply_batch_id == apply_batch_id)
    items = q.all()
    restored = sum(1 for item in items if restore_item(db, item, current_user))
    if restored > 0:
        _refresh_active_counts(db, report_id)
    db.commit()
    return {"restored_count": restored}


def _refresh_active_counts(db: Session, report_id: UUID) -> None:
    items = load_report_items(db, report_id)
    report = get_report(db, report_id)
    if not report:
        return
    report.active_issue_count = sum(1 for i in items if i.status == _ITEM_OPEN)
    report.applied_count = sum(1 for i in items if i.status == _ITEM_APPLIED)
    report.ignored_count = sum(1 for i in items if i.status == _ITEM_IGNORED)
    db.flush()


# ─────────────────────────────────────────────────────────────
# 序列化
# ─────────────────────────────────────────────────────────────

def serialize_item(item: TranslationReviewReportItem) -> dict[str, Any]:
    def _j(v):
        try: return json.loads(v) if v else []
        except: return []
    return {
        "id": str(item.id),
        "report_id": str(item.report_id),
        "file_record_id": str(item.file_record_id),
        "sentence_id": item.sentence_id,
        "file_name": item.file_name,
        "file_order": item.file_order,
        "display_index": item.display_index,
        "category_key": item.category_key,
        "category_index": item.category_index,
        "category_label": item.category_key,  # 由 LLM 给出，直接用
        "rule_ref": item.rule_ref,
        "severity": item.severity,
        "origin": item.origin,
        "source_text": item.source_text,
        "target_text": item.target_text,
        "quote": item.quote,
        "quote_start": item.quote_start,
        "quote_end": item.quote_end,
        "locate_status": item.locate_status,
        "replace_anchor": item.replace_anchor,
        "suggested_value": item.suggested_value,
        "suggested_target_text": item.suggested_target_text,
        "reason": item.reason,
        "confidence": item.confidence,
        "citations": _j(item.citations),
        "apply_mode": item.apply_mode,
        "applied": item.applied,
        "applied_at": item.applied_at.isoformat() if item.applied_at else None,
        "apply_batch_id": str(item.apply_batch_id) if item.apply_batch_id else None,
        "status": item.status,
        "ignored_at": item.ignored_at.isoformat() if item.ignored_at else None,
        "block_index": item.block_index,
        "row_index": item.row_index,
        "cell_index": item.cell_index,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def serialize_agent_run(run: TranslationReviewAgentRun) -> dict[str, Any]:
    return {
        "category_key": run.category_key,
        "category_index": run.category_index,
        "label": run.category_key,
        "mode": run.mode,
        "input_segment_count": run.input_segment_count,
        "ai_input_count": run.ai_input_count,
        "program_finding_count": run.program_finding_count,
        "ai_finding_count": run.ai_finding_count,
        "dropped_count": run.dropped_count,
        "status": run.status,
        "error_message": run.error_message,
        "web_search_requests": run.web_search_requests,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def serialize_report(
    report: TranslationReviewReport,
    items: list[TranslationReviewReportItem],
    runs: list[TranslationReviewAgentRun],
) -> dict[str, Any]:
    def _j(v): 
        try: return json.loads(v) if v else {}
        except: return {}
    def _jl(v):
        try: return json.loads(v) if v else []
        except: return []
    return {
        "id": str(report.id),
        "project_id": str(report.project_id) if report.project_id else None,
        "file_record_id": str(report.file_record_id) if report.file_record_id else None,
        "merge_view_id": str(report.merge_view_id) if report.merge_view_id else None,
        "scope": report.scope,
        "segment_scope": report.segment_scope,
        "enabled_categories": _jl(report.enabled_categories),  # LLM 自动分类后的类别列表
        "file_ids": _jl(report.file_ids),
        "total_files": report.total_files,
        "total_segments": report.total_segments,
        "checked_segments": report.checked_segments,
        "category_counts": _j(report.category_counts),
        "file_counts": _j(report.file_counts),
        "issue_count": report.issue_count,
        "active_issue_count": report.active_issue_count,
        "applied_count": report.applied_count,
        "ignored_count": report.ignored_count,
        "multi_category_segment_count": report.multi_category_segment_count,
        "provider": report.provider,
        "model": report.model,
        "web_verify_provider": report.web_verify_provider,
        "web_search_requests": report.web_search_requests,
        "task_id": report.task_id,
        "status": report.status,
        "progress": _j(report.progress),
        "failed_categories": _jl(report.failed_categories),
        "error_message": report.error_message,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "finished_at": report.finished_at.isoformat() if report.finished_at else None,
        "items": [serialize_item(i) for i in items],
        "agent_runs": [serialize_agent_run(r) for r in runs],
    }


# ─────────────────────────────────────────────────────────────
# arq worker 入口
# ─────────────────────────────────────────────────────────────

async def translation_review_job(ctx: dict, report_id: str) -> None:
    import json as _json
    from uuid import UUID as _UUID
    from app.database import SessionLocal
    from app.models import (
        TranslationReviewReport, FileRecord, ProjectMergeView, Project
    )
    from app.services.merge_view_service import load_view_file_records

    logger.info("translation_review_job started report_id=%s", report_id)
    db = SessionLocal()
    try:
        rid = _UUID(report_id)
        report = db.query(TranslationReviewReport).filter(
            TranslationReviewReport.id == rid
        ).first()
        if not report:
            logger.error("translation_review_job: report %s not found", report_id)
            return

        # 获取规则文本
        project = db.query(Project).filter(Project.id == report.project_id).first() if report.project_id else None
        rules_text = (project.translation_rules or "") if project else ""

        # 获取文件列表
        if report.scope == "merge_view" and report.merge_view_id:
            view = db.query(ProjectMergeView).filter(ProjectMergeView.id == report.merge_view_id).first()
            files = load_view_file_records(db, view) if view else []
        else:
            fr = db.query(FileRecord).filter(FileRecord.id == report.file_record_id).first()
            files = [fr] if fr else []

        if not files:
            report.status = "failed"
            report.error_message = "文件不存在或已被删除"
            db.commit()
            return

        file_order_map = {f.id: i for i, f in enumerate(files)}

        await run_review_with_rules(
            db, report, files, file_order_map, rules_text,
            segment_scope=report.segment_scope or "all",
            provider=report.provider or "auto",
            model=report.model or None,
        )
        logger.info("translation_review_job completed report_id=%s", report_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("translation_review_job failed: %s", exc)
        try:
            report = db.query(TranslationReviewReport).filter(
                TranslationReviewReport.id == report_id
            ).first()
            if report:
                report.status = "failed"
                report.error_message = str(exc)[:500]
                report.finished_at = datetime.utcnow()
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
