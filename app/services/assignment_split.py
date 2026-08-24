from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any, Literal, Sequence
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import FileRecord, Segment
from app.services.analytics_service import count_source_words
from app.services.document_statistics import normalize_document_statistics
from app.services.file_record_service import get_segment_ordering_for_file_record


SplitMode = Literal["by_part_count", "by_words_per_part"]


class AssignmentSplitError(ValueError):
    """拆分请求不合法，调用方应将消息作为 400 响应返回。"""


@dataclass(frozen=True)
class SplitUnit:
    """不可拆分的最小单元。

    一个句段永远不会在字数边界处被截断；同一段落或表格单元格中连续的
    多个句段还会合并为一个单元，与任务分配保存时的边界校验保持一致。
    """

    range_start: int
    range_end: int
    segment_count: int
    word_count: int


def _merge_block_key(row: Any) -> tuple[int, int | None, int | None]:
    return (
        int(getattr(row, "block_index", 0) or 0),
        getattr(row, "row_index", None),
        getattr(row, "cell_index", None),
    )


def _build_split_units(rows: Sequence[Any], fallback_words: dict[UUID, int]) -> list[SplitUnit]:
    units: list[SplitUnit] = []
    current_key: tuple[int, int | None, int | None] | None = None
    current_start = 0
    current_end = 0
    current_segments = 0
    current_words = 0

    for row in rows:
        position = int(row.display_position)
        key = _merge_block_key(row)
        word_count = int(row.source_word_count or 0)
        if word_count <= 0:
            word_count = max(0, int(fallback_words.get(row.id, 0)))

        if current_key is not None and key != current_key:
            units.append(
                SplitUnit(current_start, current_end, current_segments, current_words)
            )
            current_start = 0
            current_segments = 0
            current_words = 0

        if current_start == 0:
            current_start = position
            current_key = key
        current_end = position
        current_segments += 1
        current_words += word_count

    if current_start:
        units.append(SplitUnit(current_start, current_end, current_segments, current_words))
    return units


def _slice_units(
    units: Sequence[SplitUnit],
    *,
    range_start: int | None,
    range_end: int | None,
) -> list[SplitUnit]:
    if (range_start is None) != (range_end is None):
        raise AssignmentSplitError("子范围必须同时填写起始段和结束段。")
    if range_start is None or range_end is None:
        return list(units)
    if range_start > range_end:
        raise AssignmentSplitError("子范围起始段不能大于结束段。")
    if not units or range_start < units[0].range_start or range_end > units[-1].range_end:
        raise AssignmentSplitError("指定的句段子范围不存在。")

    start_index = next(
        (index for index, unit in enumerate(units) if unit.range_start == range_start),
        None,
    )
    end_index = next(
        (index for index, unit in enumerate(units) if unit.range_end == range_end),
        None,
    )
    if start_index is None or end_index is None or start_index > end_index:
        raise AssignmentSplitError(
            "指定的子范围会切断完整句段、段落或表格单元格，请调整到安全边界。"
        )
    return list(units[start_index : end_index + 1])


def split_units_by_weight(
    units: Sequence[SplitUnit],
    part_count: int,
    *,
    use_segment_weight: bool = False,
) -> list[list[SplitUnit]]:
    """在安全单元边界上选择最接近理想累计权重的切点。"""

    if not units:
        return []
    part_count = max(1, min(int(part_count), len(units)))
    weights = [unit.segment_count if use_segment_weight else unit.word_count for unit in units]
    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [unit.segment_count for unit in units]
        total_weight = sum(weights)

    cumulative: list[int] = []
    running = 0
    for weight in weights:
        running += weight
        cumulative.append(running)

    cut_indexes: list[int] = []
    previous_cut = -1
    for part_index in range(1, part_count):
        remaining_parts = part_count - part_index
        first_candidate = previous_cut + 1
        last_candidate = len(units) - remaining_parts - 1
        ideal = total_weight * part_index / part_count
        cut_index = min(
            range(first_candidate, last_candidate + 1),
            key=lambda index: (abs(cumulative[index] - ideal), index),
        )
        cut_indexes.append(cut_index)
        previous_cut = cut_index

    parts: list[list[SplitUnit]] = []
    start_index = 0
    for cut_index in [*cut_indexes, len(units) - 1]:
        parts.append(list(units[start_index : cut_index + 1]))
        start_index = cut_index + 1
    return parts


