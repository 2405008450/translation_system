from __future__ import annotations

from collections import Counter

from .features import classify_field, comparable_text, rare_numbers, unit_numbering
from .parser import AlignUnit

MAX_BLOCK_UNITS = 40


def _best_monotonic_chain(candidates: list[tuple[int, int, str, int]]) -> list[tuple[int, int, str]]:
    """求最高权重单调锚点链，避免弱锚点抢占后续强锚点。"""
    if not candidates:
        return []
    items = sorted(set(candidates), key=lambda item: (item[0], item[1], -item[3], item[2]))
    scores = [item[3] for item in items]
    previous = [-1] * len(items)
    for index, (src_index, tgt_index, _, weight) in enumerate(items):
        for before in range(index):
            if items[before][0] < src_index and items[before][1] < tgt_index:
                candidate_score = scores[before] + weight
                if candidate_score > scores[index]:
                    scores[index] = candidate_score
                    previous[index] = before
    cursor = max(range(len(items)), key=lambda item: scores[item])
    result: list[tuple[int, int, str]] = []
    while cursor >= 0:
        src_index, tgt_index, method, _ = items[cursor]
        result.append((src_index, tgt_index, method))
        cursor = previous[cursor]
    result.reverse()
    return result


def _candidate_anchors(
    src: list[AlignUnit],
    tgt: list[AlignUnit],
    *,
    include_structure: bool = True,
) -> list[tuple[int, int, str]]:
    candidates: list[tuple[int, int, str, int]] = []

    src_exact = Counter(comparable_text(unit.text) for unit in src if comparable_text(unit.text))
    tgt_exact = Counter(comparable_text(unit.text) for unit in tgt if comparable_text(unit.text))
    exact_keys = {key for key in src_exact if src_exact[key] == tgt_exact[key] == 1 and len(key) >= 4}
    src_exact_map = {comparable_text(unit.text): i for i, unit in enumerate(src) if comparable_text(unit.text) in exact_keys}
    tgt_exact_map = {comparable_text(unit.text): i for i, unit in enumerate(tgt) if comparable_text(unit.text) in exact_keys}
    candidates.extend((src_exact_map[key], tgt_exact_map[key], "anchor_exact", 110) for key in exact_keys)
    src_numbers = Counter(unit_numbering(unit) for unit in src if unit_numbering(unit))
    tgt_numbers = Counter(unit_numbering(unit) for unit in tgt if unit_numbering(unit))
    common_unique = {key for key in src_numbers if src_numbers[key] == tgt_numbers[key] == 1}
    # 编号整体匹配率过低时放弃，防止错乱编号成为硬锚点。
    denominator = max(1, min(sum(src_numbers.values()), sum(tgt_numbers.values())))
    if len(common_unique) / denominator >= 0.3:
        src_map = {unit_numbering(unit): i for i, unit in enumerate(src) if unit_numbering(unit) in common_unique}
        tgt_map = {unit_numbering(unit): i for i, unit in enumerate(tgt) if unit_numbering(unit) in common_unique}
        candidates.extend((src_map[key], tgt_map[key], "anchor_number", 85) for key in common_unique)

    common_rare = rare_numbers(src) & rare_numbers(tgt)
    src_rare = {number: i for i, unit in enumerate(src) for number in unit.numbers if number in common_rare}
    tgt_rare = {number: i for i, unit in enumerate(tgt) for number in unit.numbers if number in common_rare}
    candidates.extend((src_rare[number], tgt_rare[number], "anchor_number_rare", 100) for number in common_rare)

    src_fields: dict[str, list[int]] = {}
    tgt_fields: dict[str, list[int]] = {}
    for index, unit in enumerate(src):
        field_type = classify_field(unit.text)
        if field_type:
            src_fields.setdefault(field_type, []).append(index)
    for index, unit in enumerate(tgt):
        field_type = classify_field(unit.text)
        if field_type:
            tgt_fields.setdefault(field_type, []).append(index)
    for field_type in src_fields.keys() & tgt_fields.keys():
        src_indexes, tgt_indexes = src_fields[field_type], tgt_fields[field_type]
        field_pairs = zip(src_indexes, tgt_indexes) if len(src_indexes) == len(tgt_indexes) else ()
        for si, ti in field_pairs:
            # 同类字段两边都有数字时必须至少共享一个数字，防止重复字段误锚定。
            src_values, tgt_values = set(src[si].numbers), set(tgt[ti].numbers)
            if src_values and tgt_values and not src_values.intersection(tgt_values):
                continue
            candidates.append((si, ti, f"anchor_field_{field_type}", 105))

    if include_structure:
        src_headings = [i for i, unit in enumerate(src) if unit.is_heading]
        tgt_headings = [i for i, unit in enumerate(tgt) if unit.is_heading]
        if src_headings and len(src_headings) == len(tgt_headings):
            candidates.extend((i, j, "anchor_heading", 55) for i, j in zip(src_headings, tgt_headings))

    return _best_monotonic_chain(candidates)


