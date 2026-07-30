"""
Orchestrator — 协调 10 个 Agent 完成全量检查

公开函数：
    run_full_review(db, report, files, file_order_map, ...) → None
    rerun_categories(db, report, files, file_order_map, category_keys, ...) → None
"""
from __future__ import annotations

import importlib
import json
import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import FileRecord, TranslationReviewAgentRun, TranslationReviewReport, User
from app.services.translation_review.agent_runner import run_ai_batch
from app.services.translation_review.payload import build_payloads_for_file, build_terms_context
from app.services.translation_review.program_rules.symbols import Finding
from app.services.translation_review.registry import CategoryAgent, get_agents
from app.services.translation_review.service import (
    _finalize_report,
    _finding_to_item,
    _locate_anchor,
    _update_report_counts,
    _ITEM_OPEN,
    load_report_items,
)

logger = logging.getLogger(__name__)

_PROMPT_MODULES = {
    "tense":         "app.services.translation_review.prompts.00_tense",
    "symbols":       "app.services.translation_review.prompts.01_symbols",
    "casing":        "app.services.translation_review.prompts.02_casing",
    "number_format": "app.services.translation_review.prompts.03_number_format",
    "proper_noun":   "app.services.translation_review.prompts.04_proper_noun",
    "fixed_syntax":  "app.services.translation_review.prompts.05_fixed_syntax",
    "noun_merge":    "app.services.translation_review.prompts.06_noun_merge",
    "omission":      "app.services.translation_review.prompts.07_omission",
    "comprehension": "app.services.translation_review.prompts.08_comprehension",
    "syntax_polish": "app.services.translation_review.prompts.09_syntax",
}


def _get_prompt_builder(key: str):
    mod_path = _PROMPT_MODULES.get(key)
    if not mod_path:
        return None
    mod = importlib.import_module(mod_path)
    return getattr(mod, "build_messages", None)


