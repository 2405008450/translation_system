"""
样式标记专检服务
================
对多样式句段（源文有 run 级格式差异、译文缺失或失效标注）执行 AI 自动标签标注，
结果落库为 StyleTagCheckReport / StyleTagCheckReportItem，支持审校面板查看/应用/
拒绝/重跑；应用只写 target_layout_text（不改 target_text，不产生 revision）。

设计要点（与数字专检一致的结构，规则由本功能自身的强约束决定）：
  - 候选筛选：只挑“多样式”句段——source_format_map 含标签 id（不止 base）、
    source_layout_text 带 ⟦n⟧、target_text 非空、且当前 target_layout_text
    缺失或已失效（strip 后与 target_text 不一致）。统一样式句段直接跳过：
    导出走保留原 run 的 rPr 兜底，不需要标签。
  - AI 只允许"在译文原文字上插入标签"，不允许改写译文一个字：
    1) strip_format_tags(输出) == target_text 逐字相同；
    2) 标签结构合法（复用 pptx_inline_tags.validate_tagged_text_structure，
       扁平成对、每个 id 最多一次、id 必须来自该句段的 source_format_map）。
    任一不满足，该条判为失败（failed），不写回，导出走兜底，不影响译文本身。
  - 应用（apply）只调 set_segment_target_layout_text，完全不碰 target_text，
    不产生 revision、不改 version——和译文编辑彻底解耦。
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
    Project,
    Segment,
    StyleTagCheckReport,
    StyleTagCheckReportItem,
    User,
)
from app.services.adapters.pptx_inline_tags import (
    is_target_layout_valid,
    sanitize_tagged_text,
    strip_format_tags,
    validate_tagged_text_structure,
)
from app.services.file_record_service import (
    list_segments_for_file_record,
    set_segment_target_layout_text,
)
from app.services.llm_service import (
    LLMConfigurationError,
    LLMRequestError,
    LLMResponseValidationError,
    request_chat_completion,
)

logger = logging.getLogger(__name__)

ITEM_STATUS_PENDING = "pending"  # 待 AI 标注
ITEM_STATUS_OPEN = "open"  # AI 已给出建议，待审校
ITEM_STATUS_APPLIED = "applied"
ITEM_STATUS_REJECTED = "rejected"
ITEM_STATUS_FAILED = "failed"

_AI_BLOCK_SIZE = 10
_AI_MAX_RETRY = 1

_ERROR_MISSING = "missing"
_ERROR_API = "api_error"
_ERROR_PARSE = "parse_failed"
_ERROR_TEXT_MISMATCH = "text_mismatch"
_ERROR_STRUCTURE = "invalid_structure"

_MARKER_RE = re.compile(r"⟦\s*/?\s*\d+\s*⟧")


# ─────────────────────────────────────────
# 候选筛选
# ─────────────────────────────────────────

def _segment_metadata(segment: Segment) -> dict[str, Any]:
    raw = getattr(segment, "segment_metadata", None)
    if not raw:
        return {}
    try:
        metadata = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _is_multi_style_candidate(segment: Segment) -> tuple[bool, dict[str, Any], str]:
    """判定句段是否是需要 AI 标注的多样式候选，返回 (是否候选, format_map, source_layout_text)。"""
    metadata = _segment_metadata(segment)
    format_map = metadata.get("source_layout_formats")
    if not isinstance(format_map, dict):
        return False, {}, ""
    # 只有 base（统一样式）不需要标签
    has_tag_ids = any(key != "base" for key in format_map.keys())
    if not has_tag_ids:
        return False, {}, ""

    source_layout_text = str(metadata.get("source_layout_text") or "")
    if not _MARKER_RE.search(source_layout_text):
        return False, {}, ""

    target_text = segment.target_text or ""
    if not target_text.strip():
        return False, {}, ""

    existing_layout = str(metadata.get("target_layout_text") or "")
    if existing_layout and is_target_layout_valid(target_text, existing_layout):
        return False, {}, ""  # 已有有效标注，不需要重新标注

    return True, format_map, source_layout_text


def _collect_style_tag_candidates(
    db: Session,
    files: list[FileRecord],
) -> tuple[int, list[dict[str, Any]]]:
    """扫描所有句段，返回 (总句段数, 候选草稿列表)。"""
    total_segments = 0
    drafts: list[dict[str, Any]] = []
    for file_record in files:
        segments = list_segments_for_file_record(db, file_record.id)
        total_segments += len(segments)
        for segment in segments:
            is_candidate, format_map, source_layout_text = _is_multi_style_candidate(segment)
            if not is_candidate:
                continue
            drafts.append(
                {
                    "file_record": file_record,
                    "segment": segment,
                    "format_map": format_map,
                    "source_layout_text": source_layout_text,
                }
            )
    return total_segments, drafts


# ─────────────────────────────────────────
# 标签语义说明（给 AI 的提示）
# ─────────────────────────────────────────

def _describe_format_map(format_map: dict[str, Any]) -> str:
    """把 format_map 的 CSS token 转成人类可读的样式描述，供 AI 理解每个标签代表什么样式。"""
    descriptions: list[str] = []
    for tag_id, tokens in format_map.items():
        if tag_id == "base" or not isinstance(tokens, (list, tuple)) or not tokens:
            continue
        open_tag = str(tokens[0] or "")
        style_match = re.search(r'style="([^"]*)"', open_tag)
        css = style_match.group(1) if style_match else ""
        labels: list[str] = []
        if "font-weight:bold" in css:
            labels.append("加粗")
        if "font-style:italic" in css:
            labels.append("斜体")
        if "underline" in css:
            labels.append("下划线")
        if "line-through" in css:
            labels.append("删除线")
        color_match = re.search(r"color:(#[0-9a-fA-F]{3,6})", css)
        if color_match:
            labels.append(f"颜色{color_match.group(1)}")
        size_match = re.search(r"font-size:([0-9.]+pt)", css)
        if size_match:
            labels.append(f"字号{size_match.group(1)}")
        family_match = re.search(r"font-family:'([^']*)'", css)
        if family_match:
            labels.append(f"字体{family_match.group(1)}")
        descriptions.append(f"⟦{tag_id}⟧={('+'.join(labels) or '特殊样式')}")
    return "；".join(descriptions)


# ─────────────────────────────────────────
# AI 标注
# ─────────────────────────────────────────

def _build_ai_prompt(seq_items: list[tuple[int, str, str, str]]) -> str:
    lines = []
    for seq, source_layout_text, target_text, style_desc in seq_items:
        lines.append(
            f"[{seq}] 带标签原文: {source_layout_text}\n"
            f"[{seq}] 标签样式说明: {style_desc}\n"
            f"[{seq}] 纯译文: {target_text}"
        )
    combined = "\n\n".join(lines)
    return f"""你是双语排版标注专家。以下每条包含：带 ⟦n⟧…⟦/n⟧ 行内格式标签的原文、标签对应的样式说明、以及对应的纯译文（共 {len(seq_items)} 条，用[序号]标记）。