def _table_cell_ranges(units: list[AlignUnit]) -> tuple[int, dict[tuple[int, int, int], slice]]:
    """按“第几个表格 + 行 + 列”聚合连续句子，忽略两份 DOCX 不同的全局 block_index。"""
    table_blocks: list[int] = []
    for unit in units:
        if unit.block_type == "table_cell" and unit.block_index not in table_blocks:
            table_blocks.append(unit.block_index)
    ordinal = {block_index: index for index, block_index in enumerate(table_blocks)}
    ranges: dict[tuple[int, int, int], slice] = {}
    index = 0
    while index < len(units):
        unit = units[index]
        if unit.block_type != "table_cell" or unit.row_index is None or unit.cell_index is None:
            index += 1
            continue
        key = (ordinal[unit.block_index], unit.row_index, unit.cell_index)
        end = index + 1
        while end < len(units):
            candidate = units[end]
            if (
                candidate.block_type != "table_cell"
                or candidate.block_index != unit.block_index
                or candidate.row_index != unit.row_index
                or candidate.cell_index != unit.cell_index
            ):
                break
            end += 1
        ranges[key] = slice(index, end)
        index = end
    return len(table_blocks), ranges


def _table_span(cells: dict[tuple[int, int, int], slice], ordinal: int) -> slice | None:
    ranges = [value for key, value in cells.items() if key[0] == ordinal]
    if not ranges:
        return None
    return slice(min(item.start for item in ranges), max(item.stop for item in ranges))


def _table_topology(cells: dict[tuple[int, int, int], slice], ordinal: int) -> tuple[tuple[int, int], ...]:
    return tuple(sorted((row, cell) for table, row, cell in cells if table == ordinal))


def _table_row_ranges(
    cells: dict[tuple[int, int, int], slice], ordinal: int,
) -> dict[int, slice]:
    rows: dict[int, list[slice]] = {}
    for (table, row, _), cell_range in cells.items():
        if table == ordinal:
            rows.setdefault(row, []).append(cell_range)
    return {
        row: slice(min(item.start for item in ranges), max(item.stop for item in ranges))
        for row, ranges in rows.items()
    }


def _logical_table_row_anchors(
    src: list[AlignUnit], tgt: list[AlignUnit],
    src_rows: dict[int, slice], tgt_rows: dict[int, slice],
) -> list[tuple[int, int, str]]:
    """用唯一数字和完全相同文本寻找逻辑行锚点，不假设两侧物理行号相等。"""
    src_keys, tgt_keys = sorted(src_rows), sorted(tgt_rows)

    def row_numbers(units: list[AlignUnit], row_range: slice) -> set[str]:
        return {number for unit in units[row_range] for number in unit.numbers}

    def row_text(units: list[AlignUnit], row_range: slice) -> str:
        return comparable_text(" ".join(unit.text for unit in units[row_range]))

    src_number_rows: dict[str, list[int]] = {}
    tgt_number_rows: dict[str, list[int]] = {}
    for position, row in enumerate(src_keys):
        for number in row_numbers(src, src_rows[row]):
            src_number_rows.setdefault(number, []).append(position)
    for position, row in enumerate(tgt_keys):
        for number in row_numbers(tgt, tgt_rows[row]):
            tgt_number_rows.setdefault(number, []).append(position)

    weighted: dict[tuple[int, int], tuple[str, int]] = {}
    for number in src_number_rows.keys() & tgt_number_rows.keys():
        source_positions = src_number_rows[number]
        target_positions = tgt_number_rows[number]
        if len(source_positions) == len(target_positions) == 1:
            weighted[(source_positions[0], target_positions[0])] = (
                "anchor_table_row_number", 120,
            )

    src_text_rows: dict[str, list[int]] = {}
    tgt_text_rows: dict[str, list[int]] = {}
    for position, row in enumerate(src_keys):
        text = row_text(src, src_rows[row])
        if len(text) >= 4:
            src_text_rows.setdefault(text, []).append(position)
    for position, row in enumerate(tgt_keys):
        text = row_text(tgt, tgt_rows[row])
        if len(text) >= 4:
            tgt_text_rows.setdefault(text, []).append(position)
    for text in src_text_rows.keys() & tgt_text_rows.keys():
        source_positions = src_text_rows[text]
        target_positions = tgt_text_rows[text]
        if len(source_positions) == len(target_positions) == 1:
            weighted.setdefault(
                (source_positions[0], target_positions[0]),
                ("anchor_table_row_exact", 110),
            )

    candidates = [
        (source, target, method, weight)
        for (source, target), (method, weight) in weighted.items()
    ]
    chain = _best_monotonic_chain(candidates)
    return [(src_keys[source], tgt_keys[target], method) for source, target, method in chain]


