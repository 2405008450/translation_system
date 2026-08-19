from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from .dp import AlignPair, BoundaryKey, align_block
from .parser import AlignUnit


_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:第\s*)?\d{1,5}\s*(?:/|／|页\s*(?:共|of))\s*\d{1,5}\s*(?:页)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StructuralBlock:
    """一个可独立对应的文档粗块，以及它包含的原始句段单元。"""

    unit: AlignUnit
    members: tuple[AlignUnit, ...]


def _is_repeated_running_matter(unit: AlignUnit, frequencies: Counter[str]) -> bool:
    """识别不应参与正文对齐的重复页眉、页脚和独立页码。"""
    text = unit.text.strip()
    if _PAGE_NUMBER_RE.fullmatch(text):
        return True
    return (
        unit.block_type in {"header", "footer"}
        and bool(unit.norm_text)
        and frequencies[unit.norm_text] >= 2
    )


def partition_running_matter(
    units: list[AlignUnit],
) -> tuple[list[AlignUnit], list[AlignUnit]]:
    frequencies = Counter(unit.norm_text for unit in units if unit.norm_text)
    content: list[AlignUnit] = []
    ignored: list[AlignUnit] = []
    for unit in units:
        (ignored if _is_repeated_running_matter(unit, frequencies) else content).append(unit)
    return content, ignored


def _structural_key(unit: AlignUnit) -> tuple[object, ...]:
    if unit.block_type == "table_cell":
        # 表格先按逻辑行对应。行内各单元格和句段留给第二级细分。
        return ("table_row", unit.block_index, unit.row_index)
    return (unit.block_type, unit.block_index)


def build_structural_blocks(units: list[AlignUnit]) -> list[StructuralBlock]:
    """按正文段落、标题和表格逻辑行聚合连续句段。"""
    groups: list[list[AlignUnit]] = []
    for unit in units:
        if not groups or _structural_key(groups[-1][0]) != _structural_key(unit):
            groups.append([unit])
        else:
            groups[-1].append(unit)

    result: list[StructuralBlock] = []
    for index, members in enumerate(groups):
        first = members[0]
        text = "\n".join(member.text for member in members)
        numbers = tuple(number for member in members for number in member.numbers)
        # 粗块沿用表格号和行号；cell_index=0 只用于让既有表格匹配器把它识别为逻辑行。
        coarse = AlignUnit(
            index=index,
            text=text,
            norm_text="".join(member.norm_text for member in members),
            para_index=first.para_index,
            block_type=first.block_type,
            block_index=first.block_index,
            row_index=first.row_index,
            cell_index=0 if first.block_type == "table_cell" else first.cell_index,
            numbering=first.numbering,
            char_len=max(1, sum(member.char_len for member in members)),
            numbers=numbers,
            is_heading=first.is_heading,
            parent_segment_id=first.parent_segment_id,
            cell_key=(
                first.cell_key
                if len({member.cell_key for member in members if member.cell_key}) <= 1
                else ""
            ),
            row_key=first.row_key,
        )
        result.append(StructuralBlock(coarse, tuple(members)))
    return result


def _expand_members(
    indexes: list[int], blocks: list[StructuralBlock],
) -> list[AlignUnit]:
    return [member for index in indexes for member in blocks[index].members]


def _table_boundary_key(
    source: list[AlignUnit], target: list[AlignUnit],
) -> BoundaryKey | None:
    source_cells = {unit.cell_key for unit in source if unit.cell_key}
    target_cells = {unit.cell_key for unit in target if unit.cell_key}
    if not source_cells or not target_cells:
        return None
    ratio = min(len(source_cells), len(target_cells)) / max(len(source_cells), len(target_cells))
    use_row = ratio < 0.5
    return lambda unit: unit.row_key if use_row else unit.cell_key


