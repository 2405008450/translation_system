"""
翻译内容校对 — 核心服务层（阶段 1：程序规则 + CRUD）

公开函数：
    create_review_task       建 report 行，入队 arq（或回退本地）
    run_program_rules_only   仅跑程序规则（阶段 1 端点调用）
    get_report               读取报告（含 items / agent_runs）
    list_file_reports        单文件最近报告
    list_merge_view_reports  合并视图最近报告
    apply_item               应用单条修改建议
    restore_item             恢复单条
    reject_item              拒绝单条
    set_items_ignored        批量忽略/恢复
    apply_batch              批量应用
    undo_batch               撤销上一次批量
    serialize_report         序列化报告（含 items / agent_runs）
    serialize_item           序列化单条
    recompute_report_counts  重新统计并写回 report
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

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
from app.services.merge_view_service import serialize_file_ids
from app.services.translation_review.registry import AGENT_BY_KEY, CategoryAgent, get_agents

logger = logging.getLogger(__name__)

# ─── 常量 ────────────────────────────────────────────────

_STATUS_RUNNING = "running"
_STATUS_COMPLETED = "completed"
_STATUS_PARTIAL = "partial_failed"
_STATUS_FAILED = "failed"

_ITEM_OPEN = "open"
_ITEM_APPLIED = "applied"
_ITEM_REJECTED = "rejected"
_ITEM_IGNORED = "ignored"
_ITEM_STALE = "stale"

_LOCATE_OK = "ok"
_LOCATE_NORM = "normalized"
_LOCATE_UNAPPLICABLE = ("unlocatable", "ambiguous")

# ─── 建 report + 入队 ─────────────────────────────────────

def create_review_report(
    db: Session,
    *,
    project: Project | None,
    files: list[FileRecord],
    file_order_map: dict[UUID, int],
    merge_view: ProjectMergeView | None,
    current_user: User,
    segment_scope: str = "all",
    enabled_categories: list[str] | None = None,
    provider: str = "auto",
    model: str = "",
    web_verify_provider: str = "none",
) -> TranslationReviewReport:
    """
    新建 report 行（status='running'），返回对象。
    file_order_map: {file_record_id: 排序下标}（按视图文件顺序）
    """
    file_ids_text = serialize_file_ids([f.id for f in files])
    categories = enabled_categories or [a.key for a in get_agents()]

    report = TranslationReviewReport(
        project_id=project.id if project else None,
        file_record_id=(files[0].id if len(files) == 1 and not merge_view else None),
        merge_view_id=merge_view.id if merge_view else None,
        created_by_id=current_user.id,
        scope="merge_view" if merge_view else "file",
        segment_scope=segment_scope,
        enabled_categories=json.dumps(categories, ensure_ascii=False),
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


# ─── 程序规则阶段（阶段 1 主逻辑） ────────────────────────────

def run_program_rules_only(
    db: Session,
    report: TranslationReviewReport,
    files: list[FileRecord],
    file_order_map: dict[UUID, int],
    segment_scope: str = "all",
    enabled_keys: list[str] | None = None,
) -> None:
    """
    仅跑程序规则，不调用 LLM。
    结果直接落库为 TranslationReviewReportItem。
    """
    agents = [
        a for a in get_agents(enabled_keys)
        if a.mode in ("program_then_ai", "program_only") and a.program_rule is not None
    ]
    if not agents:
        _finalize_report(db, report, status=_STATUS_COMPLETED)
        return

    all_items: list[TranslationReviewReportItem] = []
    category_counts: dict[str, int] = {}
    file_counts: dict[str, int] = {}
    agent_runs: list[TranslationReviewAgentRun] = []

    for agent in agents:
        run = _make_agent_run(report.id, agent)
        agent_runs.append(run)
        db.add(run)
        run_findings: list[TranslationReviewReportItem] = []

        for file_record in files:
            segments = _load_segments_for_scope(db, file_record, segment_scope)
            file_order = file_order_map.get(file_record.id, 0)
            run.input_segment_count += len(segments)

            for segment in segments:
                block_type = getattr(segment, "block_type", "paragraph") or "paragraph"
                findings = agent.program_rule(  # type: ignore[misc]
                    segment.source_text or "",
                    segment.target_text or "",
                    block_type=block_type,
                )
                for f in findings:
                    item = _finding_to_item(
                        report=report,
                        segment=segment,
                        file_record=file_record,
                        file_order=file_order,
                        agent=agent,
                        finding=f,
                        origin="program",
                    )
                    run_findings.append(item)

        # 锚点定位
        for item in run_findings:
            if item.replace_anchor and item.locate_status == _LOCATE_OK:
                _locate_anchor(item)

        run.program_finding_count = len(run_findings)
        run.status = "ok"
        run.finished_at = datetime.utcnow()

        for item in run_findings:
            db.add(item)
            all_items.append(item)
            category_counts[agent.key] = category_counts.get(agent.key, 0) + 1
            fid = str(item.file_record_id)
            file_counts[fid] = file_counts.get(fid, 0) + 1

    db.flush()

    # 重新统计 report
    _update_report_counts(db, report, category_counts, file_counts, all_items)
    _finalize_report(db, report, status=_STATUS_COMPLETED)
    db.commit()


# ─── 内部工具 ─────────────────────────────────────────────

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
    return segments  # "all"


def _make_agent_run(report_id: UUID, agent: CategoryAgent) -> TranslationReviewAgentRun:
    return TranslationReviewAgentRun(
        report_id=report_id,
        category_key=agent.key,
        category_index=agent.index,
        mode=agent.mode,
        input_segment_count=0,
        ai_input_count=0,
        batch_count=0,
        llm_request_count=0,
        retry_count=0,
        web_search_requests=0,
        program_finding_count=0,
        ai_finding_count=0,
        dropped_count=0,
        status="ok",
        error_message="",
        provider="",
        model="",
        started_at=datetime.utcnow(),
    )


def _finding_to_item(
    *,
    report: TranslationReviewReport,
    segment: Segment,
    file_record: FileRecord,
    file_order: int,
    agent: CategoryAgent,
    finding: dict,
    origin: str = "ai",
) -> TranslationReviewReportItem:
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
        category_key=agent.key,
        category_index=agent.index,
        rule_ref=finding.get("rule_ref", ""),
        severity=agent.severity,
        origin=origin,
        source_text=(segment.source_text or "")[:4000],
        target_text=(segment.target_text or "")[:4000],
        quote=(finding.get("quote") or "")[:500],
        replace_anchor=(finding.get("replace_anchor") or "")[:500],
        suggested_value=(finding.get("suggested_value") or "")[:2000],
        suggested_target_text=(finding.get("suggested_target_text") or "")[:4000],
        reason=(finding.get("reason") or "")[:1000],
        confidence=finding.get("confidence", "medium"),
        apply_mode=agent.apply_mode if finding.get("replace_anchor") else "manual",
        locate_status=_LOCATE_OK,
        block_index=getattr(segment, "block_index", 0) or 0,
        row_index=getattr(segment, "row_index", None),
        cell_index=getattr(segment, "cell_index", None),
        status=_ITEM_OPEN,
    )


def _locate_anchor(item: TranslationReviewReportItem) -> None:
    """
    在 item.target_text 中定位 replace_anchor，写入 quote_start / quote_end / locate_status。
    五道关：精确 → 归一化 → unlocatable；唯一命中才允许 apply_mode=anchor。
    """
    target = item.target_text or ""
    anchor = item.replace_anchor or ""
    if not anchor:
        item.locate_status = "unlocatable"
        item.apply_mode = "manual"
        return

    # 关卡 1：精确匹配
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

    # 关卡 2：归一化匹配（统一空白、大小写、引号/破折号变体）
    import unicodedata
    import re

    def _normalize(s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip()
        s = s.replace("\u2018", "'").replace("\u2019", "'")
        s = s.replace("\u201c", '"').replace("\u201d", '"')
        s = s.replace("\u2013", "-").replace("\u2014", "-")
        return s.lower()

    norm_target = _normalize(target)
    norm_anchor = _normalize(anchor)
    norm_count = norm_target.count(norm_anchor)
    if norm_count == 1:
        norm_start = norm_target.index(norm_anchor)
        # 逼近原始偏移（归一化后偏移不精确，用原文查找作补偿）
        approx_start = target.lower().find(anchor.lower())
        if approx_start >= 0:
            item.quote_start = approx_start
            item.quote_end = approx_start + len(anchor)
        else:
            item.quote_start = norm_start
            item.quote_end = norm_start + len(anchor)
        item.locate_status = _LOCATE_NORM
        return

    # 关卡 3：无法定位
    item.locate_status = "unlocatable"
    item.apply_mode = "manual"


def _update_report_counts(
    db: Session,
    report: TranslationReviewReport,
    category_counts: dict[str, int],
    file_counts: dict[str, int],
    all_items: list[TranslationReviewReportItem],
) -> None:
    issue_count = len(all_items)
    active_count = sum(1 for i in all_items if i.status == _ITEM_OPEN)

    # 多类别句段数
    from collections import Counter
    seg_counter: Counter = Counter(i.sentence_id for i in all_items)
    multi_count = sum(1 for c in seg_counter.values() if c >= 2)

    report.issue_count = issue_count
    report.active_issue_count = active_count
    report.multi_category_segment_count = multi_count
    report.category_counts = json.dumps(category_counts, ensure_ascii=False)
    report.file_counts = json.dumps(file_counts, ensure_ascii=False)
    db.flush()


def _finalize_report(
    db: Session,
    report: TranslationReviewReport,
    status: str = _STATUS_COMPLETED,
) -> None:
    report.status = status
    report.finished_at = datetime.utcnow()
    db.flush()


# ─── 查询 ─────────────────────────────────────────────────

def get_report(
    db: Session,
    report_id: UUID,
) -> TranslationReviewReport | None:
    return db.query(TranslationReviewReport).filter(
        TranslationReviewReport.id == report_id
    ).first()


def list_file_reports(
    db: Session,
    file_record_id: UUID,
    limit: int = 1,
) -> list[TranslationReviewReport]:
    safe_limit = min(max(int(limit), 1), 20)
    return (
        db.query(TranslationReviewReport)
        .filter(
            TranslationReviewReport.scope == "file",
            TranslationReviewReport.file_record_id == file_record_id,
        )
        .order_by(
            TranslationReviewReport.created_at.desc(),
            TranslationReviewReport.id.desc(),
        )
        .limit(safe_limit)
        .all()
    )


def list_merge_view_reports(
    db: Session,
    merge_view_id: UUID,
    limit: int = 1,
) -> list[TranslationReviewReport]:
    safe_limit = min(max(int(limit), 1), 20)
    return (
        db.query(TranslationReviewReport)
        .filter(
            TranslationReviewReport.scope == "merge_view",
            TranslationReviewReport.merge_view_id == merge_view_id,
        )
        .order_by(
            TranslationReviewReport.created_at.desc(),
            TranslationReviewReport.id.desc(),
        )
        .limit(safe_limit)
        .all()
    )


def load_report_items(
    db: Session,
    report_id: UUID,
) -> list[TranslationReviewReportItem]:
    """按设计文档 §19.5 排序键返回。"""
    return (
        db.query(TranslationReviewReportItem)
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


def load_agent_runs(
    db: Session,
    report_id: UUID,
) -> list[TranslationReviewAgentRun]:
    return (
        db.query(TranslationReviewAgentRun)
        .filter(TranslationReviewAgentRun.report_id == report_id)
        .order_by(TranslationReviewAgentRun.category_index)
        .all()
    )


# ─── 应用 / 恢复 / 拒绝 / 忽略 ───────────────────────────────

def apply_item(
    db: Session,
    item: TranslationReviewReportItem,
    current_user: User,
    apply_batch_id: UUID | None = None,
) -> str:
    """
    应用单条修改建议。
    返回结果状态：'applied' | 'stale' | 'manual_only'
    """
    if item.apply_mode == "manual":
        return "manual_only"
    if item.locate_status in _LOCATE_UNAPPLICABLE and item.apply_mode == "anchor":
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
    else:  # full
        new_target = item.suggested_target_text or ""
        if not new_target:
            return "manual_only"

    # 写快照后应用
    item.original_target_text = latest_target
    item.applied = True
    item.applied_at = datetime.utcnow()
    item.apply_batch_id = apply_batch_id
    item.status = _ITEM_APPLIED

    update_segment_by_sentence_id(
        db,
        file_record_id=item.file_record_id,
        sentence_id=item.sentence_id,
        target_text=new_target,
        source="manual",
        current_user=current_user,
        track_revision=True,
        defer_commit=True,
    )
    db.flush()
    return "applied"


def restore_item(
    db: Session,
    item: TranslationReviewReportItem,
    current_user: User,
) -> bool:
    if not item.applied or not item.original_target_text:
        return False
    update_segment_by_sentence_id(
        db,
        file_record_id=item.file_record_id,
        sentence_id=item.sentence_id,
        target_text=item.original_target_text,
        source="manual",
        current_user=current_user,
        track_revision=True,
        defer_commit=True,
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
    db: Session,
    item_ids: list[UUID],
    ignored: bool,
    current_user: User,
) -> int:
    now = datetime.utcnow()
    items = (
        db.query(TranslationReviewReportItem)
        .filter(TranslationReviewReportItem.id.in_(item_ids))
        .all()
    )
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


# ─── 批量应用 ─────────────────────────────────────────────

_BATCH_SAFE_MODES = ("anchor",)
_BATCH_SAFE_STATUSES = (_ITEM_OPEN,)
_BATCH_SAFE_SEVERITIES = ("error", "warning")
_BATCH_SAFE_CONFIDENCES = ("high",)


def apply_batch(
    db: Session,
    report_id: UUID,
    mode: str,  # program | high_confidence | category | selected
    current_user: User,
    *,
    category_key: str | None = None,
    item_ids: list[UUID] | None = None,
) -> dict[str, Any]:
    """
    批量应用。返回 {applied_count, stale_count, skipped_count, apply_batch_id}
    """
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
    return {
        "applied_count": applied,
        "stale_count": stale,
        "skipped_count": skipped,
        "apply_batch_id": str(batch_id),
    }


def _select_batch_items(
    db: Session,
    report_id: UUID,
    mode: str,
    *,
    category_key: str | None,
    item_ids: list[UUID] | None,
) -> list[TranslationReviewReportItem]:
    q = db.query(TranslationReviewReportItem).filter(
        TranslationReviewReportItem.report_id == report_id,
        TranslationReviewReportItem.status.in_(_BATCH_SAFE_STATUSES),
        TranslationReviewReportItem.apply_mode != "manual",
        TranslationReviewReportItem.locate_status.in_([_LOCATE_OK, _LOCATE_NORM]),
        TranslationReviewReportItem.severity != "suggestion",  # §句法优化永不批量
    )
    if mode == "program":
        q = q.filter(TranslationReviewReportItem.origin == "program")
    elif mode == "high_confidence":
        q = q.filter(
            TranslationReviewReportItem.confidence == "high",
            TranslationReviewReportItem.apply_mode == "anchor",
        )
    elif mode == "category":
        if not category_key:
            return []
        q = q.filter(
            TranslationReviewReportItem.category_key == category_key,
            TranslationReviewReportItem.confidence == "high",
            TranslationReviewReportItem.apply_mode == "anchor",
        )
    elif mode == "selected":
        if not item_ids:
            return []
        q = q.filter(TranslationReviewReportItem.id.in_(item_ids))
    else:
        return []
    return q.all()


def undo_batch(
    db: Session,
    report_id: UUID,
    current_user: User,
    *,
    apply_batch_id: UUID | None = None,
) -> dict[str, int]:
    """
    撤销上一次（或指定）批量应用。
    """
    q = db.query(TranslationReviewReportItem).filter(
        TranslationReviewReportItem.report_id == report_id,
        TranslationReviewReportItem.status == _ITEM_APPLIED,
        TranslationReviewReportItem.applied == True,  # noqa: E712
    )
    if apply_batch_id:
        q = q.filter(TranslationReviewReportItem.apply_batch_id == apply_batch_id)
    else:
        # 找最近一次 batch
        latest = (
            db.query(TranslationReviewReportItem.apply_batch_id)
            .filter(
                TranslationReviewReportItem.report_id == report_id,
                TranslationReviewReportItem.apply_batch_id.isnot(None),
            )
            .order_by(TranslationReviewReportItem.applied_at.desc())
            .first()
        )
        if not latest or not latest[0]:
            return {"restored_count": 0}
        apply_batch_id = latest[0]
        q = q.filter(TranslationReviewReportItem.apply_batch_id == apply_batch_id)

    items = q.all()
    restored = 0
    for item in items:
        if restore_item(db, item, current_user):
            restored += 1

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


# ─── 序列化 ───────────────────────────────────────────────

def serialize_item(item: TranslationReviewReportItem) -> dict[str, Any]:
    def _j(v: str | None) -> Any:
        try:
            return json.loads(v) if v else []
        except Exception:
            return []

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
        "category_label": AGENT_BY_KEY.get(item.category_key, None) and
                          AGENT_BY_KEY[item.category_key].label or item.category_key,
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
    agent = AGENT_BY_KEY.get(run.category_key)
    return {
        "category_key": run.category_key,
        "category_index": run.category_index,
        "label": agent.label if agent else run.category_key,
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
    def _j(v: str | None) -> Any:
        try:
            return json.loads(v) if v else {}
        except Exception:
            return {}

    def _jl(v: str | None) -> Any:
        try:
            return json.loads(v) if v else []
        except Exception:
            return []

    return {
        "id": str(report.id),
        "project_id": str(report.project_id) if report.project_id else None,
        "file_record_id": str(report.file_record_id) if report.file_record_id else None,
        "merge_view_id": str(report.merge_view_id) if report.merge_view_id else None,
        "scope": report.scope,
        "segment_scope": report.segment_scope,
        "enabled_categories": _jl(report.enabled_categories),
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


def recompute_report_counts(db: Session, report_id: UUID) -> None:
    items = load_report_items(db, report_id)
    report = get_report(db, report_id)
    if not report:
        return
    _refresh_active_counts(db, report_id)
    cat_counts: dict[str, int] = {}
    file_counts: dict[str, int] = {}
    for item in items:
        cat_counts[item.category_key] = cat_counts.get(item.category_key, 0) + 1
        fid = str(item.file_record_id)
        file_counts[fid] = file_counts.get(fid, 0) + 1
    report.issue_count = len(items)
    report.category_counts = json.dumps(cat_counts, ensure_ascii=False)
    report.file_counts = json.dumps(file_counts, ensure_ascii=False)
    db.flush()


# ─── ARQ job（arq worker 调用入口） ────────────────────────

async def translation_review_job(ctx: dict, report_id: str) -> None:
    """
    ARQ worker 调用此函数执行完整翻译内容校对。
    ctx 由 arq 注入（含 db 连接等可选项）。
    """
    import json as _json
    from uuid import UUID as _UUID
    from app.database import SessionLocal
    from app.models import TranslationReviewReport, FileRecord, ProjectMergeView
    from app.services.merge_view_service import load_view_file_records
    from app.services.translation_review.orchestrator import run_full_review

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

        # 解析文件列表
        if report.scope == "merge_view" and report.merge_view_id:
            view = db.query(ProjectMergeView).filter(
                ProjectMergeView.id == report.merge_view_id
            ).first()
            files = load_view_file_records(db, view) if view else []
        else:
            fid = report.file_record_id
            fr = db.query(FileRecord).filter(FileRecord.id == fid).first()
            files = [fr] if fr else []

        if not files:
            report.status = "failed"
            report.error_message = "文件不存在或已被删除"
            db.commit()
            return

        file_order_map = {f.id: i for i, f in enumerate(files)}
        enabled_keys = _json.loads(report.enabled_categories or "[]") or None

        await run_full_review(
            db, report, files, file_order_map,
            segment_scope=report.segment_scope or "all",
            enabled_keys=enabled_keys,
            provider=report.provider or "auto",
            model=report.model or None,
            web_verify_provider=report.web_verify_provider or "none",
        )
        logger.info("translation_review_job completed report_id=%s", report_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("translation_review_job failed report_id=%s error=%s", report_id, exc)
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