任务：判断原文中每个 ⟦n⟧ 标签包裹的内容翻译成了译文中的哪些词语，在**纯译文文本上原样插入对应的 ⟦n⟧…⟦/n⟧ 标签**，标出这些词语。

硬性规则（任何一条违反都会导致该条被丢弃）：
1. 绝对不能修改、增删纯译文的任何一个字符，只能在其中插入 ⟦n⟧ 和 ⟦/n⟧ 标记本身。
2. 每个标签 id 最多使用一次，必须成对出现（⟦n⟧ 在前，⟦/n⟧ 在后），不能嵌套或交叉。
3. 如果某个标签对应的原文片段在译文中找不到明确对应词语（如无法翻译的符号、编号），可以不给该 id 打标签——不要求覆盖所有 id。
4. 语序可以和原文不同（译文的词序通常与原文不同，标签跟着对应的译文词语走）。

输出 JSON 数组，长度必须与输入条数（{len(seq_items)}）相同：
[
  {{"seq": 0, "tagged_text": "带 ⟦n⟧ 标签的译文，纯文本剥标签后必须与输入的纯译文逐字相同"}},
  {{"seq": 1, "tagged_text": "..."}}
]
只输出 JSON 数组，不要解释、不要 Markdown 代码块。

待标注内容：
{combined}
"""


def _safe_parse_json_array(content: str) -> list[dict[str, Any]]:
    if not content or not content.strip():
        return []
    try:
        cleaned = re.sub(r"```json|```", "", content).strip()
        match = re.search(r"\[.*\]", cleaned, re.S)
        candidate = match.group() if match else cleaned
        parsed = json.loads(candidate)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
        return []
    except Exception:
        return []


async def _call_ai(
    seq_items: list[tuple[int, str, str, str]],
    *,
    provider: str,
    model: str | None,
) -> tuple[dict[int, str], dict[int, str]]:
    """发一批候选给模型标注，返回 ({seq: tagged_text}, {seq: 错误状态})（成功的不出现在错误字典里）。"""
    prompt = _build_ai_prompt(seq_items)
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
        logger.warning("style-tag-check AI call failed: %s", exc)
        return {}, {seq: _ERROR_API for seq, *_ in seq_items}
    except Exception as exc:  # noqa: BLE001
        logger.exception("style-tag-check AI call unexpected error: %s", exc)
        return {}, {seq: _ERROR_API for seq, *_ in seq_items}

    logger.info(
        "style-tag-check AI raw response (batch_size=%d): %s",
        len(seq_items),
        result.content,
    )

    parsed = _safe_parse_json_array(result.content)
    if not parsed:
        return {}, {seq: _ERROR_PARSE for seq, *_ in seq_items}

    by_seq: dict[int, str] = {}
    for index, payload in enumerate(parsed):
        if not isinstance(payload, dict):
            continue
        seq = payload.get("seq", seq_items[index][0] if index < len(seq_items) else None)
        if seq is None:
            continue
        try:
            by_seq[int(seq)] = str(payload.get("tagged_text") or "")
        except (TypeError, ValueError):
            continue

    errors: dict[int, str] = {}
    for seq, _source, _target, _style in seq_items:
        if seq not in by_seq:
            errors[seq] = _ERROR_PARSE
    return by_seq, errors


def _validate_and_finalize_tagged_text(
    tagged_text: str,
    target_text: str,
    format_map: dict[str, Any],
) -> tuple[str, str]:
    """双重强校验：剥标签后必须逐字等于 target_text，且标签结构合法。

    返回 (最终带标签文本或空串, 错误状态或空串)。
    """
    sanitized = sanitize_tagged_text(tagged_text or "")
    if not sanitized:
        return "", _ERROR_PARSE
    stripped = strip_format_tags(sanitized)
    if stripped != target_text:
        logger.info(
            "style-tag-check text_mismatch detail:\n  ai_tagged=%r\n  ai_stripped=%r\n  target_text=%r",
            sanitized,
            stripped,
            target_text,
        )
        return "", _ERROR_TEXT_MISMATCH
    valid_ids = {int(key) for key in format_map.keys() if key != "base" and str(key).isdigit()}
    if not validate_tagged_text_structure(sanitized, valid_ids):
        logger.info(
            "style-tag-check invalid_structure detail: ai_tagged=%r valid_ids=%r",
            sanitized,
            valid_ids,
        )
        return "", _ERROR_STRUCTURE
    return sanitized, ""


async def _run_ai_for_drafts(
    drafts: list[dict[str, Any]],
    *,
    provider: str,
    model: str | None,
) -> dict[int, tuple[str, str]]:
    """对全部候选分块跑 AI + 校验，返回 {index: (带标签译文或空串, 错误状态或空串)}。"""
    seq_items: list[tuple[int, str, str, str]] = [
        (
            index,
            draft["source_layout_text"],
            draft["segment"].target_text or "",
            _describe_format_map(draft["format_map"]),
        )
        for index, draft in enumerate(drafts)
    ]

    results: dict[int, tuple[str, str]] = {}
    blocks = [seq_items[i:i + _AI_BLOCK_SIZE] for i in range(0, len(seq_items), _AI_BLOCK_SIZE)]
    for block in blocks:
        by_seq, errors = await _call_ai(block, provider=provider, model=model)
        for seq, tagged_text in by_seq.items():
            if seq >= len(drafts):
                continue
            final_text, error_status = _validate_and_finalize_tagged_text(
                tagged_text, drafts[seq]["segment"].target_text or "", drafts[seq]["format_map"]
            )
            results[seq] = (final_text, error_status)
        for seq in errors:
            if seq not in results:
                results[seq] = ("", errors[seq])

        for _ in range(_AI_MAX_RETRY):
            missing = [item for item in block if item[0] not in results or results[item[0]][1]]
            missing = [item for item in missing if not results.get(item[0], ("", ""))[0]]
            if not missing:
                break
            by_seq, errors = await _call_ai(missing, provider=provider, model=model)
            for seq, tagged_text in by_seq.items():
                if seq >= len(drafts):
                    continue
                final_text, error_status = _validate_and_finalize_tagged_text(
                    tagged_text, drafts[seq]["segment"].target_text or "", drafts[seq]["format_map"]
                )
                if final_text:
                    results[seq] = (final_text, error_status)
            for seq in errors:
                if not results.get(seq, ("", ""))[0]:
                    results[seq] = ("", errors[seq])

    for seq, _draft in enumerate(drafts):
        if seq not in results:
            results[seq] = ("", _ERROR_MISSING)
    return results


# ─────────────────────────────────────────
# 报告落库
# ─────────────────────────────────────────

def _persist_style_tag_check_report(
    db: Session,
    *,
    project: Project | None,
    files: list[FileRecord],
    current_user: User | None,
    scope: str,
    total_segments: int,
    drafts: list[dict[str, Any]],
) -> StyleTagCheckReport:
    file_ids = [file_record.id for file_record in files]
    report = StyleTagCheckReport(
        project_id=project.id if project else None,
        file_record_id=files[0].id if scope == "file" and len(files) == 1 else None,
        created_by_id=getattr(current_user, "id", None),
        scope=scope,
        file_ids=json.dumps([str(file_id) for file_id in file_ids]),
        total_files=len(files),
        total_segments=total_segments,
        candidate_count=len(drafts),
        applied_count=0,
        failed_count=0,
        ai_checked=False,
        status="completed",
    )
    db.add(report)
    db.flush()

    for draft in drafts:
        segment: Segment = draft["segment"]
        file_record: FileRecord = draft["file_record"]
        db.add(
            StyleTagCheckReportItem(
                report_id=report.id,
                project_id=project.id if project else None,
                file_record_id=file_record.id,
                segment_id=segment.id,
                sentence_id=segment.sentence_id,
                file_name=file_record.filename,
                source_text=segment.source_text or "",
                source_layout_text=draft["source_layout_text"],
                target_text=segment.target_text or "",
                format_map=json.dumps(draft["format_map"], ensure_ascii=False),
                suggested_target_layout_text="",
                original_target_layout_text="",
                ai_error_status="",
                ai_checked=False,
                applied=False,
                status=ITEM_STATUS_PENDING,
                block_index=int(segment.block_index or 0),
                row_index=segment.row_index,
                cell_index=segment.cell_index,
            )
        )

    db.commit()
    db.refresh(report)
    return report


def _recompute_report_counts(db: Session, report: StyleTagCheckReport) -> None:
    items = (
        db.query(StyleTagCheckReportItem)
        .filter(StyleTagCheckReportItem.report_id == report.id)
        .all()
    )
    report.candidate_count = len(items)
    report.applied_count = sum(1 for item in items if item.status == ITEM_STATUS_APPLIED)
    report.failed_count = sum(1 for item in items if item.status == ITEM_STATUS_FAILED)


def create_style_tag_check_report(
    db: Session,
    *,
    project: Project | None,
    files: list[FileRecord],
    current_user: User | None,
    scope: str,
) -> StyleTagCheckReport:
    """扫描候选并落库（不含 AI 标注）。"""
    if not files:
        raise HTTPException(status_code=400, detail="请选择要检查的文件。")
    total_segments, drafts = _collect_style_tag_candidates(db, files)
    return _persist_style_tag_check_report(
        db,
        project=project,
        files=files,
        current_user=current_user,
        scope=scope,
        total_segments=total_segments,
        drafts=drafts,
    )


async def run_ai_style_tag_check_for_report(
    db: Session,
    report: StyleTagCheckReport,
    *,
    item_ids: list[UUID] | None = None,
    provider: str = "auto",
    model: str | None = None,
) -> StyleTagCheckReport:
    """对报告中的（全部或指定）候选项跑 AI 标注。"""
    query = db.query(StyleTagCheckReportItem).filter(
        StyleTagCheckReportItem.report_id == report.id
    )
    if item_ids:
        query = query.filter(StyleTagCheckReportItem.id.in_(item_ids))
    items = query.order_by(
        StyleTagCheckReportItem.block_index.asc(),
        StyleTagCheckReportItem.sentence_id.asc(),
    ).all()

    if items:
        drafts = [
            {
                "segment": type("S", (), {"target_text": item.target_text})(),
                "source_layout_text": item.source_layout_text,
                "format_map": json.loads(item.format_map or "{}"),
            }
            for item in items
        ]
        ai_results = await _run_ai_for_drafts(drafts, provider=provider, model=model)
        for index, item in enumerate(items):
            tagged_text, error_status = ai_results.get(index, ("", _ERROR_MISSING))
            item.ai_checked = True
            if tagged_text:
                item.suggested_target_layout_text = tagged_text
                item.ai_error_status = ""
                item.status = ITEM_STATUS_OPEN
            else:
                item.suggested_target_layout_text = ""
                item.ai_error_status = error_status
                item.status = ITEM_STATUS_FAILED

    report.ai_checked = True
    _recompute_report_counts(db, report)
    db.commit()
    db.refresh(report)
    return report


async def aiter_style_tag_check_generation(
    db: Session,
    *,
    project: Project | None,
    files: list[FileRecord],
    current_user: User | None,
    scope: str,
    provider: str,
    model: str | None,
):
    """流式执行样式标记专检（扫描候选 + AI 标注），逐步 yield 进度事件。"""
    if not files:
        raise HTTPException(status_code=400, detail="请选择要检查的文件。")

    yield {"stage": "scan", "current": 0, "total": 0}
    total_segments, drafts = _collect_style_tag_candidates(db, files)
    report = _persist_style_tag_check_report(
        db,
        project=project,
        files=files,
        current_user=current_user,
        scope=scope,
        total_segments=total_segments,
        drafts=drafts,
    )
    yield {
        "stage": "scan_done",
        "candidate_count": report.candidate_count,
        "total_segments": total_segments,
    }

    items = load_style_tag_check_items(db, report.id)
    total = len(items)
    processed = 0
    if total == 0:
        report.ai_checked = True
        _recompute_report_counts(db, report)
        db.commit()
        yield {"stage": "complete", "report_id": str(report.id)}
        return

    for start in range(0, total, _AI_BLOCK_SIZE):
        batch = items[start:start + _AI_BLOCK_SIZE]
        batch_drafts = [
            {
                "source_layout_text": item.source_layout_text,
                "format_map": json.loads(item.format_map or "{}"),
                "segment": type("S", (), {"target_text": item.target_text})(),
            }
            for item in batch
        ]
        ai_results = await _run_ai_for_drafts(batch_drafts, provider=provider, model=model)
        for index, item in enumerate(batch):
            tagged_text, error_status = ai_results.get(index, ("", _ERROR_MISSING))
            item.ai_checked = True
            if tagged_text:
                item.suggested_target_layout_text = tagged_text
                item.ai_error_status = ""
                item.status = ITEM_STATUS_OPEN
            else:
                item.suggested_target_layout_text = ""
                item.ai_error_status = error_status
                item.status = ITEM_STATUS_FAILED
        processed += len(batch)
        db.commit()
        yield {"stage": "ai", "current": processed, "total": total}
        await asyncio.sleep(0)

    report.ai_checked = True
    _recompute_report_counts(db, report)
    db.commit()
    db.refresh(report)
    yield {"stage": "complete", "report_id": str(report.id)}


# ─────────────────────────────────────────
# 应用 / 拒绝 / 恢复
# ─────────────────────────────────────────

def _get_item_segment(db: Session, item: StyleTagCheckReportItem) -> Segment:
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


def apply_style_tag_check_item(db: Session, item: StyleTagCheckReportItem) -> StyleTagCheckReportItem:
    """把 AI 建议写入句段的 target_layout_text（只写标签，不改 target_text）。"""
    suggested = (item.suggested_target_layout_text or "").strip()
    if not suggested:
        raise HTTPException(status_code=400, detail="没有可应用的标注建议。")

    segment = _get_item_segment(db, item)
    current_target = segment.target_text or ""
    # 二次校验：应用时译文可能已被改动，必须重新确认一致性，避免标签错位
    if strip_format_tags(suggested) != current_target:
        raise HTTPException(
            status_code=400,
            detail="译文已发生变化，标注建议已过期，请重新标注。",
        )

    existing_metadata = _segment_metadata(segment)
    item.original_target_layout_text = str(existing_metadata.get("target_layout_text") or "")
    set_segment_target_layout_text(segment, suggested)
    item.applied = True
    item.applied_at = datetime.utcnow()
    item.status = ITEM_STATUS_APPLIED
    db.commit()
    db.refresh(item)
    return item


def reject_style_tag_check_item(db: Session, item: StyleTagCheckReportItem) -> StyleTagCheckReportItem:
    item.status = ITEM_STATUS_REJECTED
    db.commit()
    db.refresh(item)
    return item


def restore_style_tag_check_item(db: Session, item: StyleTagCheckReportItem) -> StyleTagCheckReportItem:
    """撤销应用：把句段的 target_layout_text 还原为应用前的值。"""
    if not item.applied:
        raise HTTPException(status_code=400, detail="该项未被应用，无需恢复。")

    segment = _get_item_segment(db, item)
    set_segment_target_layout_text(segment, item.original_target_layout_text or "")
    item.applied = False
    item.applied_at = None
    item.status = ITEM_STATUS_OPEN
    db.commit()
    db.refresh(item)
    return item


async def rerun_style_tag_check_item(
    db: Session,
    item: StyleTagCheckReportItem,
    *,
    provider: str = "auto",
    model: str | None = None,
) -> StyleTagCheckReportItem:
    """对单条重新跑 AI 标注（例如译文改动后原建议已失效）。"""
    segment = _get_item_segment(db, item)
    item.target_text = segment.target_text or ""
    format_map = json.loads(item.format_map or "{}")
    drafts = [
        {
            "source_layout_text": item.source_layout_text,
            "format_map": format_map,
            "segment": type("S", (), {"target_text": item.target_text})(),
        }
    ]
    ai_results = await _run_ai_for_drafts(drafts, provider=provider, model=model)
    tagged_text, error_status = ai_results.get(0, ("", _ERROR_MISSING))
    item.ai_checked = True
    if tagged_text:
        item.suggested_target_layout_text = tagged_text
        item.ai_error_status = ""
        item.status = ITEM_STATUS_OPEN
    else:
        item.suggested_target_layout_text = ""
        item.ai_error_status = error_status
        item.status = ITEM_STATUS_FAILED
    db.commit()
    db.refresh(item)
    return item


def apply_all_style_tag_check_items(
    db: Session,
    report: StyleTagCheckReport,
    *,
    item_ids: list[UUID] | None = None,
) -> int:
    """一键应用：对所有（或指定）有有效建议且未应用的项批量写入 target_layout_text。"""
    query = db.query(StyleTagCheckReportItem).filter(
        StyleTagCheckReportItem.report_id == report.id,
        StyleTagCheckReportItem.applied.is_(False),
        StyleTagCheckReportItem.status == ITEM_STATUS_OPEN,
    )
    if item_ids:
        query = query.filter(StyleTagCheckReportItem.id.in_(item_ids))
    items = query.all()

    applied_count = 0
    for item in items:
        try:
            apply_style_tag_check_item(db, item)
            applied_count += 1
        except HTTPException:
            continue
    _recompute_report_counts(db, report)
    db.commit()
    return applied_count


# ─────────────────────────────────────────
# 序列化
# ─────────────────────────────────────────

def serialize_style_tag_check_item(item: StyleTagCheckReportItem) -> dict[str, Any]:
    try:
        format_map = json.loads(item.format_map or "{}")
    except (TypeError, ValueError):
        format_map = {}
    return {
        "id": str(item.id),
        "report_id": str(item.report_id),
        "project_id": str(item.project_id) if item.project_id else None,
        "file_record_id": str(item.file_record_id),
        "segment_id": str(item.segment_id) if item.segment_id else None,
        "sentence_id": item.sentence_id,
        "file_name": item.file_name,
        "source_text": item.source_text,
        "source_layout_text": item.source_layout_text,
        "target_text": item.target_text,
        "format_map": format_map,
        "suggested_target_layout_text": item.suggested_target_layout_text or None,
        "original_target_layout_text": item.original_target_layout_text or None,
        "ai_error_status": item.ai_error_status,
        "ai_checked": bool(item.ai_checked),
        "applied": bool(item.applied),
        "applied_at": item.applied_at.isoformat() if item.applied_at else None,
        "status": item.status,
        "can_apply": bool(item.suggested_target_layout_text) and not item.applied,
        "block_index": item.block_index,
        "row_index": item.row_index,
        "cell_index": item.cell_index,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _sort_items(items: list[StyleTagCheckReportItem]) -> list[StyleTagCheckReportItem]:
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


def serialize_style_tag_check_report(
    report: StyleTagCheckReport,
    items: list[StyleTagCheckReportItem] | None = None,
) -> dict[str, Any]:
    report_items = _sort_items(list(items if items is not None else report.items))

    def _load_ids(raw: str | None) -> list[str]:
        try:
            value = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return []
        return [str(v) for v in value] if isinstance(value, list) else []

    return {
        "id": str(report.id),
        "project_id": str(report.project_id) if report.project_id else None,
        "file_record_id": str(report.file_record_id) if report.file_record_id else None,
        "scope": report.scope,
        "file_ids": _load_ids(report.file_ids),
        "total_files": report.total_files,
        "total_segments": report.total_segments,
        "candidate_count": report.candidate_count,
        "applied_count": report.applied_count,
        "failed_count": report.failed_count,
        "ai_checked": bool(report.ai_checked),
        "status": report.status,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "items": [serialize_style_tag_check_item(item) for item in report_items],
    }


def load_style_tag_check_items(db: Session, report_id: UUID) -> list[StyleTagCheckReportItem]:
    return (
        db.query(StyleTagCheckReportItem)
        .filter(StyleTagCheckReportItem.report_id == report_id)
        .all()
    )