def _flexible_table_blocks(
    src: list[AlignUnit], tgt: list[AlignUnit],
    src_cells: dict[tuple[int, int, int], slice],
    tgt_cells: dict[tuple[int, int, int], slice],
    src_ordinal: int, *, tgt_ordinal: int | None = None,
) -> list[tuple[slice, slice, str]]:
    """表格拓扑不一致时，以逻辑行强锚点切开局部窗口并允许行级增删。"""
    target_ordinal = src_ordinal if tgt_ordinal is None else tgt_ordinal
    src_span = _table_span(src_cells, src_ordinal)
    tgt_span = _table_span(tgt_cells, target_ordinal)
    if src_span is None or tgt_span is None:
        return []
    src_rows = _table_row_ranges(src_cells, src_ordinal)
    tgt_rows = _table_row_ranges(tgt_cells, target_ordinal)
    anchors = _logical_table_row_anchors(src, tgt, src_rows, tgt_rows)
    blocks: list[tuple[slice, slice, str]] = []
    si, sj = src_span.start, tgt_span.start
    for source_row, target_row, method in anchors:
        source_range, target_range = src_rows[source_row], tgt_rows[target_row]
        if source_range.start < si or target_range.start < sj:
            continue
        blocks.extend(_point_anchor_blocks(
            src[si:source_range.start], tgt[sj:target_range.start], si, sj,
        ))
        blocks.append((source_range, target_range, method))
        si, sj = source_range.stop, target_range.stop
    blocks.extend(_point_anchor_blocks(
        src[si:src_span.stop], tgt[sj:tgt_span.stop], si, sj,
    ))
    return blocks


def _match_table_ordinals(
    src: list[AlignUnit], tgt: list[AlignUnit],
    src_count: int, tgt_count: int,
    src_cells: dict[tuple[int, int, int], slice],
    tgt_cells: dict[tuple[int, int, int], slice],
) -> list[tuple[int, int, str]]:
    """表格数量不一致时，用跨表唯一证据寻找单调的逻辑表格对应关系。"""
    if src_count == tgt_count:
        return [(ordinal, ordinal, "table_ordinal") for ordinal in range(src_count)]

    def evidence_maps(
        units: list[AlignUnit], count: int,
        cells: dict[tuple[int, int, int], slice],
    ) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
        number_tables: dict[str, list[int]] = {}
        exact_tables: dict[str, list[int]] = {}
        for ordinal in range(count):
            span = _table_span(cells, ordinal)
            if span is None:
                continue
            numbers = {number for unit in units[span] for number in unit.numbers}
            texts = {
                value for unit in units[span]
                if len(value := comparable_text(unit.text)) >= 8
            }
            for number in numbers:
                number_tables.setdefault(number, []).append(ordinal)
            for text in texts:
                exact_tables.setdefault(text, []).append(ordinal)
        return number_tables, exact_tables

    src_numbers, src_exact = evidence_maps(src, src_count, src_cells)
    tgt_numbers, tgt_exact = evidence_maps(tgt, tgt_count, tgt_cells)
    evidence: dict[tuple[int, int], tuple[int, str]] = {}
    for number in src_numbers.keys() & tgt_numbers.keys():
        if len(src_numbers[number]) == len(tgt_numbers[number]) == 1:
            key = (src_numbers[number][0], tgt_numbers[number][0])
            weight, _ = evidence.get(key, (0, "anchor_table_number"))
            evidence[key] = (weight + 20, "anchor_table_number")
    for text in src_exact.keys() & tgt_exact.keys():
        if len(src_exact[text]) == len(tgt_exact[text]) == 1:
            key = (src_exact[text][0], tgt_exact[text][0])
            weight, _ = evidence.get(key, (0, "anchor_table_exact"))
            evidence[key] = (weight + 35, "anchor_table_exact")

    candidates = [
        (source, target, method, min(180, 80 + weight))
        for (source, target), (weight, method) in evidence.items()
        if weight >= 20
    ]
    return _best_monotonic_chain(candidates)


