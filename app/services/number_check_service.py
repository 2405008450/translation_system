"""
数字专检服务
============
对文件句段执行"程序检查 + AI 复核"的数值一致性检查，结果落库为
NumberCheckReport / NumberCheckReportItem，并支持按锚点替换的修改与恢复。

设计要点：
  - 程序检查复用 number_check.normalizer_total.compare_numbers（纯函数，中英文通用）。
  - AI 复核复用系统的 llm_service.request_chat_completion，不引入额外依赖。
  - 修改＝按 AI 返回的"替换锚点"在译文中做一次精确替换并写回句段
    （走 update_segment_by_sentence_id，自动产生 revision、version 自增）。
  - 恢复＝写回修改前的译文快照。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import (
    FileRecord,
    NumberCheckReport,
    NumberCheckReportItem,
    Project,
    Segment,
    User,
)
from app.services.file_record_service import (
    list_segments_for_file_record,
    update_segment_by_sentence_id,
)
from app.services.llm_service import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseValidationError,
    request_chat_completion,
)
from app.services.normalizer import normalize_text
from app.services.number_check.normalizer_total import CompareResult, compare_numbers

logger = logging.getLogger(__name__)

ITEM_STATUS_OPEN = "open"
ITEM_STATUS_IGNORED = "ignored"
ITEM_STATUS_MODIFIED = "modified"

_AI_BLOCK_SIZE = 20
_AI_MAX_RETRY = 1

_STATUS_OK = "ok"
_STATUS_PARSE_FAIL = "parse_failed"
_STATUS_EMPTY = "empty_content"
_STATUS_API_ERROR = "api_error"
_STATUS_MISSING = "missing"

_ERROR_SCHEMA = """{
  "替换锚点": "译文中需要被替换的精确字符片段",
  "译文修改建议值": "修正后的译文片段，必须与替换锚点在语境中完全对等，确保直接替换锚点后译文在语法、空格和单位上完全正确。例如锚点为'1 million'，建议值应为'10 million'，严禁只给数字'10'。",
  "is_source_consistent": "true或false — 译文数值是否忠实还原了原文数值",
  "修改理由": "简述违反的具体规则（如数量级错误）"
}"""

_SOURCE_ISSUE_SCHEMA = """{
  "原文数值": "原文中存在问题的数值片段",
  "说明": "原文数值本身的逻辑问题描述"
}"""


# ─────────────────────────────────────────
# 程序检查
# ─────────────────────────────────────────

def _format_program_reason(result: CompareResult) -> str:
    """把数值不一致项拼成可读的错误原因。"""
    parts: list[str] = []
    for mismatch in result.mismatches:
        cn = mismatch.get("cn")
        en = mismatch.get("en")
        cn_text = cn if cn is not None else "—"
        en_text = en if en is not None else "—"
        parts.append(f"原文[{cn_text}] ≠ 译文[{en_text}]")
    if parts:
        return "; ".join(parts)
    return "原文与译文数值不一致"


def _collect_program_issue_drafts(
    db: Session,
    files: list[FileRecord],
) -> tuple[int, list[dict[str, Any]]]:
    """对所有句段做程序检查，返回 (总句段数, 疑似错误草稿列表)。"""
    total_segments = 0
    drafts: list[dict[str, Any]] = []
    for file_record in files:
        segments = list_segments_for_file_record(db, file_record.id)
        total_segments += len(segments)
        for segment in segments:
            target_text = segment.target_text or ""
            # 无译文的句段无法对比数值，跳过
            if not normalize_text(target_text):
                continue
            result = compare_numbers(segment.source_text or "", target_text)
            if result.matched:
                continue
            drafts.append(
                {
                    "file_record": file_record,
                    "segment": segment,
                    "source_numbers": result.cn_numbers,
                    "target_numbers": result.en_numbers,
                    "reason": _format_program_reason(result),
                }
            )
    return total_segments, drafts


# ─────────────────────────────────────────
# AI 复核
# ─────────────────────────────────────────

def _safe_parse_json(content: str) -> tuple[list[dict[str, Any]], str]:
    """解析模型返回的 JSON 数组，返回 (列表, 状态)。"""
    if not content or not content.strip():
        return [], _STATUS_EMPTY

    no_error_hints = ["no error", "no issue", "没有发现", "未发现", "无错误", "all correct", "符合规范"]
    stripped_lower = content.strip().lower()
    if any(hint in stripped_lower for hint in no_error_hints) and "{" not in content:
        return [], _STATUS_OK

    try:
        cleaned = re.sub(r"```json|```", "", content).strip()
        match = re.search(r"\[.*\]", cleaned, re.S)
        candidate = match.group() if match else cleaned
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return parsed, _STATUS_OK
        if isinstance(parsed, dict):
            return [parsed], _STATUS_OK
        return [], _STATUS_PARSE_FAIL
    except Exception:
        return [], _STATUS_PARSE_FAIL


def _build_ai_prompt(combined: str, count: int) -> str:
    return f"""你是翻译数值审校专家。以下是一批原文/译文对（共 {count} 条，用[序号]标记）。

