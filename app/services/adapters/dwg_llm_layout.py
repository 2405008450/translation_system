"""LLM-assisted CAD layout grouping with geometry hard constraints.

GPT-5 Mini decides semantic groups only inside local regions. CAD scope,
closed cells, barriers, and handle uniqueness are validated in code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from app.config import get_settings
from app.services.adapters.text_reconstruction import (
    TextEntity,
    is_independent_legend_label_pair,
)
from app.services.llm_service import request_chat_completion

if TYPE_CHECKING:
    from app.services.adapters.text_reconstruction import BarrierIndex

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You group CAD text fragments into logical translation units.
Use meaning and reading order, but obey these hard rules:
1. Every id must appear exactly once.
2. Never merge fragments from different containers or across CAD barriers.
3. Merge list numbers with their item text when they form one item.
4. In tables, merge fragments in one cell when they form one label, including units.
5. Keep independent labels, titles, symbols, and adjacent columns separate.
Return one JSON object only: {\"groups\":[{\"ids\":[\"id\"],\"relation\":\"short reason\"}]}.
Do not rewrite text and preserve the supplied reading order inside each group."""

_NUMBER_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+)+|\d+(?:-\d+)+|\d+[.)、]|[A-Za-z][.)])\s*$"
)
_TABLE_REFERENCE_PREFIX_RE = re.compile(
    r"^\s*(?:表|table|tabla|tabelle|tableau|tabella|tabela|таблица)\s*$",
    re.IGNORECASE,
)
_TABLE_REFERENCE_NUMBER_RE = re.compile(r"^\s*\d+(?:-\d+)+\s*$")


def _is_table_reference_pair(first: TextEntity, second: TextEntity) -> bool:
    """表号前缀与连字符编号只能彼此成组，不能被正文或表名吸收。"""
    return bool(
        (
            _TABLE_REFERENCE_PREFIX_RE.fullmatch(first.text or "")
            and _TABLE_REFERENCE_NUMBER_RE.fullmatch(second.text or "")
        )
        or (
            _TABLE_REFERENCE_PREFIX_RE.fullmatch(second.text or "")
            and _TABLE_REFERENCE_NUMBER_RE.fullmatch(first.text or "")
        )
    )


@dataclass(frozen=True)
class LayoutRegion:
    key: str
    entities: Tuple[TextEntity, ...]


def _reading_order(entities: Sequence[TextEntity]) -> List[TextEntity]:
    avg_h = sum(e.height for e in entities) / max(len(entities), 1)
    y_tol = max(avg_h * 0.6, 1e-6)
    return sorted(entities, key=lambda e: (-round(e.y / y_tol), e.x))


def _cell_key(entity: TextEntity, barrier_index: Optional["BarrierIndex"]) -> Optional[Tuple]:
    if barrier_index is None:
        return None
    cell = barrier_index.enclosing_cell(
        entity.scope, entity.x, entity.y, entity.height
    )
    if cell is None:
        return None
    return (entity.scope, *(round(value, 4) for value in cell))


def _barrier_between(
    left: TextEntity,
    right: TextEntity,
    barrier_index: Optional["BarrierIndex"],
) -> bool:
    if barrier_index is None:
        return False
    avg_h = max((left.height + right.height) / 2.0, 1e-6)
    if abs(left.y - right.y) <= avg_h * 0.8:
        first, second = (left, right) if left.x <= right.x else (right, left)
        return barrier_index.vertical_between(
            left.scope,
            first.right_edge,
            second.x,
            min(first.y, second.y),
            max(first.y + first.height, second.y + second.height),
        ) is not None
    return barrier_index.horizontal_between(
        left.scope,
        left.y,
        right.y,
        min(left.x, right.x),
        max(left.right_edge, right.right_edge),
    ) is not None


def _compatible_for_region(
    first: TextEntity,
    second: TextEntity,
    barrier_index: Optional["BarrierIndex"],
) -> bool:
    if first.scope != second.scope:
        return False
    if is_independent_legend_label_pair(first, second):
        return False
    if (
        _TABLE_REFERENCE_PREFIX_RE.fullmatch(first.text or "")
        or _TABLE_REFERENCE_PREFIX_RE.fullmatch(second.text or "")
    ) and not _is_table_reference_pair(first, second):
        # “表”是独立表号前缀。即便它与“套管尺寸表：”同排，也不能把两个
        # 不同锚点合成一个翻译单元；否则导出会清空右侧表号并把 8-3 补到正文。
        return False
    if (first.text or "").rstrip().endswith((":", "：")) and (
        second.text or ""
    ).rstrip().endswith((":", "：")):
        return False
    first_root = first.handle.split("#p", 1)[0]
    second_root = second.handle.split("#p", 1)[0]
    if (
        "#p" in first.handle
        and "#p" in second.handle
        and first_root == second_root
    ):
        return False
    if first.insert_handle and second.insert_handle and first.insert_handle != second.insert_handle:
        return False
    first_cell = _cell_key(first, barrier_index)
    second_cell = _cell_key(second, barrier_index)
    if first_cell != second_cell and (first_cell is not None or second_cell is not None):
        return False
    if _barrier_between(first, second, barrier_index):
        return False

    avg_h = max((first.height + second.height) / 2.0, 1e-6)
    same_row = abs(first.y - second.y) <= avg_h * 0.9
    if same_row:
        left, right = (first, second) if first.x <= second.x else (second, first)
        gap = right.x - left.right_edge
        return -avg_h <= gap <= avg_h * 20

    vertical_gap = abs(first.y - second.y)
    x_overlap = min(first.right_edge, second.right_edge) - max(first.x, second.x)
    aligned = abs(first.x - second.x) <= avg_h * 4 or x_overlap > 0
    return aligned and vertical_gap <= avg_h * 3