def _point_anchor_blocks(
    src: list[AlignUnit], tgt: list[AlignUnit], src_offset: int, tgt_offset: int,
    *, group_structural_parents: bool = True, include_structure_anchors: bool = True,
) -> list[tuple[slice, slice, str]]:
    def parent_slice(units: list[AlignUnit], index: int) -> slice:
        unit = units[index]
        key = (unit.block_type, unit.block_index, unit.row_index, unit.cell_index)
        start = index
        while start > 0:
            before = units[start - 1]
            if (before.block_type, before.block_index, before.row_index, before.cell_index) != key:
                break
            start -= 1
        end = index + 1
        while end < len(units):
            after = units[end]
            if (after.block_type, after.block_index, after.row_index, after.cell_index) != key:
                break
            end += 1
        return slice(start, end)

    anchors = _candidate_anchors(src, tgt, include_structure=include_structure_anchors)
    blocks: list[tuple[slice, slice, str]] = []
    si = sj = 0
    for ai, aj, kind in anchors:
        src_parent, tgt_parent = parent_slice(src, ai), parent_slice(tgt, aj)
        compatible_parent = group_structural_parents and (
            src[ai].block_type == tgt[aj].block_type
            and src[ai].block_type in {"paragraph", "heading"}
        )
        if compatible_parent:
            ai, aj = src_parent.start, tgt_parent.start
            src_end, tgt_end = src_parent.stop, tgt_parent.stop
        else:
            src_end, tgt_end = ai + 1, aj + 1
        if ai < si or aj < sj:
            continue
        blocks.extend(_split_window(
            src_offset + si, tgt_offset + sj,
            src_offset + ai, tgt_offset + aj, "between_anchors",
        ))
        blocks.append((
            slice(src_offset + ai, src_offset + src_end),
            slice(tgt_offset + aj, tgt_offset + tgt_end),
            f"{kind}_parent" if compatible_parent else kind,
        ))
        si, sj = src_end, tgt_end
    blocks.extend(_split_window(
        src_offset + si, tgt_offset + sj,
        src_offset + len(src), tgt_offset + len(tgt), "tail",
    ))
    return blocks


def build_order_blocks(src: list[AlignUnit], tgt: list[AlignUnit]) -> list[tuple[slice, slice, str]]:
    """仅按全文先后顺序和文本锚点切窗，不把 Word 版式当作对齐边界。

    文档结构仍保存在单元元数据中，供最终导出时恢复版式；自动对齐阶段只使用
    单调文本锚点和长度窗口，避免表格行列、段落块差异把正确译文锁进错误区域。
    """
    return _point_anchor_blocks(
        src,
        tgt,
        0,
        0,
        group_structural_parents=False,
        include_structure_anchors=False,
    )


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
    src_table_count, src_cells = _table_cell_ranges(src)
    tgt_table_count, tgt_cells = _table_cell_ranges(tgt)
    if not src_table_count or not tgt_table_count:
        return _point_anchor_blocks(src, tgt, 0, 0)

    table_matches = _match_table_ordinals(
        src, tgt, src_table_count, tgt_table_count, src_cells, tgt_cells,
    )
    if not table_matches:
        return _point_anchor_blocks(src, tgt, 0, 0)

    blocks: list[tuple[slice, slice, str]] = []
    si = sj = 0
    for src_ordinal, tgt_ordinal, _ in table_matches:
        src_span = _table_span(src_cells, src_ordinal)
        tgt_span = _table_span(tgt_cells, tgt_ordinal)
        if src_span is None or tgt_span is None:
            continue
        blocks.extend(_point_anchor_blocks(
            src[si:src_span.start], tgt[sj:tgt_span.start], si, sj,
        ))
        if _table_topology(src_cells, src_ordinal) == _table_topology(tgt_cells, tgt_ordinal):
            for key in sorted(src_cells):
                if key[0] != src_ordinal:
                    continue
                target_key = (tgt_ordinal, key[1], key[2])
                if target_key not in tgt_cells:
                    continue
                src_slice, tgt_slice = src_cells[key], tgt_cells[target_key]
                blocks.append((src_slice, tgt_slice, "anchor_table_group"))
        else:
            blocks.extend(_flexible_table_blocks(
                src, tgt, src_cells, tgt_cells, src_ordinal,
                tgt_ordinal=tgt_ordinal,
            ))
        si, sj = src_span.stop, tgt_span.stop
    blocks.extend(_point_anchor_blocks(src[si:], tgt[sj:], si, sj))
    return blocks