def _align_by_cumulative_progress(
    source: list[AlignUnit], target: list[AlignUnit], *, max_lookahead: int = 6,
) -> list[AlignPair]:
    """按两侧累计文本进度配组，不因段落数量不同而预先制造缺口。

    这只是提交给 LLM 的保守种子。真正的漏译/增译由块内语义复核判断。
    """
    if not source:
        return [AlignPair([], [unit.index], 0.2, features={"gap": True}) for unit in target]
    if not target:
        return [AlignPair([unit.index], [], 0.2, features={"gap": True}) for unit in source]

    source_prefix = [0]
    target_prefix = [0]
    for unit in source:
        source_prefix.append(source_prefix[-1] + unit.char_len)
    for unit in target:
        target_prefix.append(target_prefix[-1] + unit.char_len)
    source_total = max(1, source_prefix[-1])
    target_total = max(1, target_prefix[-1])

    result: list[AlignPair] = []
    source_index = target_index = 0
    while source_index < len(source) and target_index < len(target):
        source_remaining = len(source) - source_index
        target_remaining = len(target) - target_index
        source_limit = min(source_remaining, max_lookahead)
        target_limit = min(target_remaining, max_lookahead)
        best: tuple[float, int, int] | None = None
        for source_count in range(1, source_limit + 1):
            source_progress = source_prefix[source_index + source_count] / source_total
            for target_count in range(1, target_limit + 1):
                target_progress = target_prefix[target_index + target_count] / target_total
                # 轻微惩罚大组合，只在累计进度确实更接近时采用 N:M。
                score = abs(source_progress - target_progress) + 0.0001 * (
                    source_count + target_count - 2
                )
                candidate = (score, source_count, target_count)
                if best is None or candidate < best:
                    best = candidate
        assert best is not None
        _, source_count, target_count = best
        source_group = source[source_index:source_index + source_count]
        target_group = target[target_index:target_index + target_count]
        result.append(AlignPair(
            [unit.index for unit in source_group],
            [unit.index for unit in target_group],
            0.6,
            method="hierarchical_seed",
            features={
                "op": f"{source_count}-{target_count}",
                "cumulative_progress_seed": True,
            },
        ))
        source_index += source_count
        target_index += target_count

    # 累计进度通常会同时到达末尾；极端长度差时将余量并入最后一个双侧配对，
    # 仍然交给 LLM 判断边界，避免程序先武断地标成漏译。
    if source_index < len(source):
        remainder = [unit.index for unit in source[source_index:]]
        result[-1].src_indices.extend(remainder)
        result[-1].features["tail_absorbed"] = True
    if target_index < len(target):
        remainder = [unit.index for unit in target[target_index:]]
        result[-1].tgt_indices.extend(remainder)
        result[-1].features["tail_absorbed"] = True
    return result


def build_hierarchical_seed_pairs(
    source: list[AlignUnit], target: list[AlignUnit], *, lang_ratio: float,
) -> tuple[list[AlignPair], list[AlignUnit], list[AlignUnit]]:
    """先对齐段落/表格行粗块，再在每个已对应粗块内进行确定性句段细分。

    返回的忽略单元稍后以显式缺口插回，既不污染正文，又保证所有原始下标恰好出现一次。
    """
    source_content, source_ignored = partition_running_matter(source)
    target_content, target_ignored = partition_running_matter(target)
    source_blocks = build_structural_blocks(source_content)
    target_blocks = build_structural_blocks(target_content)
    source_coarse = [block.unit for block in source_blocks]
    target_coarse = [block.unit for block in target_blocks]

    result: list[AlignPair] = []
    # 粗块只遵守全文单调顺序。表格号、行号仍保留在块元数据中，供后续 LLM
    # 识别边界；不把“第几个表格”硬绑定，避免两份文档表格数量不同导致整段错位。
    coarse_windows = [(slice(0, len(source_coarse)), slice(0, len(target_coarse)), "document_progress")]
    for source_slice, target_slice, anchor_method in coarse_windows:
        coarse_pairs = _align_by_cumulative_progress(
            source_coarse[source_slice],
            target_coarse[target_slice],
        )
        for coarse_pair in coarse_pairs:
            block_source = _expand_members(coarse_pair.src_indices, source_blocks)
            block_target = _expand_members(coarse_pair.tgt_indices, target_blocks)
            boundary_key = _table_boundary_key(block_source, block_target)
            fine_pairs = (
                align_block(
                    block_source, block_target,
                    lang_ratio=lang_ratio, boundary_key=boundary_key,
                )
                if boundary_key is not None
                else _align_by_cumulative_progress(block_source, block_target)
            )
            for pair in fine_pairs:
                pair.method = "hierarchical_seed"
                pair.features.update({
                    "coarse_alignment": True,
                    "coarse_anchor_method": anchor_method,
                    "coarse_source_blocks": len(coarse_pair.src_indices),
                    "coarse_target_blocks": len(coarse_pair.tgt_indices),
                })
            result.extend(fine_pairs)
    return result, source_ignored, target_ignored