def _build_regions(
    entities: Sequence[TextEntity],
    barrier_index: Optional["BarrierIndex"],
    min_size: int,
    max_size: int,
) -> List[LayoutRegion]:
    """Build disjoint local regions; no handle can be sent twice."""
    by_scope: Dict[str, List[TextEntity]] = defaultdict(list)
    for entity in entities:
        by_scope[entity.scope].append(entity)

    regions: List[LayoutRegion] = []
    region_index = 0
    for scope, scoped in by_scope.items():
        remaining = {entity.handle: entity for entity in scoped if entity.handle}
        while remaining:
            seed_handle = next(iter(remaining))
            queue = [remaining.pop(seed_handle)]
            component: List[TextEntity] = []
            while queue:
                current = queue.pop()
                component.append(current)
                neighbors = [
                    handle for handle, candidate in remaining.items()
                    if _compatible_for_region(current, candidate, barrier_index)
                ]
                for handle in neighbors:
                    queue.append(remaining.pop(handle))

            ordered = _reading_order(component)
            if len(ordered) < min_size:
                continue
            for start in range(0, len(ordered), max_size):
                chunk = ordered[start:start + max_size]
                if len(chunk) < min_size:
                    continue
                regions.append(LayoutRegion(
                    key=f"region-{region_index}:{scope}",
                    entities=tuple(chunk),
                ))
                region_index += 1
    return regions


def _region_payload(
    region: LayoutRegion,
    barrier_index: Optional["BarrierIndex"],
) -> dict:
    entities = []
    for index, entity in enumerate(region.entities):
        cell = _cell_key(entity, barrier_index)
        entities.append({
            "id": entity.handle,
            "text": (entity.text or "")[:160],
            "order": index,
            "x": round(entity.x, 2),
            "y": round(entity.y, 2),
            "width": round(entity.width, 2),
            "height": round(entity.height, 2),
            "layer": entity.layer,
            "style": entity.style,
            "entity_type": entity.entity_type,
            "insert": entity.insert_handle,
            "container": "cell:" + ":".join(map(str, cell)) if cell else region.key,
            "looks_like_list_number": bool(_NUMBER_RE.match(entity.text or "")),
        })
    return {"region": region.key, "fragments": entities}


def _parse_groups(raw: str, allowed_ids: set[str]) -> Optional[List[List[str]]]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    raw_groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(raw_groups, list):
        return None
    seen: set[str] = set()
    groups: List[List[str]] = []
    for raw_group in raw_groups:
        ids = raw_group.get("ids") if isinstance(raw_group, dict) else None
        if not isinstance(ids, list) or not ids:
            return None
        clean: List[str] = []
        for item in ids:
            if not isinstance(item, str) or item not in allowed_ids or item in seen:
                return None
            seen.add(item)
            clean.append(item)
        groups.append(clean)
    return groups if seen == allowed_ids else None


def _group_is_connected(
    members: Sequence[TextEntity],
    barrier_index: Optional["BarrierIndex"],
) -> bool:
    """LLM 组必须靠组内成员自身连通，不能借未入组的邻近文字桥接。"""
    if len(members) < 2:
        return True
    visited = {0}
    pending = [0]
    while pending:
        current = pending.pop()
        for index, candidate in enumerate(members):
            if index in visited:
                continue
            if _compatible_for_region(
                members[current], candidate, barrier_index
            ):
                visited.add(index)
                pending.append(index)
    return len(visited) == len(members)


