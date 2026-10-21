from __future__ import annotations

import csv
from copy import copy
from dataclasses import dataclass
from difflib import SequenceMatcher
from io import BytesIO, StringIO
import json
import logging
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy.orm import Session

from app.models import (
    DocumentAlignmentPair, FileRecord, ProofreadingBatch, ProofreadingColumnBinding,
    ProofreadingSegmentBaseline, Segment,
    TranslationReviewReportItem,
)
from app.services.normalizer import compact_match_core
from app.services.proofreading_export_comments import collect_proofreading_comments

from .parser import AlignUnit, parse_side
from .segments import TRANSLATION_ONLY_SOURCE_LABEL, ensure_document_pair_segments_complete
from .service import target_cache_path

logger = logging.getLogger(__name__)

MISSING_TRANSLATION_LABEL = "【译文缺失】"
TRANSLATION_ONLY_EXPORT_LABEL = "【增译】"


@dataclass(frozen=True)
class ProofreadingExportRow:
    order: int
    kind: str
    source_text: str
    original_target_text: str
    reviewed_target_text: str
    changed: bool
    confirmation_status: str
    llm_status: str
    confidence: float | None
    method: str
    pair_id: str = ""
    block_type: str = "paragraph"
    block_index: int = 0
    row_index: int | None = None
    cell_index: int | None = None
    segment_id: str = ""
    comment_text: str = ""