async def run_full_review(
    db: Session,
    report: TranslationReviewReport,
    files: list[FileRecord],
    file_order_map: dict[UUID, int],
    *,
    segment_scope: str = "all",
    enabled_keys: list[str] | None = None,
    provider: str = "auto",
    model: str | None = None,
    web_verify_provider: str = "none",
) -> None:
    """
    完整检查：程序规则 + LLM（所有 AI 类别）。
    """
    from app.models import TranslationReviewReportItem
    agents = get_agents(enabled_keys)
    category_counts: dict[str, int] = {}
    file_counts: dict[str, int] = {}
    all_items: list = []
    failed_categories: list[str] = []

    total_cats = len(agents)
    for cat_idx, agent in enumerate(agents):
        _update_progress(db, report, agent.key, cat_idx, total_cats, "running")

        run = _ensure_agent_run(db, report.id, agent)

        # ── 1. 程序规则 ──────────────────────────────────────────
        if agent.mode in ("program_then_ai", "program_only") and agent.program_rule:
            prog_items = _run_program_rules_for_agent(
                db, report, files, file_order_map, agent, segment_scope
            )
            for item in prog_items:
                db.add(item)
                all_items.append(item)
                _tally(category_counts, file_counts, item)
            run.program_finding_count = len(prog_items)

        # ── 2. LLM ───────────────────────────────────────────────
        if agent.mode in ("program_then_ai", "ai_only"):
            prompt_builder = _get_prompt_builder(agent.key)
            if not prompt_builder:
                logger.warning("no prompt module for agent %s, skipping LLM", agent.key)
                run.status = "skipped_no_prompt"
                run.finished_at = datetime.utcnow()
                db.flush()
                continue

            # 按文件分批装配 payload（§19.1 严格不跨文件）
            all_payloads: list[dict] = []
            seq = run.program_finding_count  # 接着程序规则的 seq 往下排
            for file_record in files:
                terms = build_terms_context(file_record) if agent.needs_terms else ""
                file_payloads = build_payloads_for_file(
                    db, file_record, file_order_map.get(file_record.id, 0),
                    segment_scope=segment_scope,
                    needs_context=agent.needs_context,
                    needs_terms=agent.needs_terms,
                    terms_context=terms,
                    seq_offset=seq,
                )
                all_payloads.extend(file_payloads)
                seq += len(file_payloads)

            run.input_segment_count += len(all_payloads)

            # 短路过滤
            if agent.ai_input_filter:
                filtered = agent.ai_input_filter(all_payloads)
            else:
                filtered = all_payloads

            run.ai_input_count = len(filtered)

            if not filtered:
                run.status = "skipped_no_candidate"
                run.finished_at = datetime.utcnow()
                db.flush()
                _update_progress(db, report, agent.key, cat_idx, total_cats, "done")
                continue

            # 联网工具
            web_tools, extra_body = _build_web_tools(web_verify_provider, agent)

            # 调用 AI
            findings, stats = await run_ai_batch(
                filtered,
                prompt_builder=prompt_builder,
                batch_size=agent.batch_size,
                provider=provider,
                model=model,
                web_tools=web_tools,
                extra_body=extra_body,
            )

            run.llm_request_count = stats["llm_request_count"]
            run.retry_count = stats["retry_count"]
            run.dropped_count = stats["dropped_count"]
            run.web_search_requests = stats["web_search_requests"]

            # 把 AI findings 转为 items
            seq_to_payload = {p["seq"]: p for p in filtered}
            ai_items = _ai_findings_to_items(
                report, findings, seq_to_payload, files, file_order_map, agent
            )
            for item in ai_items:
                db.add(item)
                all_items.append(item)
                _tally(category_counts, file_counts, item)
            run.ai_finding_count = len(ai_items)

            # 更新联网用量到 report
            if stats["web_search_requests"] > 0:
                report.web_search_requests = (report.web_search_requests or 0) + stats["web_search_requests"]

            if stats["status"] in ("api_error", "rate_limited", "parse_failed"):
                run.status = "partial"
                failed_categories.append(agent.key)
            else:
                run.status = "ok"

        else:
            run.status = "ok"

        run.finished_at = datetime.utcnow()
        db.flush()
        _update_progress(db, report, agent.key, cat_idx, total_cats, "done",
                         finding_count=run.program_finding_count + run.ai_finding_count)

    db.flush()
    _update_report_counts(db, report, category_counts, file_counts, all_items)

    if failed_categories:
        report.failed_categories = json.dumps(failed_categories, ensure_ascii=False)
    status = "partial_failed" if failed_categories else "completed"
    _finalize_report(db, report, status=status)
    db.commit()


# ─── 内部工具 ─────────────────────────────────────────────