def _document_word_count(file_record: FileRecord) -> int | None:
    value = normalize_document_statistics(file_record.document_statistics).get("words")
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def build_assignment_split_preview(
    db: Session,
    file_record: FileRecord,
    *,
    mode: SplitMode,
    part_count: int | None = None,
    words_per_part: int | None = None,
    range_start: int | None = None,
    range_end: int | None = None,
) -> dict[str, Any]:
    ordered_segments = (
        db.query(
            Segment.id.label("id"),
            Segment.block_index.label("block_index"),
            Segment.row_index.label("row_index"),
            Segment.cell_index.label("cell_index"),
            Segment.source_word_count.label("source_word_count"),
            func.row_number()
            .over(order_by=get_segment_ordering_for_file_record(file_record))
            .label("display_position"),
        )
        .filter(Segment.file_record_id == file_record.id)
        .subquery()
    )
    rows = (
        db.query(
            ordered_segments.c.id,
            ordered_segments.c.block_index,
            ordered_segments.c.row_index,
            ordered_segments.c.cell_index,
            ordered_segments.c.source_word_count,
            ordered_segments.c.display_position,
        )
        .order_by(ordered_segments.c.display_position.asc())
        .all()
    )
    if not rows:
        raise AssignmentSplitError("文件尚无可拆分句段。")

    missing_ids = [row.id for row in rows if int(row.source_word_count or 0) <= 0]
    fallback_words: dict[UUID, int] = {}
    if missing_ids:
        fallback_rows = (
            db.query(Segment.id, Segment.source_text)
            .filter(Segment.id.in_(missing_ids))
            .all()
        )
        fallback_words = {
            segment_id: count_source_words(source_text or "")
            for segment_id, source_text in fallback_rows
        }

    all_units = _build_split_units(rows, fallback_words)
    units = _slice_units(all_units, range_start=range_start, range_end=range_end)
    segment_words = sum(unit.word_count for unit in units)
    total_segments = sum(unit.segment_count for unit in units)
    warnings: list[str] = []
    if missing_ids:
        warnings.append(
            f"有 {len(missing_ids)} 个句段缺少缓存字数，本次已按原文实时计算，未修改文件数据。"
        )

    if mode == "by_part_count":
        if part_count is None or part_count < 1:
            raise AssignmentSplitError("按份数拆分时，份数必须大于 0。")
        requested_part_count = part_count
    elif mode == "by_words_per_part":
        if words_per_part is None or words_per_part < 1:
            raise AssignmentSplitError("按每份字数拆分时，每份字数必须大于 0。")
        requested_part_count = max(1, ceil(segment_words / words_per_part))
    else:
        raise AssignmentSplitError("不支持的拆分模式。")

    actual_part_count = min(requested_part_count, len(units))
    if actual_part_count < requested_part_count:
        warnings.append(
            f"安全拆分单元只有 {len(units)} 个，已将份数从 {requested_part_count} 调整为 {actual_part_count}。"
        )
    if segment_words == 0:
        warnings.append("句段字数合计为 0，已按完整句段数量均分。")

    target_words = (
        words_per_part
        if mode == "by_words_per_part" and words_per_part is not None
        else segment_words / actual_part_count if actual_part_count else 0
    )
    oversized_units = [unit for unit in units if target_words > 0 and unit.word_count > target_words]
    if oversized_units:
        warnings.append(
            f"有 {len(oversized_units)} 个完整句段/段落块超过目标字数；为避免截断内容，已整体保留。"
        )

    grouped_parts = split_units_by_weight(
        units,
        actual_part_count,
        use_segment_weight=segment_words == 0,
    )
    parts: list[dict[str, Any]] = []
    for index, grouped_units in enumerate(grouped_parts, start=1):
        word_count = sum(unit.word_count for unit in grouped_units)
        parts.append(
            {
                "index": index,
                "range_start": grouped_units[0].range_start,
                "range_end": grouped_units[-1].range_end,
                "segment_count": sum(unit.segment_count for unit in grouped_units),
                "word_count": word_count,
                "word_percent": round(word_count * 100 / segment_words, 2) if segment_words else 0.0,
            }
        )

    return {
        "total_segments": total_segments,
        "segment_words": segment_words,
        "document_words": _document_word_count(file_record),
        "parts": parts,
        "warnings": warnings,
    }
