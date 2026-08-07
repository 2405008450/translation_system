from __future__ import annotations

from collections import Counter

from .features import rare_numbers, unit_numbering
from .parser import AlignUnit

MAX_BLOCK_UNITS = 40


def _candidate_anchors(src: list[AlignUnit], tgt: list[AlignUnit]) -> list[tuple[int, int, str]]:
    candidates: list[tuple[int, int, str]] = []
    src_numbers = Counter(unit_numbering(unit) for unit in src if unit_numbering(unit))
    tgt_numbers = Counter(unit_numbering(unit) for unit in tgt if unit_numbering(unit))
    common_unique = {key for key in src_numbers if src_numbers[key] == tgt_numbers[key] == 1}
    # 编号整体匹配率过低时放弃，防止错乱编号成为硬锚点。
    denominator = max(1, min(sum(src_numbers.values()), sum(tgt_numbers.values())))
    if len(common_unique) / denominator >= 0.3:
        src_map = {unit_numbering(unit): i for i, unit in enumerate(src) if unit_numbering(unit) in common_unique}
        tgt_map = {unit_numbering(unit): i for i, unit in enumerate(tgt) if unit_numbering(unit) in common_unique}
        candidates.extend((src_map[key], tgt_map[key], "anchor_number") for key in common_unique)

    tgt_cells = {(u.block_index, u.row_index, u.cell_index): i for i, u in enumerate(tgt) if u.block_type == "table_cell"}
    for i, unit in enumerate(src):
        key = (unit.block_index, unit.row_index, unit.cell_index)
        if unit.block_type == "table_cell" and key in tgt_cells:
            candidates.append((i, tgt_cells[key], "anchor_table"))

    common_rare = rare_numbers(src) & rare_numbers(tgt)
    src_rare = {number: i for i, unit in enumerate(src) for number in unit.numbers if number in common_rare}
    tgt_rare = {number: i for i, unit in enumerate(tgt) for number in unit.numbers if number in common_rare}
    candidates.extend((src_rare[number], tgt_rare[number], "anchor_number_rare") for number in common_rare)

    src_headings = [i for i, unit in enumerate(src) if unit.is_heading]
    tgt_headings = [i for i, unit in enumerate(tgt) if unit.is_heading]
    if src_headings and len(src_headings) == len(tgt_headings):
        candidates.extend((i, j, "anchor_heading") for i, j in zip(src_headings, tgt_headings))

    # 只保留双侧均严格递增的锚点，避免结构重排导致交叉区间。
    result: list[tuple[int, int, str]] = []
    for candidate in sorted(set(candidates), key=lambda item: (item[0], item[1])):
        if result and (candidate[0] <= result[-1][0] or candidate[1] <= result[-1][1]):
            continue
        result.append(candidate)
    return result


def _split_window(si: int, sj: int, ei: int, ej: int, kind: str) -> list[tuple[slice, slice, str]]:
    src_count, tgt_count = ei - si, ej - sj
    windows = max(1, (max(src_count, tgt_count) + MAX_BLOCK_UNITS - 1) // MAX_BLOCK_UNITS)
    result = []
    for window in range(windows):
        sa = si + round(src_count * window / windows)
        sb = si + round(src_count * (window + 1) / windows)
        ta = sj + round(tgt_count * window / windows)
        tb = sj + round(tgt_count * (window + 1) / windows)
        if sa != sb or ta != tb:
            result.append((slice(sa, sb), slice(ta, tb), kind if windows == 1 else "ratio_window"))
    return result


def build_anchor_blocks(src: list[AlignUnit], tgt: list[AlignUnit]) -> list[tuple[slice, slice, str]]:
    """按单调硬锚点切分，且强制每个区间不超过 40 个单元。"""
    anchors = _candidate_anchors(src, tgt)
    blocks: list[tuple[slice, slice, str]] = []
    si = sj = 0
    for ai, aj, kind in anchors:
        blocks.extend(_split_window(si, sj, ai, aj, "between_anchors"))
        blocks.append((slice(ai, ai + 1), slice(aj, aj + 1), kind))
        si, sj = ai + 1, aj + 1
    blocks.extend(_split_window(si, sj, len(src), len(tgt), "tail"))
    return blocks
