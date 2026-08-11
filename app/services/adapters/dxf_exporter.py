"""
DXF 导出器 - 在原 DXF 文档上原地替换文本，最大化保留几何/样式/图层结构。

实现策略：
- 读入原始 DXF（ezdxf DOM）
- 遍历所有 layout 与具名 block 的文本类实体，按 source -> target 映射替换
- MTEXT 的格式控制码先剥离匹配，再把译文重新写回（沿用原 dxf.char_height/style）
- 写回时优先尝试与原文件相同的 dxfversion，导出 UTF-8 文本

可选行为（仅 DWG 链路启用，默认关闭以保持 .dxf 用户行为不变）：
- enable_overflow_shrink：译文超长时按视觉宽度比例缩字宽因子 / MTEXT 字高
- handle_extra_entities：回写 MULTILEADER 的 MTEXT、ACAD_TABLE 的单元格

语义重建文本导出（Spatial Merge Export）：
核心思路：做"逻辑重建"而不是"几何合并"
- 当 translations 包含合并文本信息时，启用 MTEXT 重建模式
- 方案A（推荐）：删除原 fragmented text，用"单一 MTEXT block"重新生成
- 在主实体位置创建新的 MTEXT 实体承载完整译文
- 清空所有被合并的原始 TEXT 实体的文本内容，避免重叠
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.services.adapters.dxf_adapter import (
    _mtext_paragraph_heights,
    _TEXT_ENTITY_TYPES,
    _visual_length,
    clean_mtext,
)
from app.services.adapters.text_reconstruction import estimate_text_width


logger = logging.getLogger(__name__)


def _wrap_mtext_to_width(text: str, height: float, width: float) -> str:
    """按实际字宽插入 MTEXT 段落符，避免依赖 CAD 客户端自动换行。"""
    if not text or height <= 0 or width <= 0:
        return text

    wrapped: List[str] = []
    for paragraph in re.split(r"\\P|\r\n?|\n", text):
        normalized = re.sub(r"[ \t]+", " ", paragraph).strip()
        if not normalized:
            wrapped.append("")
            continue

        line = ""
        for char in normalized:
            candidate = line + char
            if not line or estimate_text_width(candidate, height) <= width:
                line = candidate
                continue

            break_at = line.rfind(" ")
            if break_at > 0:
                wrapped.append(line[:break_at].rstrip())
                line = line[break_at + 1 :] + char
            else:
                wrapped.append(line.rstrip())
                line = char.lstrip()
        if line:
            wrapped.append(line.rstrip())

    return r"\P".join(wrapped)


@dataclass(frozen=True)
class DxfExportOptions:
    """DXF 导出可选行为开关。

    所有字段默认值都对应"老行为"，新调用方按需打开即可，不影响 .dxf 普通路径。
    """

    enable_overflow_shrink: bool = False
    """译文超长时缩字宽因子 / MTEXT 字高。"""

    min_width_factor: float = 0.55
    """字宽因子最低值，过低会变形。"""

    shrink_threshold: float = 1.02
    """译文/原文视觉长度比超过该阈值才开始缩。"""

    min_char_height_ratio: float = 0.5
    """字高最低缩到原值的比例，过低会看不清。"""

    handle_extra_entities: bool = False
    """处理 MULTILEADER / ACAD_TABLE 等扩展实体。"""

    fix_shx_font_for_unicode: bool = False
    """为非 ASCII 字符创建支持 Unicode 的文本样式。"""

    unicode_font_name: str = "Arial"
    """用于替换 SHX 字体的 TrueType 字体名称。"""

    enable_spatial_merge_export: bool = False
    """启用空间合并文本的 MTEXT 重建导出模式。"""


@dataclass
class MergedTextExportInfo:
    """合并文本导出信息"""
    source_text: str  # 原始合并后的源文本
    target_text: str  # 翻译后的目标文本
    primary_handle: str  # 主实体 handle
    merged_handles: List[str]  # 所有被合并的实体 handle
    primary_x: float = 0.0
    primary_y: float = 0.0
    primary_height: float = 2.5
    primary_style: str = ""
    primary_color: int = 256
    primary_true_color: Optional[int] = None
    primary_transparency: Optional[int] = None
    group_x: float = 0.0
    group_y_top: float = 0.0
    group_width: float = 0.0
    group_height: float = 0.0
    cad_table_cell: bool = False
    scope: str = ""
    layer: str = "0"


class DxfExporter:
    """DXF 导出器（ezdxf 实现）"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        options: DxfExportOptions | None = None,
        audit_path: str | None = None,
        merged_text_info: List[Dict] | None = None,
        mtext_split_info: List[Dict] | None = None,
    ) -> bytes:
        """导出翻译后的 DXF。
        
        Args:
            original_bytes: 原始 DXF 字节
            translations: 源文本 -> 目标文本的映射
            options: 导出选项
            audit_path: 审计日志输出路径（可选）
            merged_text_info: 空间合并文本信息列表（可选），每项包含：
                - source_text: 原始合并后的源文本
                - target_text: 翻译后的目标文本  
                - primary_handle: 主实体 handle
                - merged_handles: 所有被合并的实体 handle 列表
                - primary_x, primary_y, primary_height: 主实体位置和字高
                - layer: 图层名
        """
        if not original_bytes:
            return original_bytes
        if not translations:
            return original_bytes

        opts = options or DxfExportOptions()

        doc = self._read_doc(original_bytes)
        if doc is None:
            return original_bytes

        # 如果启用 Unicode 字体修复，创建支持 Unicode 的文本样式
        unicode_style_name: Optional[str] = None
        if opts.fix_shx_font_for_unicode:
            unicode_style_name = self._ensure_unicode_style(doc, opts.unicode_font_name)

        # 规范化键，便于宽松匹配（去首尾空白）
        normalized: Dict[str, str] = {}
        no_space_map: Dict[str, str] = {}  # 移除所有空白后的映射
        for src, tgt in translations.items():
            if src is None or tgt is None:
                continue
            normalized[src] = tgt
            stripped = src.strip()
            if stripped and stripped not in normalized:
                normalized[stripped] = tgt
            # 多空白规范化
            multi_space_normalized = re.sub(r"\s+", " ", stripped)
            if multi_space_normalized and multi_space_normalized not in normalized:
                normalized[multi_space_normalized] = tgt
            # 移除所有空白（用于兜底匹配）
            no_space = re.sub(r"\s", "", stripped)
            if no_space and no_space not in no_space_map:
                no_space_map[no_space] = tgt

        # 将无空白映射合并到 normalized（作为最后的兜底）
        for no_space_key, tgt in no_space_map.items():
            if no_space_key not in normalized:
                normalized[no_space_key] = tgt

        # MTEXT 拆段：按原 MTEXT handle 聚合所有拆段的译文；
        # 遍历实体时，遇到该 handle 就清空原 MTEXT 并在原 y 位置创建独立小 MTEXT。
        mtext_split_by_parent: Dict[str, List[Dict]] = {}
        if mtext_split_info:
            for item in mtext_split_info:
                ph = str(item.get("parent_handle") or "")
                if not ph:
                    continue
                mtext_split_by_parent.setdefault(ph, []).append(item)
            for ph, items in mtext_split_by_parent.items():
                items.sort(key=lambda it: -float(it.get("y") or 0))
            logger.info("DXF 导出：MTEXT 拆段回写，共 %d 个父 MTEXT", len(mtext_split_by_parent))

        # 复杂模式导出：优先在原文本区域创建可换行 MTEXT；创建失败时
        # 回退到把译文写入主 TEXT，保证任何情况下都不会丢字。
        merged_handles_to_clear: Set[str] = set()
        merged_primary_translations: Dict[str, Tuple[str, str]] = {}
        merged_export_infos: List[MergedTextExportInfo] = []

        if merged_text_info and opts.enable_spatial_merge_export:
            logger.info("DXF 导出：处理 %d 个合并文本信息", len(merged_text_info))
            for raw_info in merged_text_info:
                handles = raw_info.get("merged_handles", [])
                if isinstance(handles, str):
                    try:
                        parsed_handles = json.loads(handles)
                    except (TypeError, json.JSONDecodeError):
                        parsed_handles = [
                            value.strip()
                            for value in handles.split(",")
                            if value.strip()
                        ]
                    handles = parsed_handles if isinstance(parsed_handles, list) else []
                handles = [str(handle) for handle in handles if str(handle).strip()]
                source_text = raw_info.get("source_text", "")
                target_text = raw_info.get("target_text", "") or self._lookup(source_text, normalized)
                primary_handle = raw_info.get("primary_handle", handles[0] if handles else "")
                if primary_handle and primary_handle in mtext_split_by_parent:
                    logger.info(
                        "跳过闭合框重建：handle=%s 已由 MTEXT 拆段路径处理",
                        primary_handle,
                    )
                    continue
                if not target_text or not primary_handle:
                    logger.warning(
                        "DXF 导出：合并组无译文 handles=%s, source=%s...",
                        handles[:3], source_text[:30] if source_text else "(empty)",
                    )
                    continue
                merged_primary_translations[primary_handle] = (source_text, target_text)
                merged_export_infos.append(MergedTextExportInfo(
                    source_text=source_text,
                    target_text=target_text,
                    primary_handle=primary_handle,
                    merged_handles=list(handles),
                    primary_x=float(raw_info.get("primary_x", 0) or 0),
                    primary_y=float(raw_info.get("primary_y", 0) or 0),
                    primary_height=float(raw_info.get("primary_height", 2.5) or 2.5),
                    primary_style=str(raw_info.get("primary_style", "") or ""),
                    primary_color=int(raw_info.get("primary_color", 256) if raw_info.get("primary_color") is not None else 256),
                    primary_true_color=(
                        int(raw_info["primary_true_color"])
                        if raw_info.get("primary_true_color") is not None else None
                    ),
                    primary_transparency=(
                        int(raw_info["primary_transparency"])
                        if raw_info.get("primary_transparency") is not None else None
                    ),
                    group_x=float(raw_info.get("group_x", 0) or 0),
                    group_y_top=float(raw_info.get("group_y_top", 0) or 0),
                    group_width=float(raw_info.get("group_width", 0) or 0),
                    group_height=float(raw_info.get("group_height", 0) or 0),
                    cad_table_cell=bool(raw_info.get("cad_table_cell", False)),
                    scope=str(raw_info.get("scope", "") or ""),
                    layer=str(raw_info.get("layer", "0") or "0"),
                ))

        # 历史句段或拆分结果可能让同一原实体/同一闭合框出现多次。
        # 每个目标区域只创建一个 MTEXT，文本不同则按段落合并，所有原 handle 统一清空。
        unique_infos: Dict[Tuple, MergedTextExportInfo] = {}
        for info in merged_export_infos:
            if info.cad_table_cell:
                key = (
                    "frame",
                    info.scope,
                    round(info.group_x, 4),
                    round(info.group_y_top, 4),
                    round(info.group_width, 4),
                    round(info.group_height, 4),
                )
            else:
                key = ("handle", info.primary_handle)

            existing = unique_infos.get(key)
            if existing is None:
                unique_infos[key] = info
                continue

            existing.merged_handles = list(dict.fromkeys(
                [*existing.merged_handles, *info.merged_handles]
            ))
            existing_source_parts = re.split(r"\\P|\r\n?|\n", existing.source_text)
            if info.source_text and info.source_text not in existing_source_parts:
                existing.source_text = f"{existing.source_text}\n{info.source_text}".strip()
            existing_target_parts = re.split(r"\\P|\r\n?|\n", existing.target_text)
            if info.target_text and info.target_text not in existing_target_parts:
                existing.target_text = f"{existing.target_text}\\P{info.target_text}".strip(r"\P")

        if len(unique_infos) != len(merged_export_infos):
            logger.info(
                "DXF 重建项去重：%d 项归并为 %d 个文本区域",
                len(merged_export_infos),
                len(unique_infos),
            )
        merged_export_infos = list(unique_infos.values())

        # 解析阶段会过滤“(mm)”等不可翻译单位，旧句段的 merged_handles 因此可能
        # 只包含中文标签。创建译文 MTEXT 前，按已识别的表格单元格内边界查找单位
        # 兄弟实体：把单位补进译文，并把其 handle 纳入清空集合，避免新旧文字叠加。
        wrapped_unit_re = re.compile(r"^[\(\[][^\(\)\[\]]{1,20}[\)\]]$")
        bare_unit_re = re.compile(
            r"^(?:/?(?:mm|cm|dm|m|km|in|inch|ft|h|min|s|ms|l|ml|kg|g|t|"
            r"pa|kpa|mpa|bar|psi)(?:[²³23])?"
            r"(?:[/·](?:mm|cm|m|km|h|min|s|l|kg|g)(?:[²³23])?)?)$",
            re.IGNORECASE,
        )
        partial_unit_re = re.compile(
            r"^[\(\[]?/?(?:mm|cm|dm|m|km|in|inch|ft|h|min|s|ms|l|ml|kg|g|t|"
            r"pa|kpa|mpa|bar|psi)(?:[²³23])?"
            r"(?:[/·](?:mm|cm|m|km|h|min|s|l|kg|g)(?:[²³23])?)?[\)\]]?$",
            re.IGNORECASE,
        )
        list_number_re = re.compile(r"^\d+(?:\.\d+)+[.)、:]?$")
        table_reference_re = re.compile(r"^\d+(?:-\d+)+$")
        punctuation_fragment_re = re.compile(r"^[,，。；;：:]+$")

        def is_unit_fragment(value: str) -> bool:
            cleaned = clean_mtext(value or "").strip()
            if not cleaned or len(cleaned) > 24 or re.search(r"[\u4e00-\u9fff]", cleaned):
                return False
            if wrapped_unit_re.fullmatch(cleaned):
                return bool(re.search(r"[A-Za-z%°²³]", cleaned))
            return bool(
                bare_unit_re.fullmatch(cleaned)
                or partial_unit_re.fullmatch(cleaned)
            )

        # 未变化的纯单位单元格会在坐标辅助函数定义后尝试与同格标签合并；
        # 找不到标签时才跳过独立 MTEXT 重建，保留原单位 TEXT。

        def entity_text(entity) -> str:
            kind = entity.dxftype()
            if kind == "MTEXT":
                return clean_mtext(entity.text or "")
            return str(getattr(entity.dxf, "text", "") or "")

        def entity_anchor_points(entity) -> List[Tuple[float, float]]:
            points: List[Tuple[float, float]] = []
            for attr in ("insert", "align_point"):
                try:
                    point = entity.dxf.get(attr)
                    if point is not None:
                        coordinates = (float(point.x), float(point.y))
                        if coordinates not in points:
                            points.append(coordinates)
                except Exception:  # noqa: BLE001 - 不同 DXF 类型支持的坐标属性不同
                    continue
            return points

        # 旧数据经常只把“(mm)”识别为 cad_table_cell，而左侧中文标签仍是
        # 普通 TEXT。利用单位节点保存的精确单元格框，反向找到同格可翻译标签，
        # 将两者提升为一个 MTEXT 重建项；找不到标签时不重建单位，保留原 TEXT。
        promoted_infos: List[MergedTextExportInfo] = []
        for info in merged_export_infos:
            source_compact = re.sub(r"\s+", "", info.source_text or "").casefold()
            target_compact = re.sub(r"\s+", "", info.target_text or "").casefold()
            is_unchanged_unit_cell = bool(
                info.cad_table_cell
                and is_unit_fragment(info.source_text)
                and source_compact == target_compact
            )
            if not is_unchanged_unit_cell:
                promoted_infos.append(info)
                continue

            old_primary = info.primary_handle
            if info.group_width <= 0 or info.group_height <= 0:
                merged_primary_translations.pop(old_primary, None)
                continue
            try:
                target_space = doc.modelspace()
                if info.scope.startswith("layout:"):
                    layout_name = info.scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif info.scope.startswith("block:"):
                    block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "纯单位单元格标签定位失败 scope=%s: %s", info.scope, exc,
                )
                merged_primary_translations.pop(old_primary, None)
                continue

            left = info.group_x
            right = info.group_x + info.group_width
            top = info.group_y_top
            bottom = info.group_y_top - info.group_height
            tolerance = max(info.primary_height * 0.35, 1e-6)
            pending = list(target_space)
            unit_handles: List[str] = []
            label_candidates: List[Tuple[float, object, Tuple[float, float], str, str]] = []

            while pending:
                entity = pending.pop()
                if entity.dxftype() == "INSERT":
                    pending.extend(list(entity.attribs))
                    continue
                if entity.dxftype() not in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                    continue
                handle = str(getattr(entity.dxf, "handle", "") or "")
                text = entity_text(entity).strip()
                if not handle or not text:
                    continue
                matching_point = next((
                    (x, y) for x, y in entity_anchor_points(entity)
                    if left - tolerance <= x <= right + tolerance
                    and bottom - tolerance <= y <= top + tolerance
                ), None)
                if matching_point is None:
                    continue
                if is_unit_fragment(text):
                    unit_handles.append(handle)
                    continue
                if str(getattr(entity.dxf, "layer", "") or "") != info.layer:
                    continue

                translated = self._lookup(text, normalized)
                if translated is None:
                    translated = self._merge_sentence_translations(text, normalized)
                if not translated or translated == text:
                    continue

                x, y = matching_point
                entity_height = max(float(
                    getattr(entity.dxf, "height", 0)
                    or getattr(entity.dxf, "char_height", 0)
                    or info.primary_height
                ), 1e-6)
                if abs(y - info.primary_y) > max(entity_height, info.primary_height) * 0.9:
                    continue
                if x > info.primary_x + tolerance:
                    continue
                score = abs(y - info.primary_y) * 10.0 + abs(x - info.primary_x)
                label_candidates.append((score, entity, matching_point, text, translated))

            if not label_candidates:
                merged_primary_translations.pop(old_primary, None)
                logger.info(
                    "跳过未变化纯单位的独立 MTEXT 重建 primary=%s text=%r",
                    old_primary,
                    info.source_text,
                )
                continue

            _, label_entity, label_point, label_text, translated_label = min(
                label_candidates, key=lambda item: item[0]
            )
            label_handle = str(getattr(label_entity.dxf, "handle", "") or "")
            unit_text = info.source_text.strip()

            def join_unit_text(value: str, fragment: str) -> str:
                value = value.rstrip()
                if fragment.startswith(("/", ")", "]")) or value.endswith(("(", "[", "/", "·")):
                    return value + fragment
                return f"{value} {fragment}"

            info.source_text = join_unit_text(label_text, unit_text)
            info.target_text = join_unit_text(translated_label, unit_text)
            info.primary_handle = label_handle
            info.merged_handles = list(dict.fromkeys([
                label_handle,
                *unit_handles,
                *info.merged_handles,
            ]))
            info.primary_x, info.primary_y = label_point
            info.primary_height = max(float(
                getattr(label_entity.dxf, "height", 0)
                or getattr(label_entity.dxf, "char_height", 0)
                or info.primary_height
            ), 1e-6)
            info.primary_style = str(getattr(label_entity.dxf, "style", "") or "")
            info.primary_color = int(getattr(label_entity.dxf, "color", 256) or 256)
            raw_true_color = getattr(label_entity.dxf, "true_color", None)
            info.primary_true_color = int(raw_true_color) if raw_true_color is not None else None
            raw_transparency = getattr(label_entity.dxf, "transparency", None)
            info.primary_transparency = (
                int(raw_transparency) if raw_transparency is not None else None
            )
            info.layer = str(getattr(label_entity.dxf, "layer", info.layer) or info.layer)

            merged_primary_translations.pop(old_primary, None)
            merged_primary_translations[label_handle] = (
                info.source_text,
                info.target_text,
            )
            promoted_infos.append(info)
            logger.info(
                "表格标签单位提升为单一 MTEXT label=%s units=%s target=%r",
                label_handle,
                unit_handles,
                info.target_text,
            )

        merged_export_infos = promoted_infos

        for info in merged_export_infos:
            if not (info.group_width > 0 and info.group_height > 0):
                continue

            try:
                target_space = doc.modelspace()
                if info.scope.startswith("layout:"):
                    layout_name = info.scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif info.scope.startswith("block:"):
                    block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "文本框实体重定位失败 scope=%s: %s", info.scope, exc,
                )
                continue

            left = info.group_x
            right = info.group_x + info.group_width
            top = info.group_y_top
            bottom = info.group_y_top - info.group_height
            tolerance = max(info.primary_height * 0.25, 1e-6)
            source_compact = re.sub(r"\s+", "", clean_mtext(info.source_text or ""))
            pending = list(target_space)
            discovered_members: List[Tuple[float, float, str, str]] = []
            discovered_units: List[Tuple[float, str, str]] = []
            discovered_prefixes: List[Tuple[float, float, str, str]] = []
            discovered_references: List[Tuple[float, float, str, str]] = []
            discovered_punctuation: List[Tuple[float, float, str, str]] = []

            while pending:
                entity = pending.pop()
                if entity.dxftype() == "INSERT":
                    pending.extend(list(entity.attribs))
                    continue
                if entity.dxftype() not in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                    continue

                handle = str(getattr(entity.dxf, "handle", "") or "")
                text = entity_text(entity).strip()
                if not handle or not text:
                    continue

                anchors = entity_anchor_points(entity)
                matching_point = next((
                    (x, y) for x, y in anchors
                    if left - tolerance <= x <= right + tolerance
                    and bottom - tolerance <= y <= top + tolerance
                ), None)
                if matching_point is None:
                    continue

                text_compact = re.sub(r"\s+", "", clean_mtext(text))
                source_member = bool(text_compact and text_compact in source_compact)
                unit_member = bool(info.cad_table_cell and is_unit_fragment(text))
                prefix_member = bool(
                    not info.cad_table_cell and list_number_re.fullmatch(text)
                )
                reference_member = bool(
                    not info.cad_table_cell and table_reference_re.fullmatch(text)
                )
                punctuation_member = bool(
                    not info.cad_table_cell and punctuation_fragment_re.fullmatch(text)
                )
                if not (
                    source_member
                    or unit_member
                    or prefix_member
                    or reference_member
                    or punctuation_member
                ):
                    continue

                if handle not in info.merged_handles:
                    info.merged_handles.append(handle)
                matching_x, matching_y = matching_point
                discovered_members.append((matching_x, matching_y, text, handle))
                if unit_member:
                    discovered_units.append((matching_x, text, handle))
                if prefix_member:
                    discovered_prefixes.append((matching_x, matching_y, text, handle))
                if reference_member:
                    discovered_references.append((matching_x, matching_y, text, handle))
                if punctuation_member:
                    discovered_punctuation.append((matching_x, matching_y, text, handle))

            if discovered_prefixes:
                prefixes = list(dict.fromkeys(
                    item[2] for item in sorted(
                        discovered_prefixes, key=lambda item: (-item[1], item[0])
                    )
                ))
                prefix_text = " ".join(prefixes)
                if not info.source_text.lstrip().startswith(prefix_text):
                    info.source_text = f"{prefix_text} {info.source_text}".strip()
                if not info.target_text.lstrip().startswith(prefix_text):
                    info.target_text = f"{prefix_text} {info.target_text}".strip()

            if discovered_references:
                suffix_parts: List[str] = []
                for ref_x, ref_y, ref_text, _ in sorted(
                    discovered_references, key=lambda item: (-item[1], item[0])
                ):
                    suffix = ref_text
                    nearby_punctuation = [
                        item for item in discovered_punctuation
                        if item[0] >= ref_x
                        and abs(item[1] - ref_y) <= max(info.primary_height * 0.8, 1e-6)
                        and item[0] - ref_x <= max(info.primary_height * 4.0, 1e-6)
                    ]
                    if nearby_punctuation:
                        punctuation = min(
                            nearby_punctuation, key=lambda item: item[0] - ref_x
                        )[2]
                        suffix += ":" if punctuation in {"：", ":"} else punctuation
                    suffix_parts.append(suffix)
                suffix_text = " ".join(dict.fromkeys(suffix_parts))
                if suffix_text and suffix_text not in info.source_text:
                    info.source_text = f"{info.source_text} {suffix_text}".strip()
                if suffix_text and suffix_text not in info.target_text:
                    info.target_text = f"{info.target_text} {suffix_text}".strip()

            # 数据库存的是导入时 DXF handle；再次从 DWG 转换时 handle 可能变化。
            # 用框内源文片段的真实 handle 重定位主实体，避免“新 MTEXT 已创建，旧
            # TEXT 又被逐实体翻译”形成截图中的双层重叠。
            discovered_handles = {item[3] for item in discovered_members}
            if discovered_members and info.primary_handle not in discovered_handles:
                old_primary = info.primary_handle
                nearest = min(
                    discovered_members,
                    key=lambda item: (
                        (item[0] - info.primary_x) ** 2
                        + (item[1] - info.primary_y) ** 2
                    ),
                )
                info.primary_handle = nearest[3]
                merged_primary_translations.pop(old_primary, None)
                merged_primary_translations[info.primary_handle] = (
                    info.source_text,
                    info.target_text,
                )
                logger.info(
                    "DXF 合并主实体重定位：%s -> %s text=%r",
                    old_primary,
                    info.primary_handle,
                    nearest[2][:40],
                )

            if discovered_units:
                def contains_fragment(value: str, fragment: str) -> bool:
                    compact_value = re.sub(r"\s+", "", value or "").casefold()
                    compact_fragment = re.sub(r"\s+", "", fragment).casefold()
                    return compact_fragment in compact_value

                def append_fragment(value: str, fragment: str) -> str:
                    value = (value or "").rstrip()
                    if not value:
                        return fragment
                    if fragment.startswith(("/", ")", "]")) or value.endswith(("(", "[", "/", "·")):
                        return value + fragment
                    return f"{value} {fragment}"

                for _, unit_text, _ in sorted(discovered_units, key=lambda item: item[0]):
                    if not contains_fragment(info.source_text, unit_text):
                        info.source_text = append_fragment(info.source_text, unit_text)
                    if not contains_fragment(info.target_text, unit_text):
                        info.target_text = append_fragment(info.target_text, unit_text)

                # MTEXT 创建失败时会回退到主 TEXT 写入，同样必须使用补齐单位后的译文。
                if info.primary_handle in merged_primary_translations:
                    merged_primary_translations[info.primary_handle] = (
                        info.source_text,
                        info.target_text,
                    )

            if info.primary_handle in merged_primary_translations:
                merged_primary_translations[info.primary_handle] = (
                    info.source_text,
                    info.target_text,
                )

            if discovered_members:
                logger.info(
                    "文本框实体重定位 primary=%s members=%s units=%s",
                    info.primary_handle,
                    [item[3] for item in discovered_members],
                    [item[1] for item in discovered_units],
                )

        # 旧任务或未识别出闭合框的表头不会进入 merged_text_info。为这类普通
        # TEXT 增加几何兜底：同一行、同一图层且紧邻的“标签 + 工程单位”只
        # 输出一次，把单位并入标签译文并清空原单位实体。
        direct_unit_translations: Dict[str, Tuple[str, str]] = {}
        spatial_handles = {
            str(handle)
            for info in merged_export_infos
            for handle in info.merged_handles
        }
        used_unit_handles: Set[str] = set()

        def preferred_text_anchor(entity) -> Optional[Tuple[float, float]]:
            try:
                halign = int(getattr(entity.dxf, "halign", 0) or 0)
                valign = int(getattr(entity.dxf, "valign", 0) or 0)
                if halign != 0 or valign != 0:
                    point = getattr(entity.dxf, "align_point", None)
                    if point is not None:
                        return float(point[0]), float(point[1])
                point = getattr(entity.dxf, "insert", None)
                if point is not None:
                    return float(point[0]), float(point[1])
            except Exception:  # noqa: BLE001
                return None
            return None

        def scan_direct_unit_pairs(space) -> None:
            pending = list(space)
            entries: List[Dict[str, object]] = []
            while pending:
                entity = pending.pop()
                if entity.dxftype() == "INSERT":
                    pending.extend(list(entity.attribs))
                    continue
                if entity.dxftype() not in {"TEXT", "ATTRIB", "ATTDEF"}:
                    continue
                handle = str(getattr(entity.dxf, "handle", "") or "")
                text = entity_text(entity).strip()
                anchor = preferred_text_anchor(entity)
                if not handle or not text or anchor is None:
                    continue
                height = max(float(getattr(entity.dxf, "height", 0) or 0), 1e-6)
                width_factor = float(getattr(entity.dxf, "width", 1.0) or 1.0)
                entries.append({
                    "entity": entity,
                    "handle": handle,
                    "text": text,
                    "x": anchor[0],
                    "y": anchor[1],
                    "height": height,
                    "width": estimate_text_width(text, height, 0.6 * width_factor),
                    "layer": str(getattr(entity.dxf, "layer", "") or ""),
                    "rotation": float(getattr(entity.dxf, "rotation", 0) or 0),
                })

            for primary in entries:
                primary_handle = str(primary["handle"])
                if primary_handle in spatial_handles:
                    continue
                source_text = str(primary["text"])
                target_text = self._lookup(source_text, normalized)
                if target_text is None:
                    target_text = self._merge_sentence_translations(source_text, normalized)
                if not target_text or target_text == source_text:
                    continue

                primary_x = float(primary["x"])
                primary_y = float(primary["y"])
                primary_height = float(primary["height"])
                candidates: List[Tuple[float, Dict[str, object]]] = []
                for candidate in entries:
                    candidate_handle = str(candidate["handle"])
                    if (
                        candidate is primary
                        or candidate_handle in spatial_handles
                        or candidate_handle in used_unit_handles
                        or not is_unit_fragment(str(candidate["text"]))
                        or str(candidate["layer"]) != str(primary["layer"])
                    ):
                        continue
                    average_height = max(
                        (primary_height + float(candidate["height"])) / 2.0,
                        1e-6,
                    )
                    if abs(float(candidate["y"]) - primary_y) > average_height * 0.75:
                        continue
                    rotation_delta = abs(float(candidate["rotation"]) - float(primary["rotation"])) % 360
                    rotation_delta = min(rotation_delta, 360 - rotation_delta)
                    if rotation_delta > 2.0:
                        continue
                    x_delta = float(candidate["x"]) - primary_x
                    if x_delta <= average_height * 0.1:
                        continue
                    max_distance = max(
                        float(primary["width"]) + average_height * 2.0,
                        average_height * 8.0,
                    )
                    if x_delta <= max_distance:
                        candidates.append((x_delta, candidate))

                if not candidates:
                    continue
                _, unit = min(candidates, key=lambda item: item[0])
                unit_handle = str(unit["handle"])
                unit_text = str(unit["text"])

                def join_unit(value: str, fragment: str) -> str:
                    value = value.rstrip()
                    if fragment.startswith(("/", ")", "]")) or value.endswith(("(", "[", "/", "·")):
                        return value + fragment
                    return f"{value} {fragment}"

                compact_target = re.sub(r"\s+", "", target_text).casefold()
                compact_unit = re.sub(r"\s+", "", unit_text).casefold()
                combined_target = (
                    target_text
                    if compact_unit in compact_target
                    else join_unit(target_text, unit_text)
                )
                direct_unit_translations[primary_handle] = (
                    join_unit(source_text, unit_text),
                    combined_target,
                )
                used_unit_handles.add(unit_handle)
                merged_handles_to_clear.add(unit_handle)
                logger.info(
                    "普通 TEXT 标签单位合并 primary=%s unit=%s target=%r",
                    primary_handle,
                    unit_handle,
                    combined_target,
                )

        for layout in doc.layouts:
            scan_direct_unit_pairs(layout)
        for block in doc.blocks:
            if block.name.lower().startswith(("*model_space", "*paper_space")):
                continue
            scan_direct_unit_pairs(block)

        # 先创建新实体；只有创建成功的组才清空主实体，失败组沿用旧写回方式。
        recreated_primary_handles = self._create_merged_mtext_entities(
            doc,
            merged_export_infos,
            opts,
            unicode_style_name=unicode_style_name,
        )
        for info in merged_export_infos:
            if info.primary_handle in recreated_primary_handles:
                merged_handles_to_clear.update(info.merged_handles)
                merged_primary_translations.pop(info.primary_handle, None)
            else:
                merged_handles_to_clear.update(
                    handle for handle in info.merged_handles if handle != info.primary_handle
                )

        logger.info(
            "DXF 导出：MTEXT 重建=%d，回退主实体写入=%d，清空实体=%d",
            len(recreated_primary_handles),
            len(merged_primary_translations),
            len(merged_handles_to_clear),
        )

        # 启用 audit 时收集每条实体的命中明细；默认关闭，零开销保留老行为
        audit_records: Optional[list[dict]] = [] if audit_path else None

        seen_handles: set[str] = set()
        stats = {"total": 0, "hit": 0, "miss": 0, "merged_cleared": 0, "merged_written": 0}

        def visit(entities: Iterable) -> None:
            for entity in entities:
                handle = getattr(entity.dxf, "handle", None)
                if handle and handle in seen_handles:
                    continue
                if handle:
                    seen_handles.add(handle)

                # INSERT 的 ATTRIB 不会作为 layout 顶层实体出现。必须递归走同一套
                # 拆段/清空/重建判断，不能交给 _replace_in_entity 直接翻译，否则
                # 新建 MTEXT 与未清空的属性文字会叠在同一单元格里。
                if entity.dxftype() == "INSERT":
                    visit(entity.attribs)
                    continue

                # MTEXT 拆段：旧项目保存的是错误上移后的坐标，先依据原 MTEXT
                # 的 insert、宽度和 \P 索引重建布局，再清空原实体并创建独立段落。
                if handle and handle in mtext_split_by_parent:
                    splits = self._normalize_split_mtext_layout(
                        entity, mtext_split_by_parent[handle]
                    )
                    logger.info(
                        "DXF 导出：拆段 MTEXT handle=%s，拆为 %d 个独立段",
                        handle, len(splits),
                    )
                    self._clear_entity_text(entity, stats, audit_records)
                    self._create_split_mtext_entities(
                        doc,
                        splits,
                        opts,
                        stats,
                        unicode_style_name=unicode_style_name,
                    )
                    continue

                # 检查是否是被合并的实体，如果是则清空文本
                if handle and handle in merged_handles_to_clear:
                    logger.info("DXF 导出：发现需要清空的实体 handle=%s", handle)
                    self._clear_entity_text(entity, stats, audit_records)
                    continue

                # 检查是否是主实体（需要写入合并后的译文）
                if handle and handle in merged_primary_translations:
                    logger.info("DXF 导出：发现主实体 handle=%s，写入合并译文", handle)
                    source_text, target_text = merged_primary_translations[handle]
                    self._write_merged_translation(
                        entity, 
                        source_text,
                        target_text,
                        opts,
                        stats, 
                        audit_records,
                        unicode_style_name=unicode_style_name,
                    )
                    continue

                # 未进入空间重建的旧表头：把标签和单位写入同一个 TEXT，单位实体
                # 已在上面的几何扫描中加入 merged_handles_to_clear。
                if handle and handle in direct_unit_translations:
                    source_text, target_text = direct_unit_translations[handle]
                    self._write_merged_translation(
                        entity,
                        source_text,
                        target_text,
                        opts,
                        stats,
                        audit_records,
                        unicode_style_name=unicode_style_name,
                    )
                    continue

                self._replace_in_entity(
                    entity, normalized, opts, stats, audit_records,
                    unicode_style_name=unicode_style_name,
                )

        for layout in doc.layouts:
            visit(layout)
        for block in doc.blocks:
            name = block.name
            if name.lower().startswith(("*model_space", "*paper_space")):
                continue
            visit(block)

        logger.info(
            "DXF 回写：实体 %d，命中 %d，漏匹配 %d，合并主实体写入 %d，合并清空 %d",
            stats["total"],
            stats["hit"],
            stats["miss"],
            stats.get("merged_written", 0),
            stats.get("merged_cleared", 0),
        )

        if audit_path and audit_records is not None:
            self._dump_export_audit(audit_path, audit_records, normalized, stats)

        return self._write_doc(doc)

    @staticmethod
    def _dump_export_audit(
        path: str,
        records: list[dict],
        translations: Dict[str, str],
        stats: dict,
    ) -> None:
        """把 export 阶段的命中明细写成 JSON，便于离线分析。"""
        import json
        import re as _re

        # 哪些 translations 的 source 一次也没在 DXF 里出现过
        seen_sources = {r["source"] for r in records if r.get("source")}
        unused_translations = [
            {"source": s, "target": t}
            for s, t in translations.items()
            if s not in seen_sources and _re.search(r"[\u4e00-\u9fff]", s or "")
        ]

        # 哪些含中文文本在 DXF 里没找到译文（最关键的"漏译"列表）
        zh_missing = [
            r for r in records
            if r["status"] == "miss" and _re.search(r"[\u4e00-\u9fff]", r.get("source", ""))
        ]

        summary = {
            "entities_total": stats["total"],
            "entities_hit": stats["hit"],
            "entities_miss": stats["miss"],
            "translations_size": len(translations),
            "translations_unused": len(unused_translations),
            "zh_source_missing_translation": len(zh_missing),
        }

        try:
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(
                    {
                        "summary": summary,
                        "zh_missing_translation": zh_missing,
                        "unused_translations": unused_translations,
                        "records": records,
                    },
                    fp,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.info("DXF export 审计写出：%s", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DXF export 审计写出失败 %s: %s", path, exc)

    @staticmethod
    def _read_doc(raw_bytes: bytes):
        import ezdxf
        from ezdxf import recover
        from ezdxf.lldxf.const import DXFError

        for encoding in ("utf-8", "utf-8-sig", "cp1252", "gb18030", "iso-8859-1"):
            try:
                text = raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
            stream = io.BytesIO(text.encode("utf-8"))
            try:
                doc, _ = recover.read(stream)
                return doc
            except DXFError:
                pass
            except Exception:  # noqa: BLE001
                pass
            try:
                return ezdxf.read(io.StringIO(text))
            except DXFError:
                continue
        return None

    @staticmethod
    def _write_doc(doc) -> bytes:
        buffer = io.StringIO()
        doc.write(buffer)
        return buffer.getvalue().encode("utf-8")

    def _clear_entity_text(
        self,
        entity,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
    ) -> None:
        """清空被合并实体的文本内容，防止与主实体译文重叠。"""
        dxftype = entity.dxftype()
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "")
        
        original_text = ""
        try:
            if dxftype == "TEXT":
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = ""
                logger.info("清空 TEXT 实体 [handle=%s]: '%s' -> ''", handle, original_text[:50])
            elif dxftype == "MTEXT":
                original_text = entity.text or ""
                entity.text = ""
                logger.info("清空 MTEXT 实体 [handle=%s]: '%s' -> ''", handle, original_text[:50])
            elif dxftype in {"ATTRIB", "ATTDEF"}:
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = ""
                logger.info("清空 %s 实体 [handle=%s]: '%s' -> ''", dxftype, handle, original_text[:50])
            else:
                logger.warning("无法清空实体：不支持的类型 [%s|%s]", dxftype, handle)
                return
                
            if stats is not None:
                stats["merged_cleared"] = stats.get("merged_cleared", 0) + 1
                
            if audit is not None:
                audit.append({
                    "handle": handle,
                    "entity_type": dxftype,
                    "layer": layer,
                    "source": original_text,
                    "target": "",
                    "status": "merged_cleared",
                    "reason": "part_of_merged_group",
                })
        except Exception as exc:
            logger.warning("清空实体文本失败 [%s|%s]: %s", dxftype, handle, exc)

    def _write_merged_translation(
        self,
        entity,
        source_text: str,
        target_text: str,
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
        *,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        """将合并后的译文写入主实体（第一个实体）。
        
        手动合并导出时，把合并后的译文写到第一个实体，而不是创建新的 MTEXT。
        这样可以保持原始实体的位置、样式等属性。
        同时支持溢出检测，当译文比原文长时自动缩小字体。
        """
        dxftype = entity.dxftype()
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "")
        
        original_text = ""
        try:
            if dxftype == "TEXT":
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = target_text
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, source_text, target_text, opts)
            elif dxftype == "MTEXT":
                original_text = entity.text or ""
                entity.text = target_text
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_mtext_entity(entity, source_text, target_text, opts)
            elif dxftype in {"ATTRIB", "ATTDEF"}:
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = target_text
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, source_text, target_text, opts)
            else:
                logger.warning("无法写入合并译文：不支持的实体类型 [%s|%s]", dxftype, handle)
                return
            
            # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
            if unicode_style_name and self._has_non_ascii(target_text):
                self._apply_unicode_style(entity, unicode_style_name)
                
            if stats is not None:
                stats["merged_written"] = stats.get("merged_written", 0) + 1
                
            if audit is not None:
                audit.append({
                    "handle": handle,
                    "entity_type": dxftype,
                    "layer": layer,
                    "source": original_text,
                    "target": target_text,
                    "status": "merged_written",
                    "reason": "primary_of_merged_group",
                })
                
            logger.debug(
                "写入合并译文 [%s|%s]: %s -> %s", 
                dxftype, handle, original_text[:30], target_text[:30]
            )
        except Exception as exc:
            logger.warning("写入合并译文失败 [%s|%s]: %s", dxftype, handle, exc)

    @staticmethod
    def _normalize_split_mtext_layout(entity, splits: List[Dict]) -> List[Dict]:
        """用原 MTEXT 修正旧版拆段元数据，无需重新导入文档。"""
        normalized = [dict(item) for item in splits]
        if not normalized or all(
            int(item.get("layout_version") or 1) >= 3 for item in normalized
        ):
            return normalized

        parts = [part.strip() for part in clean_mtext(entity.text or "").split("\n")]
        indexed_items: list[tuple[int, Dict]] = []
        for item in normalized:
            indices = item.get("indices") or []
            try:
                index = min(int(value) for value in indices)
            except (TypeError, ValueError):
                continue
            indexed_items.append((index, item))
        if not parts or not indexed_items:
            return normalized

        first_index, first_item = min(indexed_items, key=lambda pair: pair[0])
        local_nominal_height = max(
            float(getattr(entity.dxf, "char_height", 0) or 0),
            1e-6,
        )
        saved_nominal_height = max(
            float(first_item.get("height") or local_nominal_height),
            1e-6,
        )
        height_scale = saved_nominal_height / local_nominal_height
        paragraph_heights = [
            height * height_scale
            for height in _mtext_paragraph_heights(
                entity.text or "", local_nominal_height
            )
        ]
        first_height = (
            paragraph_heights[first_index]
            if first_index < len(paragraph_heights)
            else saved_nominal_height
        )
        # 顶层 MTEXT 直接使用原图实体锚点。旧数据库里的 y 正是本次错位来源，
        # 不能再用它反推。INSERT 内实体保存的是世界坐标，沿用旧公式兜底。
        scope = str(first_item.get("scope") or "")
        if ":insert:" not in scope:
            insert = getattr(entity.dxf, "insert", (0, 0, 0))
            insert_y = float(insert[1])
            attachment = int(getattr(entity.dxf, "attachment_point", 1) or 1)
            if attachment in (1, 2, 3):
                base_y = insert_y - first_height
            elif attachment in (4, 5, 6):
                base_y = insert_y - first_height / 2.0
            else:
                base_y = insert_y
        else:
            base_y = float(first_item.get("y") or 0) - first_height * 1.3 * (
                len(parts) - 1 - first_index
            )
        spacing_factor = float(
            getattr(entity.dxf, "line_spacing_factor", 1.0) or 1.0
        )
        width = float(first_item.get("width") or 0)
        if width <= 0:
            width = float(getattr(entity.dxf, "width", 0) or 0)
        if width <= 0:
            width = first_height * 30

        y_by_index: dict[int, float] = {}
        height_by_index: dict[int, float] = {}
        budget_by_index: dict[int, float] = {}
        cursor_y = base_y
        for index, part in enumerate(parts):
            effective_height = (
                paragraph_heights[index]
                if index < len(paragraph_heights)
                else first_height
            )
            line_height = effective_height * (5.0 / 3.0) * spacing_factor
            if not part:
                cursor_y -= line_height
                continue
            y_by_index[index] = cursor_y
            height_by_index[index] = effective_height
            source_width = estimate_text_width(part, effective_height)
            source_lines = max(1, math.ceil(source_width / max(width, 1e-6)))
            budget = source_lines * line_height
            budget_by_index[index] = budget
            cursor_y -= budget

        for index, item in indexed_items:
            if index not in y_by_index:
                continue
            item["y"] = y_by_index[index]
            item["height"] = height_by_index[index]
            item["width"] = width
            item["y_budget"] = budget_by_index[index]
            item["layout_version"] = 3

        logger.info(
            "DXF 导出：旧版 MTEXT 拆段坐标已迁移 handle=%s base_y=%.2f",
            getattr(entity.dxf, "handle", ""),
            base_y,
        )
        return normalized

    def _create_split_mtext_entities(
        self,
        doc,
        splits: List[Dict],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        """把一组 MTEXT 拆段译文创建为独立 MTEXT，各自锚定在原 y 位置。

        原始 MTEXT 已在调用前清空。这里为每段创建一个小 MTEXT，只受本段
        字数影响，不会跨段拉扯位置。同时用 y_budget 做溢出保护：如果译文
        估算所需高度超过预算（本段到下段的间距），按比例缩字号，避免砸到
        下方图表/表格。
        """
        line_gap = 5.0 / 3.0  # AutoCAD MTEXT 默认基线间距
        for item in splits:
            scope = str(item.get("scope") or "")
            layer = str(item.get("layer") or "0")
            x = float(item.get("x") or 0)
            y = float(item.get("y") or 0)
            height = max(float(item.get("height") or 2.5), 1e-6)
            top_y = y + height
            width = float(item.get("width") or 0)
            target_text = str(item.get("target_text") or "")
            if not target_text:
                continue

            try:
                target_space = doc.modelspace()
                if scope.startswith("layout:"):
                    layout_name = scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif scope.startswith("block:"):
                    block_name = scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)

                if width <= 0:
                    width = height * 30  # 一段的合理默认宽度：约 30 个字宽

                # 溢出保护：算出本段允许的最大高度，比较翻译后估算高度，超了就缩字号。
                budget_raw = item.get("y_budget")
                if budget_raw is not None and opts.enable_overflow_shrink:
                    try:
                        budget = float(budget_raw)
                    except (TypeError, ValueError):
                        budget = 0.0
                    if budget > 0:
                        target_len = max(_visual_length(target_text), 1.0)
                        # 一段的估算行数 = 视觉字数 * 字宽 / 可用宽度
                        est_lines = max(1, math.ceil(target_len * height * 0.6 / max(width, 1e-6)))
                        est_height = est_lines * height * line_gap
                        # 目标：est_height ≤ budget * 0.9（留 10% 安全边）
                        allow_height = budget * 0.9
                        if est_height > allow_height:
                            shrink = allow_height / est_height
                            min_ratio = max(opts.min_char_height_ratio, 0.35)
                            shrink = max(shrink, min_ratio)
                            new_height = height * shrink
                            logger.info(
                                "MTEXT 拆段字号缩放：y=%.0f budget=%.0f est=%.0f "
                                "shrink=%.2f -> h=%.2f",
                                y, budget, est_height, shrink, new_height,
                            )
                            height = max(new_height, 1e-6)

                dxfattribs = {
                    "insert": (x, top_y, 0),  # 缩字号后仍保持原段顶部不动
                    "char_height": round(height, 4),
                    "layer": layer,
                    "attachment_point": 1,
                    "width": round(width, 4),
                    "color": int(item.get("color") or 256),
                }
                tc = item.get("true_color")
                if tc is not None:
                    dxfattribs["true_color"] = int(tc)
                tr = item.get("transparency")
                if tr is not None:
                    dxfattribs["transparency"] = int(tr)

                mtext = target_space.add_mtext(target_text, dxfattribs=dxfattribs)
                style_name = str(item.get("style") or "")
                if style_name:
                    try:
                        mtext.dxf.style = style_name
                    except Exception:  # noqa: BLE001
                        pass
                if unicode_style_name and self._has_non_ascii(target_text):
                    self._apply_unicode_style(mtext, unicode_style_name)

                if stats is not None:
                    stats["mtext_split_created"] = stats.get("mtext_split_created", 0) + 1
                logger.debug(
                    "MTEXT 拆段创建 y=%.2f x=%.2f layer=%s text=%r",
                    y, x, layer, target_text[:50],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("MTEXT 拆段创建失败：%s", exc)

    def _create_merged_mtext_entities(
        self,
        doc,
        merged_infos: List[MergedTextExportInfo],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        unicode_style_name: Optional[str] = None,
    ) -> Set[str]:
        """在原文本边界内创建可换行 MTEXT，返回创建成功的主实体 handle。"""
        created: Set[str] = set()
        line_gap = 5.0 / 3.0

        for info in merged_infos:
            try:
                original_height = max(float(info.primary_height), 1e-6)
                width_ratio = (float(info.group_width) or 0) / original_height
                height_ratio = (float(info.group_height) or 0) / original_height
                has_frame_bounds = (
                    info.cad_table_cell
                    and info.group_width > 0
                    and info.group_height > 0
                )
                # 闭合框可能本来就是宽标题栏/说明面板，不能按字高比例判异常。
                # 只有没有可靠外框的普通合并组才保留几何异常保护。
                if not has_frame_bounds and (width_ratio > 60 or height_ratio > 8):
                    logger.warning(
                        "跳过 MTEXT 重建(几何异常) primary=%s width/h=%.1f height/h=%.1f "
                        "count=%d text=%r",
                        info.primary_handle,
                        width_ratio,
                        height_ratio,
                        len(info.merged_handles),
                        info.target_text[:50],
                    )
                    if stats is not None:
                        stats["mtext_skipped_geometry"] = stats.get("mtext_skipped_geometry", 0) + 1
                    continue

                target_space = doc.modelspace()
                if info.scope.startswith("layout:"):
                    layout_name = info.scope.removeprefix("layout:").split(":insert:", 1)[0]
                    target_space = doc.layouts.get(layout_name)
                elif info.scope.startswith("block:"):
                    block_name = info.scope.removeprefix("block:").split(":insert:", 1)[0]
                    target_space = doc.blocks.get(block_name)

                source_width = estimate_text_width(info.source_text, original_height)
                box_width = float(info.group_width) if info.group_width > 0 else source_width
                available_height = (
                    float(info.group_height) if info.group_height > 0 else original_height
                )
                box_width = max(box_width, 1e-6)
                available_height = max(available_height, 1e-6)
                char_height = original_height
                safe_width = box_width * 0.98

                # 先按原字号显式换行，再根据换行后的实际行数检查纵向空间；
                # 只有整个框仍放不下时才缩字号。
                def wrap_for_height(height: float) -> str:
                    return _wrap_mtext_to_width(
                        info.target_text,
                        height,
                        safe_width,
                    )

                def required_height(height: float) -> float:
                    wrapped = wrap_for_height(height)
                    line_count = max(1, wrapped.count(r"\P") + 1)
                    return line_count * height * line_gap

                height_budget = available_height * 0.98
                if opts.enable_overflow_shrink and required_height(char_height) > height_budget:
                    low, high = 0.0, char_height
                    for _ in range(32):
                        middle = (low + high) / 2.0
                        if required_height(middle) <= height_budget:
                            low = middle
                        else:
                            high = middle
                    char_height = max(low * 0.995, 1e-6)
                    logger.info(
                        "MTEXT 换行后缩放 primary=%s box=%.2fx%.2f h=%.4f->%.4f",
                        info.primary_handle,
                        box_width,
                        available_height,
                        original_height,
                        char_height,
                    )

                if info.cad_table_cell and info.group_width > 0 and info.group_height > 0:
                    # 表格单元格使用中心锚点，不继承原 TEXT 的宽度因子或对齐点。
                    # 显式框宽负责自动换行，纵向字号已由 required_height 约束。
                    insert_x = info.group_x + info.group_width / 2.0
                    insert_y = info.group_y_top - info.group_height / 2.0
                    attachment_point = 5
                else:
                    insert_x = info.group_x if info.group_width > 0 else info.primary_x
                    insert_y = info.group_y_top if info.group_height > 0 else info.primary_y
                    attachment_point = 1
                dxfattribs = {
                    "insert": (insert_x, insert_y, 0),
                    "char_height": char_height,
                    "layer": info.layer,
                    "attachment_point": attachment_point,
                    "width": box_width,
                    "line_spacing_factor": 1.0,
                    "line_spacing_style": 2,
                    "color": info.primary_color,
                }
                if info.primary_true_color is not None:
                    dxfattribs["true_color"] = info.primary_true_color
                if info.primary_transparency is not None:
                    dxfattribs["transparency"] = info.primary_transparency
                wrapped_text = wrap_for_height(char_height)
                mtext = target_space.add_mtext(wrapped_text, dxfattribs=dxfattribs)
                if info.primary_style:
                    try:
                        mtext.dxf.style = info.primary_style
                    except Exception:  # noqa: BLE001
                        pass
                if unicode_style_name and self._has_non_ascii(info.target_text):
                    self._apply_unicode_style(mtext, unicode_style_name)

                created.add(info.primary_handle)
                if stats is not None:
                    stats["mtext_created"] = stats.get("mtext_created", 0) + 1
                logger.info(
                    "创建合并 MTEXT [primary=%s, layer=%s, box=%.2fx%.2f, height=%.4f]: %s",
                    info.primary_handle,
                    info.layer,
                    box_width,
                    available_height,
                    char_height,
                    info.target_text[:50],
                )
            except Exception as exc:
                logger.error("创建合并 MTEXT 失败 [%s]: %s", info.primary_handle, exc)

        return created



    def _replace_in_entity(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
        *,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        dxftype = entity.dxftype()

        if dxftype == "INSERT":
            for attrib in entity.attribs:
                self._replace_in_entity(
                    attrib, translations, opts, stats, audit,
                    unicode_style_name=unicode_style_name,
                )
            return

        def _record(hit: bool, original: str, *, replaced: str = "", reason: str = "") -> None:
            if stats is not None:
                stats["total"] += 1
                if hit:
                    stats["hit"] += 1
                else:
                    stats["miss"] += 1
                    if original.strip():
                        snippet = original.strip().replace("\n", " ")[:60]
                        logger.debug("DXF 漏匹配 [%s]: %s", dxftype, snippet)
            if audit is not None:
                audit.append(
                    {
                        "handle": getattr(entity.dxf, "handle", ""),
                        "entity_type": dxftype,
                        "layer": getattr(entity.dxf, "layer", ""),
                        "source": original,
                        "target": replaced,
                        "status": "hit" if hit else "miss",
                        "reason": reason,
                    }
                )

        if dxftype == "TEXT":
            current = getattr(entity.dxf, "text", "") or ""
            if not current.strip():
                return
            new_value = self._lookup(current, translations)
            # 整段找不到时，尝试按句子拆分后逐句翻译再拼接
            if new_value is None:
                new_value = self._merge_sentence_translations(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, current, new_value, opts)
                # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
                if unicode_style_name and self._has_non_ascii(new_value):
                    self._apply_unicode_style(entity, unicode_style_name)
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if dxftype == "MTEXT":
            raw = entity.text or ""
            cleaned = clean_mtext(raw)
            if not cleaned.strip():
                return
            # 优先按整段查 translations；找不到再按段独立替换（多段 MTEXT 漏译兜底）
            new_value = self._lookup(cleaned, translations)
            if new_value is None:
                new_value = self._lookup(raw, translations)
            if new_value is None:
                new_value = self._merge_mtext_paragraph_translations(cleaned, translations)
            if new_value is None:
                _record(False, cleaned, reason="not_in_translations")
                return
            # MTEXT.text 的 setter 会按 char_height 重新分行，不需要手动处理控制码
            entity.text = new_value
            if opts.enable_overflow_shrink:
                self._shrink_mtext_entity(entity, cleaned, new_value, opts)
            # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
            if unicode_style_name and self._has_non_ascii(new_value):
                self._apply_unicode_style(entity, unicode_style_name)
            _record(True, cleaned, replaced=new_value)
            return

        if dxftype in {"ATTRIB", "ATTDEF"}:
            current = getattr(entity.dxf, "text", "") or ""
            if not current.strip():
                return
            new_value = self._lookup(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, current, new_value, opts)
                # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
                if unicode_style_name and self._has_non_ascii(new_value):
                    self._apply_unicode_style(entity, unicode_style_name)
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if dxftype == "DIMENSION":
            current = getattr(entity.dxf, "text", "") or ""
            if not current or current.strip() in {"", "<>"}:
                return
            new_value = self._lookup(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if opts.handle_extra_entities:
            if dxftype == "MULTILEADER":
                self._replace_multileader(entity, translations, opts)
                return
            if dxftype == "ACAD_TABLE":
                self._replace_acad_table(entity, translations, opts)
                return

        # 其它 _TEXT_ENTITY_TYPES 中的类型在不开 handle_extra_entities 时跳过
        if dxftype not in _TEXT_ENTITY_TYPES:
            return

    # ---------------------------------------------------------------------
    # 缩放 / 溢出处理
    # ---------------------------------------------------------------------

    @staticmethod
    def _shrink_text_entity(
        entity,
        original: str,
        translated: str,
        opts: DxfExportOptions,
    ) -> None:
        """单行 TEXT/ATTRIB 只按比例缩字高，保留原字宽因子。

        SHX 字体把 width factor 压到 0.55 左右时，字形会像截图一样互相覆盖。
        使用 CJK/ASCII 视觉宽度比直接缩字高，可保持字形完整，同时让译文占用
        与原文近似的水平空间。
        """
        try:
            orig_len = _visual_length(original)
            new_len = _visual_length(translated)
            if orig_len <= 0 or new_len <= 0:
                return

            ratio = new_len / orig_len
            if ratio <= opts.shrink_threshold:
                return

            current_height = float(getattr(entity.dxf, "height", 0) or 0)
            if current_height <= 0:
                return

            # 预留少量右侧安全间距。下限允许低于全局默认 0.5，避免超长英文
            # 在表格碎片中继续压到相邻实体；空间重建路径仍会优先使用 MTEXT。
            minimum_ratio = min(max(opts.min_char_height_ratio, 0.05), 0.25)
            height_ratio = max(minimum_ratio, 0.96 / ratio)
            if height_ratio < 1.0:
                entity.dxf.height = round(current_height * height_ratio, 4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shrink TEXT 失败: %s", exc)

    @staticmethod
    def _shrink_mtext_entity(
        entity,
        original: str,
        translated: str,
        opts: DxfExportOptions,
    ) -> None:
        """MTEXT：给定 box 宽度让 ezdxf 自动换行，必要时缩字宽因子和字高。"""
        try:
            orig_len = _visual_length(original)
            new_len = _visual_length(translated)
            if orig_len <= 0 or new_len <= 0:
                return
            
            # 计算字符数比例（不考虑视觉宽度，纯字符数）
            char_ratio = len(translated) / max(len(original), 1)
            # 取视觉比例和字符比例中较大的
            ratio = max(new_len / orig_len, char_ratio)
            
            if ratio <= opts.shrink_threshold:
                return

            char_height = float(getattr(entity.dxf, "char_height", 0) or 0)
            current_box_width = float(getattr(entity.dxf, "width", 0) or 0)

            # 没有 box 宽度时给一个估算值，按原文视觉宽度 × 0.6 × 字高
            if char_height > 0 and current_box_width <= 0:
                estimated = orig_len * char_height * 0.6
                if estimated > 0:
                    entity.dxf.width = round(estimated, 4)

            # 译文长就直接按比例缩字高（曲线更陡，溢出越多缩越多）
            if char_height > 0 and ratio > 1.05:
                shrink = max(opts.min_char_height_ratio, 1.0 / ratio)
                if shrink < 1.0:
                    entity.dxf.char_height = round(char_height * shrink, 4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shrink MTEXT 失败: %s", exc)

    # ---------------------------------------------------------------------
    # 扩展实体
    # ---------------------------------------------------------------------

    def _replace_multileader(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
    ) -> None:
        """MULTILEADER (MLEADER)：替换 context.mtext 里的文本。"""
        try:
            mtext = getattr(getattr(entity, "context", None), "mtext", None)
            current_attr: Optional[str] = None
            current_value = ""
            if mtext is not None:
                for attr in ("default_content", "text"):
                    value = getattr(mtext, attr, None)
                    if value:
                        current_attr = attr
                        current_value = str(value)
                        break

            if not current_value:
                # 兜底：dxf.default_content
                current_value = getattr(entity.dxf, "default_content", "") or ""

            if not current_value:
                return

            cleaned = clean_mtext(current_value)
            new_value = self._lookup(cleaned, translations) or self._lookup(current_value, translations)
            if not new_value or new_value == current_value:
                return

            if mtext is not None and current_attr is not None:
                setattr(mtext, current_attr, new_value)
            try:
                entity.dxf.default_content = new_value
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("替换 MULTILEADER 失败: %s", exc)

    def _replace_acad_table(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
    ) -> None:
        """ACAD_TABLE：逐单元格替换。ezdxf 对 ACAD_TABLE 的写支持较弱，失败就跳过。"""
        n_rows = getattr(entity.dxf, "n_rows", 0) or 0
        n_cols = getattr(entity.dxf, "n_cols", 0) or 0
        for row in range(n_rows):
            for col in range(n_cols):
                try:
                    raw = entity.get_text(row, col) or ""
                except Exception:  # noqa: BLE001
                    continue
                if not raw:
                    continue
                cleaned = clean_mtext(raw)
                new_value = self._lookup(cleaned, translations) or self._lookup(raw, translations)
                if not new_value or new_value == raw:
                    continue
                try:
                    entity.set_text(row, col, new_value)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("替换 ACAD_TABLE 单元格失败 (%d,%d): %s", row, col, exc)

    def _merge_mtext_paragraph_translations(
            self,
            cleaned: str,
            translations: Dict[str, str],
        ) -> Optional[str]:
            """按段落、句子及跨段组合回写 MTEXT 译文。"""
            parts = cleaned.split("\n")
            translated_parts: list[str] = []
            any_hit = False
            index = 0

            while index < len(parts):
                part = parts[index]
                stripped = part.strip()
                if not stripped:
                    translated_parts.append(part)
                    index += 1
                    continue

                replacement = self._lookup(stripped, translations)
                if replacement is None:
                    replacement = self._merge_sentence_translations(stripped, translations)

                consumed = 1
                if replacement is None:
                    # 一个数据库句段可能跨多个 MTEXT \P 段。优先匹配最长连续范围，
                    # 例如标题段加后续多条说明共同组成一个翻译单元。
                    for end in range(len(parts), index + 1, -1):
                        candidate = "\n".join(parts[index:end]).strip()
                        replacement = self._lookup(candidate, translations)
                        if replacement is not None:
                            consumed = end - index
                            break

                if replacement is None:
                    translated_parts.append(part)
                else:
                    translated_parts.append(replacement)
                    any_hit = True
                index += consumed

            if not any_hit:
                return None
            return "\\P".join(translated_parts)


    def _merge_sentence_translations(
        self,
        text: str,
        translations: Dict[str, str],
    ) -> Optional[str]:
        """按句子拆分后逐句翻译再拼接。

        用于 TEXT 实体整段找不到翻译时的兜底处理。
        segment_extractor 会按句号分割，导致原始长文本被拆成多个句子，
        但 DXF 实体中仍是整段，导出时需要按句子逐一查找翻译再拼接。
        """
        if not text or not text.strip():
            return None
        
        # 中文和英文句子结束标点
        sentence_endings = "。？！!?."
        
        # 按句子结束标点拆分，但保留标点
        sentences: list[str] = []
        current = ""
        for i, char in enumerate(text):
            current += char
            if char in sentence_endings:
                # 英文句号后面需要有空格才算句子结束，或者是文本结尾
                if char == ".":
                    next_idx = i + 1
                    if next_idx < len(text) and text[next_idx] not in " \t\n":
                        # 句号后面紧跟非空白字符，不分句
                        continue
                sentences.append(current)
                current = ""
        if current.strip():
            sentences.append(current)
        
        if len(sentences) <= 1:
            # 只有一个句子，无需拆分
            return None
        
        translated_parts: list[str] = []
        any_hit = False
        
        for sentence in sentences:
            stripped = sentence.strip()
            if not stripped:
                translated_parts.append(sentence)
                continue
            
            replacement = self._lookup(stripped, translations)
            if replacement is None:
                # 尝试不带结尾标点的匹配
                without_ending = stripped.rstrip(sentence_endings)
                if without_ending and without_ending != stripped:
                    replacement = self._lookup(without_ending, translations)
                    if replacement:
                        # 补回标点
                        ending = stripped[len(without_ending):]
                        replacement = replacement + ending
            
            if replacement is None:
                translated_parts.append(sentence)
            else:
                translated_parts.append(replacement)
                any_hit = True
        
        if not any_hit:
            return None
        
        return "".join(translated_parts)

    @staticmethod
    def _lookup(value: str, translations: Dict[str, str]) -> Optional[str]:
        if value is None:
            return None
        if value in translations:
            return translations[value]
        stripped = value.strip()
        if stripped and stripped in translations:
            return translations[stripped]
        # 段内多空白的兜底匹配
        normalized = re.sub(r"\s+", " ", stripped)
        if normalized and normalized in translations:
            return translations[normalized]
        # 移除所有空白后的兜底匹配（处理 ODA 转换可能产生的空白差异）
        no_space = re.sub(r"\s", "", stripped)
        if no_space and no_space in translations:
            return translations[no_space]
        return None

    @staticmethod
    def _has_non_ascii(text: str) -> bool:
        """检查文本是否包含非 ASCII 字符（如西班牙语重音字母）。"""
        if not text:
            return False
        for ch in text:
            if ord(ch) > 127:
                return True
        return False

    @staticmethod
    def _ensure_unicode_style(doc, font_name: str) -> str:
        """确保文档中存在支持 Unicode 的文本样式，返回样式名称。"""
        style_name = "_UNICODE_EXPORT"
        try:
            # 检查样式是否已存在
            if style_name in doc.styles:
                return style_name
            # 创建新的文本样式，使用 TrueType 字体
            style = doc.styles.new(style_name)
            style.dxf.font = font_name
            # 不设置 bigfont，让 CAD 软件自动处理
            style.dxf.bigfont = ""
            logger.debug("创建 Unicode 文本样式: %s, 字体: %s", style_name, font_name)
            return style_name
        except Exception as exc:
            logger.warning("创建 Unicode 文本样式失败: %s", exc)
            return ""

    @staticmethod
    def _apply_unicode_style(entity, style_name: str) -> None:
        """将实体的文本样式切换为支持 Unicode 的样式。"""
        if not style_name:
            return
        try:
            entity.dxf.style = style_name
        except Exception as exc:
            logger.debug("应用 Unicode 样式失败: %s", exc)