请按两步完成检查：
第一步：通读所有条目，建立整体语境认知（领域、单位体系、量级、货币、百分比惯例、跨条目数值关联）。
第二步：基于整体语境，逐条判断译文数值是否正确，把所有数值不一致处全部找出，区分两类：
- errors：译文数值与原文不符（错译、漏译、多译，严禁四舍五入、严禁序号与单位漏译），【需要修改译文】。一条译文中可能有多个 error，请全部列出。
- source_issues：译文忠实还原了原文，但原文数值本身存在逻辑问题，【不需要修改译文】。

特别注意中文数量单位换算：万=10^4（如 80万吨=800,000吨）、亿=10^8。比对前先写出原文实际数值再与译文对比。

输出 JSON 数组，长度必须与输入条数({count})相同，每项对应同序号条目：
[
  {{"seq": 0, "is_correct": true, "errors": [], "source_issues": []}},
  {{"seq": 1, "is_correct": false, "errors": [{_ERROR_SCHEMA}], "source_issues": [{_SOURCE_ISSUE_SCHEMA}]}}
]

待检查内容：
{combined}
"""


async def _call_ai(
    seq_items: list[tuple[int, str, str]],
    *,
    provider: str,
    model: str | None,
) -> tuple[dict[int, dict[str, Any]], str]:
    """发送一批 (seq, 原文, 译文) 给模型，返回 ({seq: 结果}, 状态)。"""
    lines = [f"[{seq}] 原文: {src}\n[{seq}] 译文: {tgt}" for seq, src, tgt in seq_items]
    combined = "\n\n".join(lines)
    prompt = _build_ai_prompt(combined, len(seq_items))

    try:
        result = await request_chat_completion(
            messages=[
                {"role": "system", "content": "只输出JSON数组"},
                {"role": "user", "content": prompt},
            ],
            provider=provider,
            model_override=model,
            temperature=0,
        )
    except (LLMConfigurationError, LLMRequestError, LLMResponseValidationError) as exc:
        logger.warning("number-check AI call failed: %s", exc)
        return {}, _STATUS_API_ERROR
    except Exception as exc:  # noqa: BLE001
        logger.exception("number-check AI call unexpected error: %s", exc)
        return {}, _STATUS_API_ERROR

    parsed, status = _safe_parse_json(result.content)
    seq_map: dict[int, dict[str, Any]] = {}
    for index, payload in enumerate(parsed):
        if not isinstance(payload, dict):
            continue
        seq = payload.get("seq", seq_items[index][0] if index < len(seq_items) else None)
        if seq is None:
            continue
        try:
            seq_map[int(seq)] = payload
        except (TypeError, ValueError):
            continue
    return seq_map, status


async def _run_ai_for_inputs(
    inputs: list[tuple[int, str, str]],
    *,
    provider: str,
    model: str | None,
) -> dict[int, dict[str, Any]]:
    """对全部输入分块跑 AI，缺失条目补发重试，返回 {seq: 结果}。"""
    seq_map: dict[int, dict[str, Any]] = {}
    last_status: dict[int, str] = {}
    blocks = [inputs[i:i + _AI_BLOCK_SIZE] for i in range(0, len(inputs), _AI_BLOCK_SIZE)]
    for block in blocks:
        block_map, status = await _call_ai(block, provider=provider, model=model)
        seq_map.update(block_map)
        for seq, _, _ in block:
            if seq not in block_map:
                last_status[seq] = status

        for _ in range(_AI_MAX_RETRY):
            missing = [item for item in block if item[0] not in seq_map]
            if not missing:
                break
            retry_map, status = await _call_ai(missing, provider=provider, model=model)
            seq_map.update(retry_map)
            for seq, _, _ in missing:
                if seq not in retry_map:
                    last_status[seq] = status

    # 把仍缺失的标记出来，供调用方写入 ai_error_status
    for seq, status in last_status.items():
        if seq not in seq_map:
            seq_map[seq] = {"_missing": True, "_error_status": status}
    return seq_map


def _apply_ai_result_to_item(item: NumberCheckReportItem, ai_payload: dict[str, Any]) -> None:
    """把单条 AI 结果写入报告项。"""
    item.ai_checked = True
    if ai_payload.get("_missing"):
        item.ai_error_status = str(ai_payload.get("_error_status") or _STATUS_MISSING)
        return

    item.ai_error_status = ""
    errors = ai_payload.get("errors") or []
    source_issues = ai_payload.get("source_issues") or []
    if not isinstance(errors, list):
        errors = []
    if not isinstance(source_issues, list):
        source_issues = []

    item.ai_errors = json.dumps(errors, ensure_ascii=False)
    item.ai_source_issues = json.dumps(source_issues, ensure_ascii=False)
    item.ai_is_correct = bool(ai_payload.get("is_correct", not errors)) and not errors

    if errors and isinstance(errors[0], dict):
        first = errors[0]
        item.replace_anchor = str(first.get("替换锚点") or "")
        item.suggested_value = str(first.get("译文修改建议值") or "")
        item.is_source_consistent = str(first.get("is_source_consistent", "")).strip().lower() == "true"
    else:
        item.replace_anchor = ""
        item.suggested_value = ""
        item.is_source_consistent = bool(source_issues)


def _recompute_report_counts(db: Session, report: NumberCheckReport) -> None:
    items = (
        db.query(NumberCheckReportItem)
        .filter(NumberCheckReportItem.report_id == report.id)
        .all()
    )
    report.program_issue_count = len(items)
    report.ai_issue_count = sum(1 for item in items if item.ai_checked and not item.ai_is_correct)
    report.source_issue_count = sum(
        1 for item in items if _load_json_list(item.ai_source_issues)
    )


# ─────────────────────────────────────────
# 报告生成 / 复核
# ─────────────────────────────────────────

def _persist_number_check_report(
    db: Session,
    *,
    project: Project | None,
    files: list[FileRecord],
    current_user: User | None,
    scope: str,
    total_segments: int,
    drafts: list[dict[str, Any]],
) -> NumberCheckReport:
    """根据程序检查草稿落库为报告及明细。"""
    file_ids = [file_record.id for file_record in files]
    report = NumberCheckReport(
        project_id=project.id if project else None,
        file_record_id=files[0].id if scope == "file" and len(files) == 1 else None,
        created_by_id=getattr(current_user, "id", None),
        scope=scope,
        file_ids=json.dumps([str(file_id) for file_id in file_ids]),
        total_files=len(files),
        total_segments=total_segments,
        checked_segments=total_segments,
        program_issue_count=len(drafts),
        ai_issue_count=0,
        source_issue_count=0,
        ai_checked=False,
        status="completed",
    )
    db.add(report)
    db.flush()

    for draft in drafts:
        segment: Segment = draft["segment"]
        file_record: FileRecord = draft["file_record"]
        db.add(
            NumberCheckReportItem(
                report_id=report.id,
                project_id=project.id if project else None,
                file_record_id=file_record.id,
                segment_id=segment.id,
                sentence_id=segment.sentence_id,
                file_name=file_record.filename,
                source_text=segment.source_text or "",
                target_text=segment.target_text or "",
                source_numbers=json.dumps(draft["source_numbers"], ensure_ascii=False),
                target_numbers=json.dumps(draft["target_numbers"], ensure_ascii=False),
                error_reason=draft["reason"],
                ai_checked=False,
                ai_is_correct=True,
                ai_errors="[]",
                ai_source_issues="[]",
                replace_anchor="",
                suggested_value="",
                is_source_consistent=False,
                ai_error_status="",
                original_target_text="",
                applied=False,
                status=ITEM_STATUS_OPEN,
                block_index=int(segment.block_index or 0),
                row_index=segment.row_index,
                cell_index=segment.cell_index,
            )
        )

    db.commit()
    db.refresh(report)
    return report


def create_number_check_report(
    db: Session,
    *,
    project: Project | None,
    files: list[FileRecord],
    current_user: User | None,
    scope: str,
) -> NumberCheckReport:
    """执行程序检查并落库（不含 AI）。"""
    if not files:
        raise HTTPException(status_code=400, detail="请选择要检查的文件。")
    total_segments, drafts = _collect_program_issue_drafts(db, files)
    return _persist_number_check_report(
        db,
        project=project,
        files=files,
        current_user=current_user,
        scope=scope,
        total_segments=total_segments,
        drafts=drafts,
    )


async def run_ai_number_check_for_report(
    db: Session,
    report: NumberCheckReport,
    *,
    item_ids: list[UUID] | None = None,
    provider: str = "auto",
    model: str | None = None,
) -> NumberCheckReport:
    """对报告中的（全部或指定）程序疑似错误项跑 AI 复核。"""
    query = db.query(NumberCheckReportItem).filter(
        NumberCheckReportItem.report_id == report.id
    )
    if item_ids:
        query = query.filter(NumberCheckReportItem.id.in_(item_ids))
    items = query.order_by(
        NumberCheckReportItem.block_index.asc(),
        NumberCheckReportItem.sentence_id.asc(),
    ).all()

    if items:
        inputs = [
            (index, item.source_text or "", item.target_text or "")
            for index, item in enumerate(items)
        ]
        ai_results = await _run_ai_for_inputs(inputs, provider=provider, model=model)
        for index, item in enumerate(items):
            payload = ai_results.get(index)
            if payload is None:
                item.ai_checked = True
                item.ai_error_status = _STATUS_MISSING
                continue
            _apply_ai_result_to_item(item, payload)

    report.ai_checked = True
    _recompute_report_counts(db, report)
    db.commit()
    db.refresh(report)
    return report


async def run_ai_number_check_all_segments(
    db: Session,
    report: NumberCheckReport,
    files: list[FileRecord],
    *,
    provider: str = "auto",
    model: str | None = None,
) -> NumberCheckReport:
    """对所有有译文的句段执行 AI 复核（含程序未判错的句段）。

    程序已判错的句段更新其 AI 结果；程序未判错但 AI 认为有问题的句段会新建报告项。
    """
    seg_entries: list[tuple[FileRecord, Segment]] = []
    for file_record in files:
        for segment in list_segments_for_file_record(db, file_record.id):
            if normalize_text(segment.target_text or ""):
                seg_entries.append((file_record, segment))

    existing_items = {
        item.segment_id: item
        for item in db.query(NumberCheckReportItem)
        .filter(NumberCheckReportItem.report_id == report.id)
        .all()
        if item.segment_id is not None
    }

    inputs = [
        (index, segment.source_text or "", segment.target_text or "")
        for index, (_, segment) in enumerate(seg_entries)
    ]
    ai_results = await _run_ai_for_inputs(inputs, provider=provider, model=model)

    for index, (file_record, segment) in enumerate(seg_entries):
        payload = ai_results.get(index)
        if payload is None:
            continue
        is_missing = bool(payload.get("_missing"))
        has_error = bool(payload.get("errors")) if not is_missing else False
        has_source_issue = bool(payload.get("source_issues")) if not is_missing else False

        item = existing_items.get(segment.id)
        if item is None:
            # 程序未判错且 AI 也认为正常 → 不入库
            if not (has_error or has_source_issue):
                continue
            result = compare_numbers(segment.source_text or "", segment.target_text or "")
            item = NumberCheckReportItem(
                report_id=report.id,
                project_id=report.project_id,
                file_record_id=file_record.id,
                segment_id=segment.id,
                sentence_id=segment.sentence_id,
                file_name=file_record.filename,
                source_text=segment.source_text or "",
                target_text=segment.target_text or "",
                source_numbers=json.dumps(result.cn_numbers, ensure_ascii=False),
                target_numbers=json.dumps(result.en_numbers, ensure_ascii=False),
                error_reason=_format_program_reason(result) if not result.matched else "程序未发现，AI 检出",
                ai_checked=False,
                ai_is_correct=True,
                ai_errors="[]",
                ai_source_issues="[]",
                replace_anchor="",
                suggested_value="",
                is_source_consistent=False,
                ai_error_status="",
                original_target_text="",
                applied=False,
                status=ITEM_STATUS_OPEN,
                block_index=int(segment.block_index or 0),
                row_index=segment.row_index,
                cell_index=segment.cell_index,
            )
            db.add(item)
            db.flush()
            existing_items[segment.id] = item
        _apply_ai_result_to_item(item, payload)

    report.ai_checked = True
    _recompute_report_counts(db, report)
    db.commit()
    db.refresh(report)
    return report


def _upsert_ai_found_item(
    db: Session,
    report: NumberCheckReport,
    file_record: FileRecord,
    segment: Segment,
    existing_items: dict,
) -> NumberCheckReportItem:
    item = existing_items.get(segment.id)
    if item is not None:
        return item
    result = compare_numbers(segment.source_text or "", segment.target_text or "")
    item = NumberCheckReportItem(
        report_id=report.id,
        project_id=report.project_id,
        file_record_id=file_record.id,
        segment_id=segment.id,
        sentence_id=segment.sentence_id,
        file_name=file_record.filename,
        source_text=segment.source_text or "",
        target_text=segment.target_text or "",
        source_numbers=json.dumps(result.cn_numbers, ensure_ascii=False),
        target_numbers=json.dumps(result.en_numbers, ensure_ascii=False),
        error_reason=_format_program_reason(result) if not result.matched else "程序未发现，AI 检出",
        ai_checked=False,
        ai_is_correct=True,
        ai_errors="[]",
        ai_source_issues="[]",
        replace_anchor="",
        suggested_value="",
        is_source_consistent=False,
        ai_error_status="",
        original_target_text="",
        applied=False,
        status=ITEM_STATUS_OPEN,
        block_index=int(segment.block_index or 0),
        row_index=segment.row_index,
        cell_index=segment.cell_index,
    )
    db.add(item)
    db.flush()
    existing_items[segment.id] = item
    return item


async def _aiter_ai_recheck_progress(
    db: Session,
    report: NumberCheckReport,
    items: list[NumberCheckReportItem],
    *,
    provider: str,
    model: str | None,
):
    """对给定报告项分批跑 AI，每批后 yield (已处理, 总数)。"""
    total = len(items)
    processed = 0
    if total == 0:
        report.ai_checked = True
        _recompute_report_counts(db, report)
        db.commit()
        return
    for start in range(0, total, _AI_BLOCK_SIZE):
        batch = items[start:start + _AI_BLOCK_SIZE]
        inputs = [
            (index, item.source_text or "", item.target_text or "")
            for index, item in enumerate(batch)
        ]
        results = await _run_ai_for_inputs(inputs, provider=provider, model=model)
        for index, item in enumerate(batch):
            payload = results.get(index)
            if payload is None:
                item.ai_checked = True
                item.ai_error_status = _STATUS_MISSING
            else:
                _apply_ai_result_to_item(item, payload)
        processed += len(batch)
        db.commit()
        yield processed, total
    report.ai_checked = True
    _recompute_report_counts(db, report)
    db.commit()


async def _aiter_ai_check_all_progress(
    db: Session,
    report: NumberCheckReport,
    files: list[FileRecord],
    *,
    provider: str,
    model: str | None,
):
    """对所有有译文句段分批跑 AI（含程序未判错的），每批后 yield (已处理, 总数)。"""
    seg_entries: list[tuple[FileRecord, Segment]] = []
    for file_record in files:
        for segment in list_segments_for_file_record(db, file_record.id):
            if normalize_text(segment.target_text or ""):
                seg_entries.append((file_record, segment))

    existing_items = {
        item.segment_id: item
        for item in load_number_check_items(db, report.id)
        if item.segment_id is not None
    }

    total = len(seg_entries)
    processed = 0
    if total == 0:
        report.ai_checked = True
        _recompute_report_counts(db, report)
        db.commit()
        return
    for start in range(0, total, _AI_BLOCK_SIZE):
        batch = seg_entries[start:start + _AI_BLOCK_SIZE]
        inputs = [
            (index, segment.source_text or "", segment.target_text or "")
            for index, (_, segment) in enumerate(batch)
        ]
        results = await _run_ai_for_inputs(inputs, provider=provider, model=model)
        for index, (file_record, segment) in enumerate(batch):
            payload = results.get(index)
            if payload is None:
                continue
            is_missing = bool(payload.get("_missing"))
            has_error = bool(payload.get("errors")) if not is_missing else False
            has_source_issue = bool(payload.get("source_issues")) if not is_missing else False
            item = existing_items.get(segment.id)
            if item is None:
                if not (has_error or has_source_issue):
                    continue
                item = _upsert_ai_found_item(db, report, file_record, segment, existing_items)
            _apply_ai_result_to_item(item, payload)
        processed += len(batch)
        db.commit()
        yield processed, total
    report.ai_checked = True
    _recompute_report_counts(db, report)
    db.commit()


async def aiter_number_check_generation(
    db: Session,
    *,
    project: Project | None,
    files: list[FileRecord],
    current_user: User | None,
    scope: str,
    run_ai: bool,
    ai_scope: str,
    provider: str,
    model: str | None,
):
    """流式执行数字专检，逐步 yield 进度事件，最终 yield 报告 id。"""
    if not files:
        raise HTTPException(status_code=400, detail="请选择要检查的文件。")

    # ── 程序检查 ──
    seg_entries: list[tuple[FileRecord, Segment]] = []
    for file_record in files:
        for segment in list_segments_for_file_record(db, file_record.id):
            seg_entries.append((file_record, segment))

    total = len(seg_entries)
    yield {"stage": "program", "current": 0, "total": total}

    drafts: list[dict[str, Any]] = []
    for index, (file_record, segment) in enumerate(seg_entries, start=1):
        target_text = segment.target_text or ""
        if normalize_text(target_text):
            result = compare_numbers(segment.source_text or "", target_text)
            if not result.matched:
                drafts.append(
                    {
                        "file_record": file_record,
                        "segment": segment,
                        "source_numbers": result.cn_numbers,
                        "target_numbers": result.en_numbers,
                        "reason": _format_program_reason(result),
                    }
                )
        if index % 100 == 0 or index == total:
            yield {"stage": "program", "current": index, "total": total}
            await asyncio.sleep(0)

    report = _persist_number_check_report(
        db,
        project=project,
        files=files,
        current_user=current_user,
        scope=scope,
        total_segments=total,
        drafts=drafts,
    )
    yield {
        "stage": "program_done",
        "program_issue_count": report.program_issue_count,
        "total_segments": total,
    }

    # ── AI 复核 ──
    if run_ai:
        if ai_scope == "all":
            async for processed, ai_total in _aiter_ai_check_all_progress(
                db, report, files, provider=provider, model=model
            ):
                yield {"stage": "ai", "current": processed, "total": ai_total}
        else:
            items = load_number_check_items(db, report.id)
            async for processed, ai_total in _aiter_ai_recheck_progress(
                db, report, items, provider=provider, model=model
            ):
                yield {"stage": "ai", "current": processed, "total": ai_total}

    db.refresh(report)
    yield {"stage": "complete", "report_id": str(report.id)}


# ─────────────────────────────────────────
# 修改 / 恢复 / 忽略
# ─────────────────────────────────────────

def _get_item_segment(db: Session, item: NumberCheckReportItem) -> Segment:
    segment = (
        db.query(Segment)
        .filter(
            Segment.file_record_id == item.file_record_id,
            Segment.sentence_id == item.sentence_id,
        )
        .first()
    )
    if not segment:
        raise HTTPException(status_code=404, detail="对应句段不存在，无法操作。")
    return segment


def apply_number_check_item(
    db: Session,
    item: NumberCheckReportItem,
    current_user: User,
) -> NumberCheckReportItem:
    """按锚点替换将 AI 建议应用到译文。"""
    anchor = (item.replace_anchor or "").strip()
    suggested = item.suggested_value or ""
    if not anchor:
        raise HTTPException(status_code=400, detail="缺少替换锚点，无法自动替换，请手动修改。")

    segment = _get_item_segment(db, item)
    current_target = segment.target_text or ""
    if anchor not in current_target:
        raise HTTPException(
            status_code=400,
            detail="替换锚点不在当前译文中，译文可能已变更，请手动处理。",
        )

    new_target = current_target.replace(anchor, suggested, 1)
    if new_target == current_target:
        raise HTTPException(status_code=400, detail="按建议替换后译文没有变化。")

    if not item.applied:
        item.original_target_text = current_target
    item.target_text = new_target
    item.applied = True
    item.applied_at = datetime.utcnow()
    item.status = ITEM_STATUS_MODIFIED

    update_segment_by_sentence_id(
        db=db,
        file_record_id=item.file_record_id,
        sentence_id=item.sentence_id,
        target_text=new_target,
        source="manual",
        current_user=current_user,
    )
    db.refresh(item)
    return item


def restore_number_check_item(
    db: Session,
    item: NumberCheckReportItem,
    current_user: User,
) -> NumberCheckReportItem:
    """将译文恢复到修改前的快照。"""
    if not item.applied:
        raise HTTPException(status_code=400, detail="该项未被修改，无需恢复。")

    original = item.original_target_text or ""
    item.target_text = original
    item.applied = False
    item.applied_at = None
    item.status = ITEM_STATUS_OPEN

    update_segment_by_sentence_id(
        db=db,
        file_record_id=item.file_record_id,
        sentence_id=item.sentence_id,
        target_text=original,
        source="manual",
        current_user=current_user,
    )
    db.refresh(item)
    return item


def set_number_check_item_ignored(
    db: Session,
    item: NumberCheckReportItem,
    current_user: User,
    ignored: bool,
) -> NumberCheckReportItem:
    now = datetime.utcnow()
    if ignored:
        item.status = ITEM_STATUS_IGNORED
        item.ignored_by_id = getattr(current_user, "id", None)
        item.ignored_at = item.ignored_at or now
    else:
        item.status = ITEM_STATUS_MODIFIED if item.applied else ITEM_STATUS_OPEN
        item.ignored_by_id = None
        item.ignored_at = None
    db.commit()
    db.refresh(item)
    return item


def _apply_single_number_check_item(
    db: Session,
    item: NumberCheckReportItem,
    current_user: User,
) -> bool:
    """尝试按锚点替换应用单个项，失败返回 False（不抛异常，供批量使用）。"""
    anchor = (item.replace_anchor or "").strip()
    suggested = item.suggested_value or ""
    if not anchor or item.applied or item.status == ITEM_STATUS_IGNORED:
        return False
    segment = (
        db.query(Segment)
        .filter(
            Segment.file_record_id == item.file_record_id,
            Segment.sentence_id == item.sentence_id,
        )
        .first()
    )
    if not segment:
        return False
    current_target = segment.target_text or ""
    if anchor not in current_target:
        return False
    new_target = current_target.replace(anchor, suggested, 1)
    if new_target == current_target:
        return False

    item.original_target_text = current_target
    item.target_text = new_target
    item.applied = True
    item.applied_at = datetime.utcnow()
    item.status = ITEM_STATUS_MODIFIED
    update_segment_by_sentence_id(
        db=db,
        file_record_id=item.file_record_id,
        sentence_id=item.sentence_id,
        target_text=new_target,
        source="manual",
        current_user=current_user,
    )
    return True


def apply_number_check_items_bulk(
    db: Session,
    report: NumberCheckReport,
    current_user: User,
    *,
    item_ids: list[UUID] | None = None,
) -> int:
    """一键修改：对所有（或指定）可应用项按锚点替换应用 AI 建议。"""
    query = db.query(NumberCheckReportItem).filter(
        NumberCheckReportItem.report_id == report.id,
        NumberCheckReportItem.applied.is_(False),
        NumberCheckReportItem.status != ITEM_STATUS_IGNORED,
    )
    if item_ids:
        query = query.filter(NumberCheckReportItem.id.in_(item_ids))
    items = query.order_by(
        NumberCheckReportItem.block_index.asc(),
        NumberCheckReportItem.sentence_id.asc(),
    ).all()

    applied_count = 0
    for item in items:
        if _apply_single_number_check_item(db, item, current_user):
            applied_count += 1
    db.commit()
    return applied_count


def ignore_number_check_items_bulk(
    db: Session,
    report: NumberCheckReport,
    current_user: User,
    *,
    item_ids: list[UUID] | None = None,
    ignored: bool = True,
) -> int:
    """一键忽略 / 取消忽略：对所有（或指定）项批量更新忽略状态。"""
    query = db.query(NumberCheckReportItem).filter(
        NumberCheckReportItem.report_id == report.id
    )
    if ignored:
        query = query.filter(NumberCheckReportItem.status != ITEM_STATUS_IGNORED)
    else:
        query = query.filter(NumberCheckReportItem.status == ITEM_STATUS_IGNORED)
    if item_ids:
        query = query.filter(NumberCheckReportItem.id.in_(item_ids))
    items = query.all()

    now = datetime.utcnow()
    count = 0
    for item in items:
        if ignored:
            item.status = ITEM_STATUS_IGNORED
            item.ignored_by_id = getattr(current_user, "id", None)
            item.ignored_at = item.ignored_at or now
        else:
            item.status = ITEM_STATUS_MODIFIED if item.applied else ITEM_STATUS_OPEN
            item.ignored_by_id = None
            item.ignored_at = None
        count += 1
    db.commit()
    return count


# ─────────────────────────────────────────
# 序列化
# ─────────────────────────────────────────

def _load_json_list(raw_value: str | None) -> list[Any]:
    try:
        value = json.loads(raw_value or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def serialize_number_check_item(item: NumberCheckReportItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "report_id": str(item.report_id),
        "project_id": str(item.project_id) if item.project_id else None,
        "file_record_id": str(item.file_record_id),
        "segment_id": str(item.segment_id) if item.segment_id else None,
        "sentence_id": item.sentence_id,
        "file_name": item.file_name,
        "source_text": item.source_text,
        "target_text": item.target_text,
        "source_numbers": _load_json_list(item.source_numbers),
        "target_numbers": _load_json_list(item.target_numbers),
        "error_reason": item.error_reason,
        "program_flagged": not (item.error_reason or "").startswith("程序未发现"),
        "ai_checked": bool(item.ai_checked),
        "ai_is_correct": bool(item.ai_is_correct),
        "ai_errors": _load_json_list(item.ai_errors),
        "ai_source_issues": _load_json_list(item.ai_source_issues),
        "replace_anchor": item.replace_anchor,
        "suggested_value": item.suggested_value,
        "is_source_consistent": bool(item.is_source_consistent),
        "ai_error_status": item.ai_error_status,
        "original_target_text": item.original_target_text,
        "applied": bool(item.applied),
        "applied_at": item.applied_at.isoformat() if item.applied_at else None,
        "status": item.status,
        "ignored": item.status == ITEM_STATUS_IGNORED,
        "can_apply": bool(item.replace_anchor) and not item.applied,
        "block_index": item.block_index,
        "row_index": item.row_index,
        "cell_index": item.cell_index,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _sort_items(items: list[NumberCheckReportItem]) -> list[NumberCheckReportItem]:
    return sorted(
        items,
        key=lambda item: (
            item.file_name or "",
            int(item.block_index or 0),
            item.row_index if item.row_index is not None else -1,
            item.cell_index if item.cell_index is not None else -1,
            item.sentence_id or "",
        ),
    )


def serialize_number_check_report(
    report: NumberCheckReport,
    items: list[NumberCheckReportItem] | None = None,
) -> dict[str, Any]:
    report_items = _sort_items(list(items if items is not None else report.items))
    ignored_count = sum(1 for item in report_items if item.status == ITEM_STATUS_IGNORED)
    return {
        "id": str(report.id),
        "project_id": str(report.project_id) if report.project_id else None,
        "file_record_id": str(report.file_record_id) if report.file_record_id else None,
        "scope": report.scope,
        "file_ids": [str(value) for value in _load_json_list(report.file_ids)],
        "total_files": report.total_files,
        "total_segments": report.total_segments,
        "checked_segments": report.checked_segments,
        "program_issue_count": report.program_issue_count,
        "ai_issue_count": report.ai_issue_count,
        "source_issue_count": report.source_issue_count,
        "ai_checked": bool(report.ai_checked),
        "active_issue_count": len(report_items) - ignored_count,
        "ignored_count": ignored_count,
        "status": report.status,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "items": [serialize_number_check_item(item) for item in report_items],
    }


def load_number_check_items(db: Session, report_id: UUID) -> list[NumberCheckReportItem]:
    return (
        db.query(NumberCheckReportItem)
        .filter(NumberCheckReportItem.report_id == report_id)
        .all()
    )