def _segment_metadata(segment: Segment) -> dict[str, Any]:
    try:
        value = json.loads(segment.segment_metadata or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_proofreading_export_rows(
    db: Session, batch: ProofreadingBatch, *, include_comments: bool = False,
) -> tuple[list[ProofreadingExportRow], FileRecord | None]:
    """建立导出的唯一顺序清单，不依赖 sentence_id 或数据库自然顺序。"""
    if batch.batch_kind == "document_pair":
        ensure_document_pair_segments_complete(db, batch)
        binding = db.query(ProofreadingColumnBinding).filter_by(batch_id=batch.id).first()
        file_record = db.get(FileRecord, binding.file_record_id) if binding else None
        baselines = db.query(ProofreadingSegmentBaseline).filter_by(batch_id=batch.id).all()
        baseline_by_segment = {item.segment_id: item for item in baselines}
        segment_by_pair: dict[str, Segment] = {}
        if file_record is not None:
            for segment in db.query(Segment).filter_by(file_record_id=file_record.id).all():
                pair_id = str(_segment_metadata(segment).get("alignment_pair_id") or "")
                if pair_id:
                    segment_by_pair[pair_id] = segment
        rows: list[ProofreadingExportRow] = []
        comments = collect_proofreading_comments(db, batch, [
            (segment, baseline_by_segment.get(segment.id)) for segment in segment_by_pair.values()
        ]) if include_comments else {}
        pairs = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(
            DocumentAlignmentPair.pair_order,
        ).all()
        for pair in pairs:
            segment = segment_by_pair.get(str(pair.id))
            baseline = baseline_by_segment.get(segment.id) if segment is not None else None
            source_text = segment.source_text if segment is not None else pair.source_text
            reviewed_target = segment.target_text if segment is not None else pair.target_text
            original_target = baseline.original_target_text if baseline is not None else pair.target_text
            try:
                source_indices = json.loads(pair.src_indices or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                source_indices = []
            translation_only = not bool(source_indices if isinstance(source_indices, list) else [])
            kind = "增译" if translation_only else "缺译" if not reviewed_target else "对齐"
            llm_status = (
                "已校对" if segment is not None and bool(segment.llm_provider)
                else "人工确认" if segment is not None and segment.status == "confirmed"
                else "未校对"
            )
            rows.append(ProofreadingExportRow(
                order=pair.pair_order,
                kind=kind,
                source_text=TRANSLATION_ONLY_SOURCE_LABEL if translation_only else source_text,
                original_target_text=original_target or "",
                reviewed_target_text=reviewed_target or "",
                changed=(reviewed_target or "") != (original_target or ""),
                confirmation_status="已确认" if segment is not None and segment.status == "confirmed" else "未确认",
                llm_status=llm_status,
                confidence=pair.confidence,
                method=pair.method,
                pair_id=str(pair.id),
                block_type=pair.block_type,
                block_index=pair.block_index,
                row_index=pair.row_index,
                cell_index=pair.cell_index,
                segment_id=str(segment.id) if segment is not None else "",
                comment_text=comments.get(str(segment.id), "") if segment is not None else "",
            ))
        return rows, file_record

    bindings = db.query(ProofreadingColumnBinding).filter_by(batch_id=batch.id).all()
    file_record = db.get(FileRecord, bindings[0].file_record_id) if bindings else None
    ordered_items: list[tuple[int, int, int, int, ProofreadingSegmentBaseline, Segment]] = []
    for binding in bindings:
        items = db.query(ProofreadingSegmentBaseline, Segment).join(
            Segment, Segment.id == ProofreadingSegmentBaseline.segment_id,
        ).filter(ProofreadingSegmentBaseline.binding_id == binding.id).all()
        if batch.batch_kind == "workflow_segments":
            from app.services.workflow_proofreading import batch_config
            step_id = batch_config(batch).get("workflow_step_id")
            items = [(baseline, segment) for baseline, segment in items if str(segment.workflow_step_id) == step_id]
        for baseline, segment in items:
            ordered_items.append((
                baseline.sheet_index,
                baseline.row_index,
                binding.target_column,
                int(segment.sequence_index or 0),
                baseline,
                segment,
            ))
    comments = collect_proofreading_comments(db, batch, [
        (segment, baseline) for *_, baseline, segment in ordered_items
    ]) if include_comments else {}
    rows = []
    for order, (_, _, _, _, baseline, segment) in enumerate(sorted(ordered_items, key=lambda item: item[:4])):
        rows.append(ProofreadingExportRow(
            order=order,
            kind="缺译" if not segment.target_text else "对齐",
            source_text=segment.source_text,
            original_target_text=baseline.original_target_text,
            reviewed_target_text=segment.target_text or "",
            changed=(segment.target_text or "") != (baseline.original_target_text or ""),
            confirmation_status="已确认" if segment.status == "confirmed" else "未确认",
            llm_status="已校对" if segment.llm_provider else "未校对",
            confidence=None,
            method="xlsx",
            block_type=segment.block_type,
            block_index=segment.block_index,
            row_index=baseline.row_index,
            cell_index=segment.cell_index,
            segment_id=str(segment.id),
            comment_text=comments.get(str(segment.id), ""),
        ))
    return rows, file_record


def target_revision_export_available(batch: ProofreadingBatch) -> bool:
    """目标 DOCX 原件存在时，才允许生成保留目标排版的修订版。"""
    config = _batch_config(batch)
    target_filename = str(config.get("target_filename") or "")
    return (
        Path(target_filename).suffix.lower() == ".docx"
        and target_cache_path(batch.id, target_filename, create_parent=False).is_file()
    )


def is_xlsx_pair(batch: ProofreadingBatch) -> bool:
    return batch.batch_kind == "document_pair" and _batch_config(batch).get("pair_format") == "xlsx"


def export_batch_is_busy(batch: ProofreadingBatch) -> bool:
    """解析、对齐或校对仍在生成时，不导出中间状态。"""
    return getattr(batch, "status", None) in {"aligning", "queued", "running", "canceling"} or (
        batch.batch_kind == "document_pair" and (
            getattr(batch, "alignment_status", None) in {"aligning", "canceling"}
            or getattr(batch, "workflow_stage", "not_applicable") == "import"
        )
    )


def build_export_readiness(db: Session, batch: ProofreadingBatch) -> dict[str, Any]:
    rows, file_record = build_proofreading_export_rows(db, batch)
    stage = getattr(batch, "workflow_stage", "not_applicable")
    unavailable_formats: dict[str, str] = {}
    if batch.batch_kind == "document_pair":
        available_formats = ["proofreading_audit_xlsx"]
        if stage == "proofreading" and is_xlsx_pair(batch):
            available_formats = ["proofreading_audit_xlsx"]
            target_filename = str(_batch_config(batch).get("target_filename") or "")
            if target_cache_path(batch.id, target_filename, create_parent=False).is_file():
                available_formats.append("proofreading_xlsx_target")
        elif stage == "proofreading":
            available_formats = [
                "proofreading_docx_layout",
                "proofreading_docx_ordered",
                "proofreading_audit_xlsx",
            ]
            if target_revision_export_available(batch):
                available_formats.insert(0, "proofreading_docx_target_revisions")
    elif batch.batch_kind == "workflow_segments":
        available_formats = ["proofreading_audit_xlsx"]
        from app.services.workflow_proofreading_export import workflow_revision_export_unavailable_reason
        reason = workflow_revision_export_unavailable_reason(file_record)
        if not rows:
            reason = "当前文件没有处于校对阶段的句段。"
        if reason:
            unavailable_formats["proofreading_docx_revisions"] = reason
        else:
            available_formats.append("proofreading_docx_revisions")
    else:
        available_formats = ["proofreading_xlsx_original", "proofreading_docx_ordered"]
    if batch.batch_kind == "document_pair" and stage in {"alignment", "proofreading"}:
        if "proofreading_docx_ordered" not in available_formats:
            available_formats.append("proofreading_docx_ordered")
    if export_batch_is_busy(batch):
        for format_name in available_formats:
            unavailable_formats[format_name] = "请先完成或结束当前解析、对齐或校对任务再导出。"
        available_formats = []
    return {
        "batch_id": str(batch.id),
        "total": len(rows),
        "confirmed": sum(row.confirmation_status == "已确认" for row in rows),
        "unconfirmed": sum(row.confirmation_status != "已确认" for row in rows),
        "missing_translation": sum(row.kind == "缺译" for row in rows),
        "translation_only": sum(row.kind == "增译" for row in rows),
        "translation_only_unreviewed": sum(row.kind == "增译" and row.llm_status == "未校对" for row in rows),
        "llm_failed": int(batch.failed_segments or 0),
        "available_formats": available_formats,
        "unavailable_formats": unavailable_formats,
        "has_warnings": any((
            any(row.confirmation_status != "已确认" for row in rows),
            any(row.kind == "缺译" for row in rows),
            any(row.kind == "增译" and row.llm_status == "未校对" for row in rows),
            bool(batch.failed_segments),
        )),
    }


def _batch_config(batch: ProofreadingBatch) -> dict[str, Any]:
    try:
        value = json.loads(batch.config_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _project_boundaries(before: str, after: str, boundaries: list[int]) -> list[int]:
    """把旧文本边界投影到新文本，保证边界单调且覆盖全部新文本。"""
    matcher = SequenceMatcher(a=before, b=after, autojunk=False)
    opcodes = matcher.get_opcodes()
    projected: list[int] = []
    for boundary in boundaries:
        mapped = len(after)
        for _tag, i1, i2, j1, j2 in opcodes:
            if boundary < i1:
                mapped = j1
                break
            if i1 <= boundary <= i2:
                if i2 == i1:
                    mapped = j2
                elif j2 == j1:
                    mapped = j1
                else:
                    ratio = (boundary - i1) / (i2 - i1)
                    mapped = j1 + round(ratio * (j2 - j1))
                break
        projected.append(max(0, min(len(after), mapped)))
    for index in range(1, len(projected)):
        projected[index] = max(projected[index], projected[index - 1])
    return projected


PAIR_TARGET_JOINER = "\n"


def _part_spans(parts: list[str], joiner: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for index, part in enumerate(parts):
        end = cursor + len(part)
        spans.append((cursor, end))
        cursor = end + (len(joiner) if index < len(parts) - 1 else 0)
    return spans


def _split_text_by_projection(parts: list[str], joiner: str, reviewed: str) -> list[str]:
    """按原有片段边界拆分修改后的文本，各片段首尾相接，不丢失任何字符。"""
    if not parts:
        return []
    boundaries = [end for _, end in _part_spans(parts, joiner)[:-1]]
    cuts = [0, *_project_boundaries(joiner.join(parts), reviewed, boundaries), len(reviewed)]
    return [reviewed[cuts[index]:cuts[index + 1]] for index in range(len(parts))]


def _split_reviewed_text(original_parts: list[str], reviewed: str) -> list[str]:
    """把一个对齐对的校对后译文拆回它所含的目标单元。

    对齐对的 target_text 由单元文本以换行连接而成，拆分必须使用同一连接符，
    否则未修改的多单元对齐对也会在后续单元前多出换行。
    """
    if not original_parts:
        return []
    if len(original_parts) == 1:
        return [reviewed]
    if reviewed == PAIR_TARGET_JOINER.join(original_parts):
        return list(original_parts)
    lines = reviewed.split(PAIR_TARGET_JOINER)
    if len(lines) == len(original_parts):
        return lines
    return _split_text_by_projection(original_parts, PAIR_TARGET_JOINER, reviewed)


def _trim_edit(old_text: str, start: int, end: int, replacement: str) -> tuple[int, int, str]:
    """去掉编辑前后与原文相同的部分，使编辑只覆盖真正变化的字符。

    一个对齐单元可能横跨多个 DOCX 句段（如同一单元格内的两句），整段替换会让偏移映射
    只能按比例分配。收缩后未变化的句段不再受影响。被替换的旧文本至少保留 1 个字符，
    避免纯插入落在句段之间的连接符上而丢失。
    """
    old_slice = old_text[start:end]
    limit = min(len(old_slice), len(replacement))
    prefix = 0
    while prefix < limit and old_slice[prefix] == replacement[prefix]:
        prefix += 1
    suffix = 0
    while suffix < limit - prefix and old_slice[-1 - suffix] == replacement[-1 - suffix]:
        suffix += 1
    if prefix + suffix >= len(old_slice):
        if suffix:
            suffix -= 1
        elif prefix:
            prefix -= 1
    return (
        start + prefix,
        end - suffix,
        replacement[prefix:len(replacement) - suffix],
    )


def _map_offset(position: int, edits: list[tuple[int, int, str]]) -> int:
    """按已知编辑把旧文本偏移换算为新文本偏移；未被编辑的文字偏移只做平移。"""
    delta = 0
    for start, end, replacement in edits:
        if position <= start:
            break
        if position < end:
            ratio = (position - start) / (end - start)
            return start + delta + round(ratio * len(replacement))
        delta += len(replacement) - (end - start)
    return position + delta


def _apply_block_edits(
    parts: list[str],
    joiner: str,
    edits: list[tuple[int, int, str]],
) -> list[str]:
    """把编辑应用到由 parts 拼成的块文本，并按原句段边界拆回各句段。

    edits 为按起点升序、互不重叠的 (start, end, replacement)，偏移基于 joiner.join(parts)。
    """
    old_text = joiner.join(parts)
    new_text = old_text
    for start, end, replacement in reversed(edits):
        new_text = f"{new_text[:start]}{replacement}{new_text[end:]}"
    return [
        new_text[_map_offset(start, edits):_map_offset(end, edits)]
        for start, end in _part_spans(parts, joiner)
    ]


def _workspace_block_key(segment: dict[str, Any]) -> tuple[int, int | None, int | None]:
    return (
        int(segment.get("block_index") or 0),
        segment.get("row_index"),
        segment.get("cell_index"),
    )


def _unit_block_key(unit: AlignUnit) -> tuple[int, int | None, int | None]:
    return (unit.block_index, unit.row_index, unit.cell_index)


def _project_pair_texts_onto_units(
    pairs: list[DocumentAlignmentPair],
    text_by_pair: dict[str, str],
    units_by_index: dict[int, AlignUnit],
    target_units: list[AlignUnit],
) -> dict[int, str]:
    """把每个对齐对的译文投影到目标 DOCX 单元，返回 单元序号 -> 新文本。

    只记录文本确实不同于目标原件的单元；未记录的单元原样保留。
    """
    replacement_by_unit: dict[int, str] = {}
    deferred_insertions: list[tuple[int, str]] = []
    last_target_index: int | None = None
    for pair in pairs:
        try:
            indices = json.loads(pair.tgt_indices or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            indices = []
        valid_indices: list[int] = []
        for index in indices if isinstance(indices, list) else []:
            try:
                normalized_index = int(index)
            except (TypeError, ValueError):
                continue
            if normalized_index in units_by_index:
                valid_indices.append(normalized_index)
        indices = valid_indices
        text = str(text_by_pair.get(str(pair.id), pair.target_text or "") or "")
        if indices:
            original_parts = [units_by_index[index].text for index in indices]
            pieces = _split_reviewed_text(original_parts, text.strip())
            for index, piece in zip(indices, pieces):
                piece = piece.strip()
                if piece != units_by_index[index].text:
                    replacement_by_unit[index] = piece
            last_target_index = indices[-1]
        elif text:
            # 原文有而目标文档无的段落没有天然版式锚点，先挂到前一目标单元；
            # 若位于文首，则在完成遍历后挂到后一目标单元之前。
            deferred_insertions.append((last_target_index if last_target_index is not None else -1, text))

    def current_unit_text(index: int) -> str:
        return replacement_by_unit.get(index, units_by_index[index].text)

    for anchor_index, inserted in deferred_insertions:
        if anchor_index >= 0 and anchor_index in units_by_index:
            replacement_by_unit[anchor_index] = f"{current_unit_text(anchor_index)}\n{inserted}"
        elif target_units:
            first = target_units[0].index
            replacement_by_unit[first] = f"{inserted}\n{current_unit_text(first)}"
    return replacement_by_unit


def _apply_unit_replacements_to_block(
    source_parts: list[str],
    joiner: str,
    block_units: list[AlignUnit],
    replacement_by_unit: dict[int, str],
    *,
    batch_id: Any,
) -> list[str]:
    """把单元级替换应用到一个块，并按原句段边界拆回各句段文本。"""
    old_block = joiner.join(source_parts)
    # 对齐单元的偏移相对于“去掉首尾空白后的块文本”，这里换算到未裁剪的拼接文本。
    leading = len(old_block) - len(old_block.lstrip())
    edits: list[tuple[int, int, str]] = []
    for unit in block_units:
        replacement = replacement_by_unit.get(unit.index)
        if replacement is None:
            continue
        start = unit.source_start + leading
        end = unit.source_end + leading
        span_text = old_block[start:end]
        if span_text.strip() != unit.text:
            logger.warning(
                "target revision export skipped edit: unit %s offsets do not match block text (batch=%s)",
                unit.index, batch_id,
            )
            continue
        left_pad = len(span_text) - len(span_text.lstrip())
        right_pad = len(span_text) - len(span_text.rstrip())
        edits.append(_trim_edit(old_block, start + left_pad, end - right_pad, replacement))
    if not edits:
        return list(source_parts)
    edits.sort()
    return _apply_block_edits(source_parts, joiner, edits)


def _build_target_revision_payload(
    db: Session,
    batch: ProofreadingBatch,
    target_bytes: bytes,
    target_filename: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """将对齐对的校对结果投影到目标 DOCX 的原始句段，并生成校对阶段的修订。

    修订基线是进入校对阶段时的译文（ProofreadingSegmentBaseline），而不是目标 DOCX 原件：
    用户在对齐阶段手动调整的译文属于基线，会静默写入导出文档，不带修订痕迹。
    做法是把“基线译文”和“当前译文”各自投影一遍，仅当同一句段两次结果不同才生成修订。
    """
    from app.services.document_workspace import parse_docx_workspace

    config = _batch_config(batch)
    granularity = str(config.get("granularity") or "sentence")
    target_units = parse_side(target_bytes, target_filename, granularity)
    units_by_index = {unit.index: unit for unit in target_units}
    rows = build_proofreading_export_rows(db, batch, include_comments=True)[0]
    reviewed_by_pair = {row.pair_id: row.reviewed_target_text for row in rows}
    baseline_by_pair = {row.pair_id: row.original_target_text for row in rows}
    pairs = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(
        DocumentAlignmentPair.pair_order,
    ).all()

    # 按目标单元的真实位置分配理由，避免重复译文导致批注串位。
    reason_by_pair = {row.pair_id: getattr(row, "comment_text", "") for row in rows if getattr(row, "comment_text", "")}
    reasons_by_unit: dict[int, list[tuple[str, str]]] = {}
    previous_target: int | None = None
    for pair in pairs:
        try:
            indices = json.loads(pair.tgt_indices or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            indices = []
        valid: list[int] = []
        for index in indices if isinstance(indices, list) else []:
            try:
                normalized = int(index)
            except (TypeError, ValueError):
                continue
            if normalized in units_by_index:
                valid.append(normalized)
        reason = reason_by_pair.get(str(pair.id), "")
        anchors = valid or ([previous_target if previous_target is not None else target_units[0].index] if target_units else [])
        if reason:
            for index in anchors:
                reasons_by_unit.setdefault(index, []).append((str(pair.id), reason))
        if valid:
            previous_target = valid[-1]

    final_replacements = _project_pair_texts_onto_units(pairs, reviewed_by_pair, units_by_index, target_units)
    baseline_replacements = _project_pair_texts_onto_units(pairs, baseline_by_pair, units_by_index, target_units)

    workspace_segments = list(parse_docx_workspace(target_bytes).get("segments", []))
    grouped: dict[tuple[int, int | None, int | None], list[dict[str, Any]]] = {}
    for segment in workspace_segments:
        grouped.setdefault(_workspace_block_key(segment), []).append(segment)
    units_by_block: dict[tuple[int, int | None, int | None], list[AlignUnit]] = {}
    for unit in target_units:
        units_by_block.setdefault(_unit_block_key(unit), []).append(unit)

    export_segments: list[dict[str, Any]] = []
    revisions: list[dict[str, Any]] = []
    located_comment_pairs: set[str] = set()
    for block_key, block_segments in grouped.items():
        block_type = str(block_segments[0].get("block_type") or "paragraph")
        if granularity == "sentence":
            raw_parts = [
                str(item.get("display_text") or item.get("source_text") or "").strip()
                for item in block_segments
            ]
            joiner = "\n" if block_type == "table_cell" else " "
        else:
            raw_parts = [
                str(item.get("display_text") or item.get("source_text") or "")
                for item in block_segments
            ]
            joiner = ""
        kept_pairs = [(segment, part) for segment, part in zip(block_segments, raw_parts) if part]
        if not kept_pairs:
            continue
        kept_segments = [segment for segment, _ in kept_pairs]
        source_parts = [part for _, part in kept_pairs]
        block_units = units_by_block.get(block_key, [])

        final_parts = _apply_unit_replacements_to_block(
            source_parts, joiner, block_units, final_replacements, batch_id=batch.id,
        )
        baseline_parts = _apply_unit_replacements_to_block(
            source_parts, joiner, block_units, baseline_replacements, batch_id=batch.id,
        )
        spans = _part_spans(source_parts, joiner)
        leading = len(joiner.join(source_parts)) - len(joiner.join(source_parts).lstrip())
        for segment, before_text, after_text, (start, end) in zip(kept_segments, baseline_parts, final_parts, spans):
            payload = dict(segment)
            payload["target_text"] = after_text
            payload["target_html"] = None
            export_segments.append(payload)
            sentence_id = str(segment.get("sentence_id") or "")
            if sentence_id and before_text != after_text:
                matched_reasons = [
                    item for unit in block_units
                    if max(start, unit.source_start + leading) < min(end, unit.source_end + leading)
                    for item in reasons_by_unit.get(unit.index, [])
                ]
                located_comment_pairs.update(pair_id for pair_id, _ in matched_reasons)
                reasons = list(dict.fromkeys(reason for _, reason in matched_reasons))
                revisions.append({
                    "id": f"proofreading-{batch.id}-{sentence_id}",
                    "sentence_id": sentence_id,
                    "before_text": before_text,
                    "after_text": after_text,
                    "status": "pending",
                    "source": "manual",
                    "author": {"name": "校对工作流"},
                    "comment_text": "\n".join(reasons),
                })
    if set(reason_by_pair) - located_comment_pairs:
        raise ValueError("无法安全定位部分校对建议的完整译文范围，请使用上下对照 Word 导出。")
    return export_segments, revisions


def export_target_docx_with_revisions(
    db: Session, batch: ProofreadingBatch, *, release_transaction_before_render: bool = False,
) -> tuple[bytes, str]:
    """以目标 DOCX 原件为母版，导出校对后的真实 Word 修订痕迹。"""
    from app.services.document_exporter import export_translated_docx

    if batch.batch_kind != "document_pair":
        raise ValueError("目标原格式修订导出仅用于双文档校对批次。")
    config = _batch_config(batch)
    target_filename = str(config.get("target_filename") or "")
    if Path(target_filename).suffix.lower() != ".docx":
        raise ValueError("目标原格式修订导出目前仅支持 DOCX 目标文档。")
    path = target_cache_path(batch.id, target_filename, create_parent=False)
    if not path.is_file():
        raise ValueError("该历史批次未保存目标文档原件，请重新导入双文档后再导出。")
    target_bytes = path.read_bytes()
    segments, revisions = _build_target_revision_payload(
        db, batch, target_bytes, target_filename,
    )
    target_language = batch.target_language
    if release_transaction_before_render:
        db.expunge_all()
        db.rollback()
    content = export_translated_docx(
        target_bytes,
        segments,
        target_language=target_language,
        revisions=revisions,
        include_revision_marks=True,
    )
    if not _docx_package_is_well_formed(content):
        raise ValueError("目标原格式修订文档生成失败，请检查目标 DOCX 后重试。")
    return content, f"{Path(target_filename).stem}_校对版_保留目标格式_含修订.docx"


def export_alignment_csv(pairs: list[DocumentAlignmentPair]) -> bytes:
    """导出对齐草稿；UTF-8 BOM 保证 Excel 直接打开中文不乱码。"""
    output = StringIO(newline="")
    fieldnames = [
        "pair_index", "source_indices", "target_indices", "source_text", "target_text",
        "confidence", "confidence_level", "method", "semantic_score", "status",
        "operation", "features_json",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for pair in pairs:
        features = json.loads(pair.features or "{}")
        if pair.source_text and not pair.target_text:
            status = "缺译"
        elif pair.target_text and not pair.source_text:
            status = "增译"
        elif pair.confidence_level == "low":
            status = "低置信"
        elif pair.confidence_level == "medium":
            status = "建议复核"
        else:
            status = "已对齐"
        writer.writerow({
            "pair_index": pair.pair_order,
            "source_indices": ",".join(map(str, json.loads(pair.src_indices or "[]"))),
            "target_indices": ",".join(map(str, json.loads(pair.tgt_indices or "[]"))),
            "source_text": pair.source_text,
            "target_text": pair.target_text,
            "confidence": pair.confidence,
            "confidence_level": pair.confidence_level,
            "method": pair.method,
            "semantic_score": features.get("semantic_similarity", features.get("absorbed_gap_similarity", "")),
            "status": status,
            "operation": features.get("op", ""),
            "features_json": json.dumps(features, ensure_ascii=False, separators=(",", ":")),
        })
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def export_workflow_proofreading_xlsx(
    db: Session, batch: ProofreadingBatch, *, release_transaction_before_render: bool = False,
) -> tuple[bytes, str]:
    """普通工作流的校对报告，使用原句段顺序及当前有效校对基线。"""
    from app.models import TranslationReviewReport

    rows, file = build_proofreading_export_rows(db, batch)
    baselines = db.query(ProofreadingSegmentBaseline, Segment).join(
        Segment, Segment.id == ProofreadingSegmentBaseline.segment_id,
    ).filter(ProofreadingSegmentBaseline.batch_id == batch.id).all()
    baseline_by_id = {baseline.segment_id: baseline for baseline, _ in baselines}
    reason_by_id: dict = {}
    items = db.query(TranslationReviewReportItem).join(
        TranslationReviewReport, TranslationReviewReport.id == TranslationReviewReportItem.report_id,
    ).filter(
        TranslationReviewReport.proofreading_batch_id == batch.id,
        TranslationReviewReportItem.status != "resolved",
    ).order_by(TranslationReviewReport.created_at.desc(), TranslationReviewReportItem.created_at.desc()).all()
    for item in items:
        baseline = baseline_by_id.get(item.segment_id)
        if baseline is not None and item.created_at >= baseline.created_at:
            reason_by_id.setdefault(item.segment_id, item.reason)
    reason_by_position = {
        baseline.row_index: reason_by_id.get(segment.id, "") for baseline, segment in baselines
    }
    filename = f"{Path(file.filename if file else batch.filename).stem}_校对报告.xlsx"
    if release_transaction_before_render:
        db.expunge_all()
        db.rollback()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "校对报告"
    sheet.append(["序号", "原文", "进入校对时译文", "当前译文", "是否修改", "确认状态", "修改建议或失败原因"])
    for row in rows:
        sheet.append([
            row.order + 1, row.source_text, row.original_target_text, row.reviewed_target_text,
            "是" if row.changed else "否", row.confirmation_status,
            reason_by_position.get(row.row_index, ""),
        ])
    # 所有内容按文本输出，避免原文以等号开头时被 Excel 当成公式。
    for cells in sheet:
        for cell in cells:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(fill_type="solid", fgColor="FFD9EAF7")
    for column in ("B", "C", "D", "G"):
        sheet.column_dimensions[column].width = 48
    sheet.freeze_panes = "B2"
    sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue(), filename


def export_document_pair_xlsx(db: Session, batch: ProofreadingBatch) -> tuple[bytes, str]:
    """导出纯对齐结果，不读取或生成校对后译文。"""
    if batch.batch_kind == "workflow_segments":
        return export_workflow_proofreading_xlsx(db, batch)
    if is_xlsx_pair(batch):
        return export_xlsx_pair_audit(db, batch)
    pairs = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(
        DocumentAlignmentPair.pair_order,
    ).all()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "双文档对齐"
    sheet.append([
        "序号", "对齐状态", "原文", "译文", "置信等级", "置信度",
        "对齐方法", "原文单元索引", "译文单元索引",
    ])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(fill_type="solid", fgColor="FFD9EAF7")
    for pair in pairs:
        source_indices = json.loads(pair.src_indices or "[]")
        target_indices = json.loads(pair.tgt_indices or "[]")
        status = (
            "原文无对应译文" if source_indices and not target_indices
            else "译文无对应原文" if target_indices and not source_indices
            else "低置信" if pair.confidence_level == "low"
            else "建议复核" if pair.confidence_level == "medium"
            else "已对齐"
        )
        sheet.append([
            pair.pair_order + 1, status, pair.source_text or "", pair.target_text or "",
            pair.confidence_level, pair.confidence, pair.method,
            ",".join(map(str, source_indices)), ",".join(map(str, target_indices)),
        ])
        if status in {"原文无对应译文", "译文无对应原文", "低置信"}:
            cell = sheet.cell(row=sheet.max_row, column=2)
            cell.font = Font(color="FF9C0006", bold=True)
            cell.fill = PatternFill(fill_type="solid", fgColor="FFFFC7CE")
        for cell in sheet[sheet.max_row]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:I{sheet.max_row}"
    sheet.sheet_view.showGridLines = False
    for column, width in zip("ABCDEFGHI", (9, 18, 48, 48, 12, 12, 20, 20, 20)):
        sheet.column_dimensions[column].width = width
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue(), f"{Path(batch.filename).stem}_原文译文对齐表.xlsx"


def export_xlsx_pair_audit(db: Session, batch: ProofreadingBatch) -> tuple[bytes, str]:
    """独立报告保留两侧位置；对齐阶段只报告原译文，校对阶段包含修订。"""
    from .xlsx import pair_locations

    config = _batch_config(batch)
    proofreading = getattr(batch, "workflow_stage", "") == "proofreading"
    rows, file_record = build_proofreading_export_rows(db, batch)
    pairs = db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(DocumentAlignmentPair.pair_order).all()
    reasons: dict[str, str] = {}
    if proofreading and file_record is not None:
        segments = {s.id: s for s in db.query(Segment).filter_by(file_record_id=file_record.id).all()}
        for item in db.query(TranslationReviewReportItem).filter_by(file_record_id=file_record.id).order_by(
            TranslationReviewReportItem.created_at, TranslationReviewReportItem.id,
        ).all():
            segment = segments.get(item.segment_id)
            if segment is not None and item.suggested_target_text == segment.target_text and item.reason:
                reasons[str(_segment_metadata(segment).get("alignment_pair_id", ""))] = item.reason
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Excel校对报告" if proofreading else "Excel对齐报告"
    headers = ["序号", "状态", "原文位置", "译文位置", "原文", "原译文"]
    if proofreading:
        headers += ["校对译文", "是否修改", "确认状态", "修改理由"]
    headers += ["结构提示"]
    sheet.append(headers)
    for row, pair in zip(rows, pairs):
        features = json.loads(pair.features or "{}")
        values = [row.order + 1, row.kind,
                  pair_locations(config, json.loads(pair.src_indices or "[]"), "source"),
                  pair_locations(config, json.loads(pair.tgt_indices or "[]"), "target"),
                  pair.source_text, row.original_target_text if proofreading else pair.target_text]
        if proofreading:
            values += [row.reviewed_target_text, "是" if row.changed else "否", row.confirmation_status,
                       reasons.get(row.pair_id, "人工修改" if row.changed else "")]
        values += [features.get("structure_warning", "")]
        sheet.append(values)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill(fill_type="solid", fgColor="FFD9EAF7")
    # 报告文本始终按字符串写入，避免把以等号开头的译文当成公式。
    for cells in sheet.iter_rows(min_row=2):
        for cell in cells:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    from openpyxl.utils import get_column_letter
    for index, title in enumerate(headers, 1):
        sheet.column_dimensions[get_column_letter(index)].width = 48 if "文" in title else 32 if "位置" in title or "理由" in title or "提示" in title else 14
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    coverage = workbook.create_sheet("覆盖说明")
    coverage.append(["项目", "说明"])
    coverage.append(["处理范围", "文字单元格及可编辑图形文字；公式、数字、协议标签保持原样。"])
    coverage.append(["配对方式", "按工作表名称和单元格位置；图形按对象标识及段落位置。"])
    coverage.append(["图片文字", "图片内部文字未自动校对，请人工检查。"])
    for side, label in (("source", "原文"), ("target", "译文")):
        summary = config.get("xlsx_summary", {}).get(side, {})
        coverage.append([label + "文字单元格", summary.get("text_cells", 0)])
        coverage.append([label + "图形文字段落", summary.get("drawing_texts", 0)])
        coverage.append([label + "公式单元格", summary.get("formula_cells", 0)])
    coverage.column_dimensions["A"].width = 25
    coverage.column_dimensions["B"].width = 95
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    suffix = "校对报告" if proofreading else "对齐报告"
    return output.getvalue(), f"{Path(batch.filename).stem}_{suffix}.xlsx"


def export_target_xlsx(db: Session, batch: ProofreadingBatch) -> tuple[bytes, str]:
    """以译文副本为母版，只回写有明确目标位置的已修改文字。"""
    from .xlsx import patch_xlsx_text

    if not is_xlsx_pair(batch) or getattr(batch, "workflow_stage", "") != "proofreading":
        raise ValueError("仅在双 Excel 校对阶段允许导出目标原格式。")
    config = _batch_config(batch)
    filename = str(config.get("target_filename") or "")
    path = target_cache_path(batch.id, filename, create_parent=False)
    if not path.is_file():
        raise ValueError("译文 Excel 原件缺失，请重新导入。")
    rows, _ = build_proofreading_export_rows(db, batch)
    rows_by_pair = {row.pair_id: row for row in rows}
    manifest = config["xlsx_manifest"]["target"]
    replacements = []
    for pair in db.query(DocumentAlignmentPair).filter_by(batch_id=batch.id).order_by(DocumentAlignmentPair.pair_order).all():
        row = rows_by_pair[str(pair.id)]
        indices = json.loads(pair.tgt_indices or "[]")
        if len(indices) != 1:
            original_parts = "\n".join(manifest[index]["text"] for index in indices)
            if row.reviewed_target_text != original_parts:
                raise ValueError("修改内容没有唯一的目标单元格或图形段落，请先拆分配对；也可导出独立校对报告。")
            continue
        item = manifest[indices[0]]
        # 包含对齐阶段的人工文本修订，不能只比较校对阶段基线。
        if row.reviewed_target_text != item["text"]:
            replacements.append((item, row.reviewed_target_text))
    raw_bytes = path.read_bytes()
    return patch_xlsx_text(raw_bytes, replacements), f"{Path(filename).stem}_校对版.xlsx"


def export_ordered_bilingual_docx(
    db: Session, batch: ProofreadingBatch, *, release_transaction_before_render: bool = False,
) -> tuple[bytes, str]:
    """生成顺序优先的原文在前双语 Word，不依赖源文件版式映射。"""
    rows, _ = build_proofreading_export_rows(db, batch, include_comments=True)
    filename = batch.filename
    if release_transaction_before_render:
        db.expunge_all()
        db.rollback()
    return _render_ordered_bilingual_docx(rows, filename)


def _render_ordered_bilingual_docx(rows: list[ProofreadingExportRow], filename: str) -> tuple[bytes, str]:
    from docx import Document
    from docx.oxml import parse_xml
    from docx.shared import Pt, RGBColor
    from app.services.docx_comments import SentenceComment, add_sentence_comments, wrap_sentence_comment

    document = Document()
    comments: list[SentenceComment] = []
    title = document.add_heading("双语校对文档", level=1)
    title.paragraph_format.keep_with_next = True
    for row in rows:
        source = document.add_paragraph()
        source.paragraph_format.keep_with_next = True
        source_run = source.add_run(row.source_text or TRANSLATION_ONLY_SOURCE_LABEL)
        source_run.bold = row.block_type == "heading"
        target = document.add_paragraph()
        target.paragraph_format.space_after = Pt(8)
        target_text = row.reviewed_target_text or MISSING_TRANSLATION_LABEL
        target_run = target.add_run(target_text)
        if row.kind == "增译":
            source_run.font.color.rgb = RGBColor(127, 96, 0)
        if row.kind == "缺译":
            target_run.bold = True
            target_run.font.color.rgb = RGBColor(156, 0, 6)
        if row.comment_text:
            comment_id = len(comments)
            comments.append(SentenceComment(row.segment_id, comment_id, row.comment_text))
            start, end, reference = wrap_sentence_comment([], comment_id)
            target._p.insert(list(target._p).index(target_run._r), parse_xml(ET.tostring(start)))
            target._p.append(parse_xml(ET.tostring(end)))
            target._p.append(parse_xml(ET.tostring(reference)))
    output = BytesIO()
    document.save(output)
    return add_sentence_comments(output.getvalue(), comments), f"{Path(filename).stem}_上下对照.docx"


def _normalize_export_text(value: str) -> str:
    return "".join((value or "").split())


def _docx_package_is_well_formed(content: bytes) -> bool:
    """执行不依赖 Office 的基础 OOXML 包完整性检查。"""
    try:
        with ZipFile(BytesIO(content)) as archive:
            names = set(archive.namelist())
            if archive.testzip() is not None:
                return False
            if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}.issubset(names):
                return False
            for name in names:
                if name.endswith((".xml", ".rels")):
                    ET.fromstring(archive.read(name))
    except (BadZipFile, KeyError, ET.ParseError, OSError):
        return False
    return True


def _layout_export_is_complete(
    content: bytes,
    rows: list[ProofreadingExportRow],
    *,
    source_content: bytes | None = None,
    document_parse_mode: str = "full",
    document_parse_options: dict[str, object] | str | None = None,
) -> bool:
    """用目标文本顺序门禁拦截内容缺失或错序的保留排版结果。"""
    if not _docx_package_is_well_formed(content):
        return False
    try:
        from app.services.document_workspace import parse_docx_workspace

        parsed = parse_docx_workspace(
            content,
            document_parse_mode=document_parse_mode,
            document_parse_options=document_parse_options,
        )
        output_text = _normalize_export_text("\n".join(
            str(item.get("display_text") or item.get("source_text") or "")
            for item in parsed.get("segments", [])
        ))
    except Exception:
        return False
    cursor = 0
    expected_counts: dict[str, int] = {}
    for row in rows:
        expected = _normalize_export_text(row.reviewed_target_text or MISSING_TRANSLATION_LABEL)
        if not expected:
            continue
        expected_counts[expected] = expected_counts.get(expected, 0) + 1
        position = output_text.find(expected, cursor)
        if position < 0:
            return False
        cursor = position + len(expected)
    if not all(output_text.count(text) >= count for text, count in expected_counts.items()):
        return False
    if source_content is not None:
        try:
            source_workspace = parse_docx_workspace(
                source_content,
                document_parse_mode=document_parse_mode,
                document_parse_options=document_parse_options,
            )
        except Exception:
            return False
        source_output_text = compact_match_core(output_text)
        source_cursor = 0
        for segment in source_workspace.get("segments", []):
            expected_source = compact_match_core(str(
                segment.get("display_text") or segment.get("source_text") or ""
            ))
            if not expected_source:
                continue
            position = source_output_text.find(expected_source, source_cursor)
            if position < 0:
                return False
            source_cursor = position + len(expected_source)
    return True


def _build_layout_export_segments(
    db: Session,
    file_record: FileRecord,
    rows: list[ProofreadingExportRow],
) -> list[dict[str, Any]]:
    """把增译临时锚定到前一有效源文块，避免改写持久化的对齐位置。"""
    segment_by_pair: dict[str, Segment] = {}
    for segment in db.query(Segment).filter_by(file_record_id=file_record.id).all():
        pair_id = str(_segment_metadata(segment).get("alignment_pair_id") or "")
        if pair_id:
            segment_by_pair[pair_id] = segment

    ordered_segments = [segment_by_pair.get(row.pair_id) for row in rows]
    export_segments: list[dict[str, Any]] = []
    for index, (row, segment) in enumerate(zip(rows, ordered_segments)):
        if segment is None:
            continue
        metadata = _segment_metadata(segment)
        anchor = segment
        if bool(metadata.get("translation_only")):
            anchor = next(
                (
                    candidate for candidate in reversed(ordered_segments[:index])
                    if candidate is not None and not bool(_segment_metadata(candidate).get("translation_only"))
                ),
                None,
            ) or next(
                (
                    candidate for candidate in ordered_segments[index + 1:]
                    if candidate is not None and not bool(_segment_metadata(candidate).get("translation_only"))
                ),
                segment,
            )
        target_text = segment.target_text or MISSING_TRANSLATION_LABEL
        if bool(metadata.get("translation_only")):
            target_text = f"{TRANSLATION_ONLY_EXPORT_LABEL}{target_text}"
        export_segments.append({
            "sentence_id": segment.sentence_id,
            "source_text": segment.source_text,
            "display_text": segment.display_text,
            "target_text": target_text,
            "target_html": None,
            "source_html": segment.source_html,
            "numbering_text": str(metadata.get("numbering_text") or ""),
            "matched_source_text": segment.matched_source_text,
            "sequence_index": row.order,
            "block_type": anchor.block_type,
            "block_index": anchor.block_index,
            "row_index": anchor.row_index,
            "cell_index": anchor.cell_index,
            "segment_metadata": metadata,
            "comment_text": row.comment_text,
        })
    return export_segments


def export_layout_bilingual_docx(
    db: Session, batch: ProofreadingBatch, *, release_transaction_before_render: bool = False,
) -> tuple[bytes, str, bool]:
    """优先保留源 DOCX 排版；完整性门禁失败时返回顺序优先版本。"""
    from app.services.file_record_service import load_file_record_source
    from app.services.task_file_service import export_bilingual_task_docx_with_layout

    rows, file_record = build_proofreading_export_rows(db, batch, include_comments=True)
    filename = batch.filename
    supported = file_record is not None and Path(file_record.filename).suffix.lower() == ".docx"
    raw_bytes = load_file_record_source(file_record) if supported else None
    export_segments = _build_layout_export_segments(db, file_record, rows) if raw_bytes is not None else []
    file_options = dict(filename=file_record.filename, document_parse_mode=file_record.document_parse_mode,
                        document_parse_options=file_record.document_parse_options,
                        target_language=file_record.target_language) if supported else {}
    if release_transaction_before_render:
        db.expunge_all()
        db.rollback()
    if raw_bytes is None:
        content, filename = _render_ordered_bilingual_docx(rows, filename)
        return content, filename, True
    try:
        exported = export_bilingual_task_docx_with_layout(
            raw_bytes=raw_bytes,
            filename=file_options["filename"],
            segments=export_segments,
            order="source_first",
            document_parse_mode=file_options["document_parse_mode"],
            document_parse_options=file_options["document_parse_options"],
            target_language=file_options["target_language"],
        )
        if _layout_export_is_complete(
            exported.content,
            rows,
            source_content=raw_bytes,
            document_parse_mode=file_options["document_parse_mode"],
            document_parse_options=file_options["document_parse_options"],
        ):
            return (
                exported.content,
                f"{Path(filename).stem}_双语校对版_保留排版.docx",
                False,
            )
    except Exception:
        pass
    content, filename = _render_ordered_bilingual_docx(rows, filename)
    return content, filename, True