def _run_program_rules_for_agent(
    db: Session,
    report: TranslationReviewReport,
    files: list[FileRecord],
    file_order_map: dict,
    agent: CategoryAgent,
    segment_scope: str,
) -> list:
    """对所有文件跑单个 agent 的程序规则，返回 item 列表（未 add 到 db）。"""
    from app.services.file_record_service import list_segments_for_file_record
    from app.services.translation_review.service import _load_segments_for_scope

    items = []
    for file_record in files:
        file_order = file_order_map.get(file_record.id, 0)
        segments = _load_segments_for_scope(db, file_record, segment_scope)
        for segment in segments:
            block_type = getattr(segment, "block_type", "paragraph") or "paragraph"
            findings = agent.program_rule(
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
                _locate_anchor(item)
                items.append(item)
    return items


def _ai_findings_to_items(
    report: TranslationReviewReport,
    findings: list[dict],
    seq_to_payload: dict[int, dict],
    files: list[FileRecord],
    file_order_map: dict,
    agent: CategoryAgent,
) -> list:
    """把 AI 返回的 findings 转为 TranslationReviewReportItem 列表。"""
    from app.models import Segment
    # 构建 file_record 查找表
    fr_by_id = {str(f.id): f for f in files}

    items = []
    for raw in findings:
        if raw.get("_missing") or not raw.get("has_issue"):
            continue
        seq = raw.get("seq")
        payload = seq_to_payload.get(seq)
        if not payload:
            continue

        frid = payload.get("file_record_id", "")
        file_record = fr_by_id.get(frid)
        if not file_record:
            continue

        file_order = file_order_map.get(file_record.id, 0)

        for finding in raw.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            # 构造 minimal segment-like 对象从 payload
            _seg = _MockSegment(payload)
            item = _finding_to_item(
                report=report,
                segment=_seg,
                file_record=file_record,
                file_order=file_order,
                agent=agent,
                finding=finding,
                origin="ai",
            )
            _locate_anchor(item)
            items.append(item)
    return items


class _MockSegment:
    """payload dict 的轻量包装，让 _finding_to_item 复用。"""
    def __init__(self, p: dict) -> None:
        self.id = None
        self.sentence_id = p.get("sid", "")
        self.source_text = p.get("source_text", "")
        self.target_text = p.get("target_text", "")
        self.target_html = None
        self.block_type = p.get("block_type", "paragraph")
        self.block_index = p.get("block_index", 0)
        self.row_index = p.get("row_index", None)
        self.cell_index = p.get("cell_index", None)
        self.display_index = p.get("display_index", -1)
        self.sequence_index = p.get("sequence_index", -1)
        self.status = "none"


def _tally(
    category_counts: dict[str, int],
    file_counts: dict[str, int],
    item: Any,
) -> None:
    category_counts[item.category_key] = category_counts.get(item.category_key, 0) + 1
    fid = str(item.file_record_id)
    file_counts[fid] = file_counts.get(fid, 0) + 1


def _ensure_agent_run(
    db: Session,
    report_id: UUID,
    agent: CategoryAgent,
) -> TranslationReviewAgentRun:
    from app.services.translation_review.service import _make_agent_run
    existing = (
        db.query(TranslationReviewAgentRun)
        .filter(
            TranslationReviewAgentRun.report_id == report_id,
            TranslationReviewAgentRun.category_key == agent.key,
        )
        .first()
    )
    if existing:
        existing.started_at = datetime.utcnow()
        existing.finished_at = None
        existing.program_finding_count = 0
        existing.ai_finding_count = 0
        existing.dropped_count = 0
        existing.status = "ok"
        existing.error_message = ""
        db.flush()
        return existing
    run = _make_agent_run(report_id, agent)
    db.add(run)
    db.flush()
    return run


def _update_progress(
    db: Session,
    report: TranslationReviewReport,
    category_key: str,
    cat_idx: int,
    total_cats: int,
    status: str,
    finding_count: int = 0,
) -> None:
    try:
        progress = json.loads(report.progress or "{}")
    except Exception:
        progress = {}

    cats = progress.get("categories") or []
    updated = False
    for cat in cats:
        if cat.get("key") == category_key:
            cat["status"] = status
            if finding_count:
                cat["finding_count"] = finding_count
            updated = True
            break
    if not updated:
        cats.append({"key": category_key, "status": status, "finding_count": finding_count})

    overall = round((cat_idx + (1 if status == "done" else 0)) / max(total_cats, 1) * 100)
    progress["overall_percent"] = overall
    progress["current_category"] = category_key
    progress["categories"] = cats
    progress["updated_at"] = datetime.utcnow().isoformat()
    report.progress = json.dumps(progress, ensure_ascii=False)
    db.flush()


def _build_web_tools(
    web_verify_provider: str,
    agent: CategoryAgent,
) -> tuple[list[dict] | None, dict | None]:
    """构造 OpenRouter web_search tool（仅在条件满足时）。"""
    if web_verify_provider != "openrouter" or not agent.allow_web_verify:
        return None, None
    from app.config import get_settings
    config = get_settings()
    tools = [{
        "type": "openrouter:web_search",
        "engine": config.translation_review_web_search_engine,
        "max_results": config.translation_review_web_search_max_results,
        "max_uses": config.translation_review_web_search_max_uses,
        "max_total_results": config.translation_review_web_search_max_results * config.translation_review_web_search_max_uses,
        "search_context_size": "low",
        **({"allowed_domains": config.translation_review_web_allow_domains}
           if config.translation_review_web_allow_domains else {}),
    }]
    extra_body = {"max_tool_calls": config.translation_review_web_search_max_uses * 2}
    return tools, extra_body