def restore_running_matter_gaps(
    pairs: list[AlignPair], source_ignored: list[AlignUnit], target_ignored: list[AlignUnit],
) -> list[AlignPair]:
    """按两侧原始顺序插回被隔离内容，不让页眉页码进入 LLM 复核窗口。"""
    source_noise = sorted(unit.index for unit in source_ignored)
    target_noise = sorted(unit.index for unit in target_ignored)
    source_cursor = target_cursor = 0
    result: list[AlignPair] = []

    def append_before(source_limit: int, target_limit: int) -> None:
        nonlocal source_cursor, target_cursor
        source_group: list[int] = []
        while source_cursor < len(source_noise) and source_noise[source_cursor] < source_limit:
            source_group.append(source_noise[source_cursor])
            source_cursor += 1
        if source_group:
            result.append(AlignPair(
                source_group, [], 0.2, method="ignored_running_matter",
                features={"gap": True, "ignored_running_matter": True},
            ))
        target_group: list[int] = []
        while target_cursor < len(target_noise) and target_noise[target_cursor] < target_limit:
            target_group.append(target_noise[target_cursor])
            target_cursor += 1
        if target_group:
            result.append(AlignPair(
                [], target_group, 0.2, method="ignored_running_matter",
                features={"gap": True, "ignored_running_matter": True},
            ))

    for pair in pairs:
        source_limit = min(pair.src_indices) if pair.src_indices else 10**18
        target_limit = min(pair.tgt_indices) if pair.tgt_indices else 10**18
        # 单侧缺口不能促使另一侧把文档尾部噪声提前插入。
        if not pair.src_indices:
            source_limit = (max((item for previous in result for item in previous.src_indices), default=-1) + 1)
        if not pair.tgt_indices:
            target_limit = (max((item for previous in result for item in previous.tgt_indices), default=-1) + 1)
        append_before(source_limit, target_limit)
        result.append(pair)
    append_before(10**18, 10**18)
    return result


def repair_adjacent_bilingual_gaps(
    pairs: list[AlignPair], source: list[AlignUnit], target: list[AlignUnit],
) -> list[AlignPair]:
    """把 LLM 在同一邻域拆出的双侧伪缺口重新组成单调 N:M 配对。

    仅含一侧内容的连续区间保持原样，因此真实漏译/增译不会被强行吞并。
    """
    source_map = {unit.index: unit for unit in source}
    target_map = {unit.index: unit for unit in target}
    result: list[AlignPair] = []
    index = 0
    while index < len(pairs):
        pair = pairs[index]
        if pair.src_indices and pair.tgt_indices:
            result.append(pair)
            index += 1
            continue
        end = index
        source_indices: list[int] = []
        target_indices: list[int] = []
        while end < len(pairs):
            candidate = pairs[end]
            if candidate.src_indices and candidate.tgt_indices:
                break
            source_indices.extend(candidate.src_indices)
            target_indices.extend(candidate.tgt_indices)
            end += 1
        if source_indices and target_indices:
            source_units = [source_map[item] for item in source_indices]
            target_units = [target_map[item] for item in target_indices]
            boundary_key = _table_boundary_key(source_units, target_units)
            repaired = (
                align_block(source_units, target_units, boundary_key=boundary_key)
                if boundary_key is not None
                else _align_by_cumulative_progress(source_units, target_units)
            )
            for item in repaired:
                item.method = "hierarchical_gap_repair"
                item.confidence = 0.55
                item.features.update({
                    "adjacent_bilingual_gap_repair": True,
                    "repaired_gap_pairs": end - index,
                })
            result.extend(repaired)
        elif source_indices:
            result.append(AlignPair(
                source_indices, [], 0.25, method="hierarchical_isolated_gap",
                features={
                    "gap": True,
                    "isolated_gap_run": True,
                    "coalesced_gap_pairs": end - index,
                },
            ))
        elif target_indices:
            result.append(AlignPair(
                [], target_indices, 0.25, method="hierarchical_isolated_gap",
                features={
                    "gap": True,
                    "isolated_gap_run": True,
                    "coalesced_gap_pairs": end - index,
                },
            ))
        else:
            result.extend(pairs[index:end])
        index = end
    return result