def _groups_respect_geometry(
    groups: Sequence[Sequence[str]],
    entities: Sequence[TextEntity],
    barrier_index: Optional["BarrierIndex"],
) -> bool:
    by_id = {entity.handle: entity for entity in entities}
    for group in groups:
        members = [by_id[item] for item in group]
        member_types = {member.entity_type for member in members}
        # INSERT 实例 ATTRIB 使用世界坐标且只属于该实例；块定义 TEXT/ATTDEF
        # 使用局部坐标并由所有引用共享，二者绝不能形成同一重建句段。
        if "ATTRIB" in member_types and any(
            entity_type != "ATTRIB" for entity_type in member_types
        ):
            return False
        if not _group_is_connected(members, barrier_index):
            return False
        colon_labels = [
            member for member in members
            if (member.text or "").rstrip().endswith((":", "："))
        ]
        if len(colon_labels) > 1:
            return False
        table_reference_prefixes = [
            member for member in members
            if _TABLE_REFERENCE_PREFIX_RE.fullmatch(member.text or "")
        ]
        for prefix in table_reference_prefixes:
            # 独立“表/Table”只能和连字符编号（8-3）组成表号。任何正文、
            # 表名或说明文字混入都说明 LLM 跨锚点误合并，必须整组回退。
            if any(
                member is not prefix
                and not _TABLE_REFERENCE_NUMBER_RE.fullmatch(member.text or "")
                for member in members
            ):
                return False
        # 短冒号标签处在独立行时通常是标题或表名，不能与上方正文合并。
        # 例如“套管尺寸表：”若被并入前一段，导出时会随正文一起清空，
        # 最终表现为标题漏翻译。
        for label in colon_labels:
            if len((label.text or "").strip()) > 32:
                continue
            for member in members:
                avg_h = max((label.height + member.height) / 2.0, 1e-6)
                if abs(label.y - member.y) > avg_h * 0.9:
                    return False
        roots = [member.handle.split("#p", 1)[0] for member in members]
        split_roots = [
            root for root, member in zip(roots, members) if "#p" in member.handle
        ]
        if len(split_roots) != len(set(split_roots)):
            return False
        if len({member.scope for member in members}) != 1:
            return False
        insert_ids = {member.insert_handle for member in members}
        if len(insert_ids) > 1:
            return False
        cells = {_cell_key(member, barrier_index) for member in members}
        concrete_cells = {cell for cell in cells if cell is not None}
        if concrete_cells and (len(concrete_cells) != 1 or None in cells):
            return False
        for index, first in enumerate(members):
            for second in members[index + 1:]:
                if is_independent_legend_label_pair(first, second):
                    return False
                if _barrier_between(first, second, barrier_index):
                    return False
    return True


async def _call_llm(
    region: LayoutRegion,
    barrier_index: Optional["BarrierIndex"],
) -> Optional[List[List[str]]]:
    settings = get_settings()
    model = getattr(settings, "dwg_llm_layout_model", "openai/gpt-5.4-mini")
    provider = getattr(settings, "dwg_llm_layout_provider", "openrouter")
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(
            _region_payload(region, barrier_index), ensure_ascii=False
        )},
    ]
    try:
        result = await request_chat_completion(
            messages=messages,
            provider=provider,
            model_override=model,
            response_format={"type": "json_object"},
            temperature=0.0,
            settings=settings,
            allow_fallback=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("CAD LLM 分组失败 region=%s: %s", region.key, exc)
        return None

    allowed = {entity.handle for entity in region.entities}
    groups = _parse_groups(result.content, allowed)
    if groups is None or not _groups_respect_geometry(
        groups, region.entities, barrier_index
    ):
        logger.warning("CAD LLM 分组返回无效 region=%s", region.key)
        return None
    return groups


async def _run_all(
    regions: Sequence[LayoutRegion],
    barrier_index: Optional["BarrierIndex"],
    concurrency: int,
) -> Dict[str, str]:
    semaphore = asyncio.Semaphore(max(concurrency, 1))
    mapping: Dict[str, str] = {}

    async def run_region(index: int, region: LayoutRegion) -> None:
        async with semaphore:
            groups = await _call_llm(region, barrier_index)
        if not groups:
            return
        for group_index, group in enumerate(groups):
            # 单元素组不包含任何 LLM 合并信息。若仍标记为已覆盖，会阻断
            # 几何回退把同一表格单元格中的“标签 + 单位”重新合并。
            if len(group) < 2:
                continue
            group_id = f"layout-{index}-{group_index}"
            for handle in group:
                if handle in mapping:
                    logger.error("CAD LLM handle 重复归属: %s", handle)
                    return
                mapping[handle] = group_id

    await asyncio.gather(*(
        run_region(index, region) for index, region in enumerate(regions)
    ))
    return mapping


def layout_group_entities(
    entities: Sequence[TextEntity],
    barrier_index: Optional["BarrierIndex"] = None,
) -> Dict[str, str]:
    """Return validated semantic groups for disjoint local CAD regions."""
    settings = get_settings()
    if not getattr(settings, "dwg_llm_layout_enabled", False) or not entities:
        return {}

    min_size = max(int(getattr(settings, "dwg_llm_layout_min_bucket", 2)), 2)
    max_size = max(int(getattr(settings, "dwg_llm_layout_max_bucket", 30)), min_size)
    concurrency = max(int(getattr(settings, "dwg_llm_layout_concurrency", 3)), 1)
    regions = _build_regions(entities, barrier_index, min_size, max_size)
    if not regions:
        return {}

    logger.info(
        "CAD LLM 版面分析：%d 个局部区域，%d 个候选实体",
        len(regions),
        sum(len(region.entities) for region in regions),
    )
    try:
        loop = asyncio.new_event_loop()
        try:
            mapping = loop.run_until_complete(
                _run_all(regions, barrier_index, concurrency)
            )
        finally:
            loop.close()
    except RuntimeError:
        logger.warning("CAD LLM 版面分析跳过：当前上下文已有事件循环")
        return {}

    logger.info(
        "CAD LLM 版面分析完成：%d 个实体，%d 个逻辑块",
        len(mapping), len(set(mapping.values())),
    )
    return mapping
