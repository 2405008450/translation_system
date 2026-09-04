"""SVG 导出器：按稳定文本槽位将译文写回 SVG。"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

from lxml import etree

from app.services.adapters.exceptions import ExportError
from app.services.adapters.svg_text_units import (
    collect_text_slots,
    element_origin,
    group_logical_text_units,
    is_simulated_vertical_unit,
    unit_source_text,
)


SVG_NS = "http://www.w3.org/2000/svg"
AI_NS = "http://ns.adobe.com/AdobeIllustrator/10.0/"
NSMAP = {"svg": SVG_NS, "i": AI_NS}


class SvgExporter:
    """将翻译结果写回 SVG，并清理会覆盖译文的 Illustrator 私有数据。"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        strip_illustrator_private_data: bool = True,
    ) -> Tuple[bytes, List[dict]]:
        """导出译后 SVG。

        ``translations`` 同时兼容三种键：稳定句段 ID（如 ``seg-000001``）、
        旧接口顺序 ID（如 ``seg_0``）以及原文。稳定句段 ID 优先。
        """
        return self._export(
            original_bytes,
            translations,
            bilingual=False,
            separator=" / ",
            strip_illustrator_private_data=strip_illustrator_private_data,
        )

    def export_bilingual(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        separator: str = " / ",
        *,
        strip_illustrator_private_data: bool = True,
    ) -> Tuple[bytes, List[dict]]:
        """导出原文和译文并列的 SVG。"""
        return self._export(
            original_bytes,
            translations,
            bilingual=True,
            separator=separator,
            strip_illustrator_private_data=strip_illustrator_private_data,
        )

    def _export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        bilingual: bool,
        separator: str,
        strip_illustrator_private_data: bool,
    ) -> Tuple[bytes, List[dict]]:
        root = self._parse(original_bytes)
        warnings: List[dict] = []
        translated_units: list[dict] = []

        logical_units = group_logical_text_units(collect_text_slots(root))
        for unit_index, unit in enumerate(logical_units):
            first = unit[0]
            owner = first.owner
            slot_kind = first.slot_kind
            original_text = unit_source_text(unit)
            match = self._resolve_translation(translations, unit_index, original_text)
            if match is None:
                continue

            segment_id, translated_text = match
            replacement = (
                f"{original_text.strip()}{separator}{translated_text}"
                if bilingual
                else translated_text
            )
            if len(unit) == 1:
                replacement = self._preserve_edge_whitespace(first.text, replacement)

            if slot_kind == "text":
                owner.text = replacement
            else:
                owner.tail = replacement
            for extra_slot in unit[1:]:
                if extra_slot.slot_kind == "text":
                    extra_slot.owner.text = ""
                else:
                    extra_slot.owner.tail = ""

            translated_units.append(
                {
                    "owner": owner,
                    "slot_kind": slot_kind,
                    "original_text": original_text.strip(),
                    "translated_text": translated_text,
                    "replacement": replacement,
                    "unit_index": unit_index,
                    "group_owners": [slot.owner for slot in unit],
                    "simulated_vertical": is_simulated_vertical_unit(unit),
                    "logical_group": len(unit) > 1,
                }
            )

            warning = self._check_length_change(
                segment_id,
                original_text,
                replacement,
            )
            if warning:
                warnings.append(warning)

        layout_warnings = self._adjust_translated_text_layout(root, translated_units)
        warnings.extend(layout_warnings)

        if strip_illustrator_private_data:
            removed = self._strip_illustrator_private_data(root)
            if removed:
                warnings.append(
                    {
                        "code": "illustrator_private_data_removed",
                        "message": (
                            "已从译后副本中移除 Illustrator 私有 PGF 数据，"
                            "避免重新打开时旧文字覆盖译文。"
                        ),
                        "removed_elements": removed,
                    }
                )

        exported_bytes = etree.tostring(
            root,
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=False,
        )
        return exported_bytes, warnings

    def _adjust_translated_text_layout(
        self,
        root: etree._Element,
        translated_units: list[dict],
    ) -> List[dict]:
        """根据邻近矩形和连线对译文进行保守的居中、缩放和竖排整理。"""
        if not translated_units:
            return []

        styles = self._collect_css_rules(root)
        rects = self._collect_rects(root)
        lines = self._collect_horizontal_lines(root)
        document_bounds = self._document_bounds(root)
        rect_text_counts = self._count_text_elements_by_rect(root, rects, styles)
        warnings: List[dict] = []
        logical_member_ids: set[int] = set()

        for item in translated_units:
            if not item.get("logical_group"):
                continue
            owner = item["owner"]
            logical_member_ids.add(id(owner))
            group_owners = item.get("group_owners") or [owner]
            points = [self._element_origin(group_owner) for group_owner in group_owners]
            points = [point for point in points if point is not None]
            if not points:
                continue
            font_size = self._font_size(owner, styles)
            translated_text = str(item["translated_text"])

            if item.get("simulated_vertical"):
                x = sum(point[0] for point in points) / len(points)
                y = sum(point[1] for point in points) / len(points)
                rect = self._find_containing_rect(
                    rects,
                    x,
                    y,
                    max_width=max(font_size * 8.0, 48.0),
                )
                self._place_vertical_text(
                    owner,
                    translated_text,
                    rect,
                    x,
                    y,
                    font_size,
                )
                warnings.append(
                    {
                        "code": "svg_simulated_vertical_text",
                        "segment_id": f"seg-{item['unit_index'] + 1:06d}",
                        "message": "已将逐行单字组成的伪竖排作为一个完整句段导出。",
                    }
                )
            else:
                origin = points[0]
                available_width = self._nearby_line_width(
                    lines,
                    origin[0],
                    origin[1],
                    font_size,
                )
                if available_width is not None:
                    fitted_size = self._fit_font_size(
                        translated_text,
                        font_size,
                        max(available_width - 2.0, 1.0),
                    )
                    self._set_inline_style(owner, "font-size", f"{fitted_size:.2f}px")
                warnings.append(
                    {
                        "code": "svg_logical_text_group",
                        "segment_id": f"seg-{item['unit_index'] + 1:06d}",
                        "message": "已将同一标注的多个文本节点作为一个完整句段导出。",
                    }
                )

        vertical_member_ids: set[int] = set()
        for group in self._find_vertical_character_groups(translated_units):
            first = group[0]
            joined_translation = " ".join(
                str(item["translated_text"]).strip()
                for item in group
                if str(item["translated_text"]).strip()
            )
            if not joined_translation:
                continue

            first_owner = first["owner"]
            first_owner.text = joined_translation
            for item in group[1:]:
                item["owner"].text = ""
            vertical_member_ids.update(id(item["owner"]) for item in group)

            points = [self._element_origin(item["owner"]) for item in group]
            points = [point for point in points if point is not None]
            if points:
                x = sum(point[0] for point in points) / len(points)
                y = sum(point[1] for point in points) / len(points)
                font_size = self._font_size(first_owner, styles)
                rect = self._find_containing_rect(
                    rects,
                    x,
                    y,
                    max_width=max(font_size * 8.0, 48.0),
                )
                self._place_vertical_text(
                    first_owner,
                    joined_translation,
                    rect,
                    x,
                    y,
                    font_size,
                )
                warnings.append(
                    {
                        "code": "svg_vertical_text_merged",
                        "segment_id": f"seg-{first['unit_index'] + 1:06d}",
                        "message": "已合并连续竖排文字并按可用高度重新居中。",
                    }
                )

        for item in translated_units:
            owner = item["owner"]
            if (
                item["slot_kind"] != "text"
                or id(owner) in vertical_member_ids
                or id(owner) in logical_member_ids
            ):
                continue
            if etree.QName(owner).localname != "text":
                continue

            origin = self._element_origin(owner)
            if origin is None:
                continue
            x, y = origin
            font_size = self._font_size(owner, styles)
            translated_text = str(item["translated_text"])

            if self._is_vertical_text(owner, styles):
                rect = self._find_containing_rect(
                    rects,
                    x,
                    y,
                    max_width=max(font_size * 8.0, 48.0),
                )
                self._place_vertical_text(
                    owner,
                    translated_text,
                    rect,
                    x,
                    y,
                    font_size,
                )
                continue

            rect = self._find_containing_rect(
                rects,
                x,
                y,
                max_height=max(font_size * 4.0, 30.0),
            )
            if rect is not None:
                if rect_text_counts.get(rect, 0) > 1:
                    # 同一框体内存在多行文本时保留各自原始基线，避免每一行都被
                    # 移到矩形中心而发生重叠；仅按右边界压缩过长译文。
                    available_width = rect[0] + rect[2] - x - 3.0
                    if available_width > 1.0:
                        fitted_size = self._fit_font_size(
                            translated_text,
                            font_size,
                            available_width,
                        )
                        if fitted_size < font_size - 0.05:
                            self._set_inline_style(
                                owner,
                                "font-size",
                                f"{fitted_size:.2f}px",
                            )
                else:
                    self._place_text_in_rect(
                        owner,
                        translated_text,
                        rect,
                        font_size,
                    )
                continue

            available_width = self._nearby_line_width(lines, x, y, font_size)
            peer_width = self._next_same_baseline_width(
                translated_units,
                item,
                x,
                y,
                font_size,
            )
            if peer_width is not None:
                available_width = (
                    peer_width
                    if available_width is None
                    else min(available_width, peer_width)
                )
            if document_bounds is not None:
                page_width = document_bounds[2] - x - max(font_size, 6.0)
                if page_width > 4.0:
                    available_width = (
                        page_width
                        if available_width is None
                        else min(available_width, page_width)
                    )
            boundary_rect = self._find_containing_rect(rects, x, y)
            if boundary_rect is not None:
                boundary_width = boundary_rect[0] + boundary_rect[2] - x - 3.0
                if boundary_width > 4.0:
                    available_width = (
                        boundary_width
                        if available_width is None
                        else min(available_width, boundary_width)
                    )
            if available_width is not None:
                fitted_size = self._fit_font_size(
                    translated_text,
                    font_size,
                    max(available_width - 2.0, 1.0),
                )
                if fitted_size < font_size - 0.05:
                    self._set_inline_style(owner, "font-size", f"{fitted_size:.2f}px")

        return warnings

    def _parse(self, original_bytes: bytes) -> etree._Element:
        try:
            parser = etree.XMLParser(
                remove_blank_text=False,
                recover=False,
                resolve_entities="internal",
                no_network=True,
            )
            root = etree.fromstring(original_bytes, parser=parser)
        except (etree.XMLSyntaxError, ValueError) as exc:
            raise ExportError(
                filename="<unknown>",
                reason=f"无法解析 SVG 文件: {exc}",
            ) from exc

        if etree.QName(root).localname.lower() != "svg":
            raise ExportError(filename="<unknown>", reason="根元素不是 SVG")
        return root

    def _iter_text_slots(
        self,
        root: etree._Element,
    ) -> Iterable[tuple[etree._Element, str, str]]:
        text_elements = root.xpath("//svg:text | //text", namespaces=NSMAP)
        for text_element in text_elements:
            yield from self._iter_element_text_slots(text_element)

    def _iter_element_text_slots(
        self,
        element: etree._Element,
    ) -> Iterable[tuple[etree._Element, str, str]]:
        if element.text and element.text.strip():
            yield element, "text", element.text
        for child in element:
            yield from self._iter_element_text_slots(child)
            if child.tail and child.tail.strip():
                yield child, "tail", child.tail

    def _resolve_translation(
        self,
        translations: Dict[str, str],
        unit_index: int,
        original_text: str,
    ) -> Optional[tuple[str, str]]:
        stable_id = f"seg-{unit_index + 1:06d}"
        legacy_id = f"seg_{unit_index}"
        source_text = original_text.strip()
        for key in (stable_id, legacy_id, source_text):
            if key not in translations:
                continue
            value = translations[key]
            if value is None:
                continue
            translated_text = str(value)
            if not translated_text:
                continue
            return key, translated_text
        return None

    def _strip_illustrator_private_data(self, root: etree._Element) -> int:
        """移除 Illustrator 的旧 PGF 和指向它的占位节点。"""
        candidates = list(root.xpath("//i:pgf", namespaces=NSMAP))
        candidates.extend(
            root.xpath(
                "//*[local-name()='foreignObject' and "
                "contains(@requiredExtensions, $ai_ns)]",
                ai_ns=AI_NS,
            )
        )

        removed = 0
        seen: set[int] = set()
        for element in candidates:
            marker = id(element)
            if marker in seen:
                continue
            seen.add(marker)
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                removed += 1
        return removed

    @staticmethod
    def _preserve_edge_whitespace(original: str, replacement: str) -> str:
        leading = re.match(r"^\s*", original).group(0)
        trailing = re.search(r"\s*$", original).group(0)
        return f"{leading}{replacement}{trailing}"

    @staticmethod
    def _collect_css_rules(root: etree._Element) -> dict[str, dict[str, str]]:
        rules: dict[str, dict[str, str]] = {}
        for style_element in root.xpath("//*[local-name()='style']"):
            css = style_element.text or ""
            for selector_text, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
                properties: dict[str, str] = {}
                for declaration in declarations.split(";"):
                    if ":" not in declaration:
                        continue
                    key, value = declaration.split(":", 1)
                    properties[key.strip().lower()] = value.strip()
                for selector in selector_text.split(","):
                    selector = selector.strip()
                    if re.fullmatch(r"\.[A-Za-z_][\w-]*", selector):
                        rules[selector[1:]] = properties
        return rules

    @staticmethod
    def _collect_rects(root: etree._Element) -> list[tuple[float, float, float, float]]:
        rects: list[tuple[float, float, float, float]] = []
        for element in root.xpath("//*[local-name()='rect']"):
            try:
                x = float(element.get("x", "0"))
                y = float(element.get("y", "0"))
                width = float(element.get("width", "0"))
                height = float(element.get("height", "0"))
            except ValueError:
                continue
            if width > 0 and height > 0:
                rects.append((x, y, width, height))
        return rects

    @staticmethod
    def _collect_horizontal_lines(root: etree._Element) -> list[tuple[float, float, float]]:
        lines: list[tuple[float, float, float]] = []
        for element in root.xpath("//*[local-name()='line']"):
            try:
                x1 = float(element.get("x1", "0"))
                y1 = float(element.get("y1", "0"))
                x2 = float(element.get("x2", "0"))
                y2 = float(element.get("y2", "0"))
            except ValueError:
                continue
            if abs(y1 - y2) <= 0.5 and abs(x2 - x1) >= 4:
                lines.append((min(x1, x2), max(x1, x2), (y1 + y2) / 2))
        return lines

    def _count_text_elements_by_rect(
        self,
        root: etree._Element,
        rects: list[tuple[float, float, float, float]],
        styles: dict[str, dict[str, str]],
    ) -> dict[tuple[float, float, float, float], int]:
        """统计每个最小容器矩形内的可见文本行数。"""
        counts: dict[tuple[float, float, float, float], int] = {}
        for element in root.xpath("//*[local-name()='text']"):
            if not "".join(element.itertext()).strip():
                continue
            origin = self._element_origin(element)
            if origin is None:
                continue
            font_size = self._font_size(element, styles)
            rect = self._find_containing_rect(
                rects,
                origin[0],
                origin[1],
                max_height=max(font_size * 4.0, 30.0),
            )
            if rect is not None:
                counts[rect] = counts.get(rect, 0) + 1
        return counts

    @staticmethod
    def _document_bounds(root: etree._Element) -> Optional[tuple[float, float, float, float]]:
        viewbox = root.get("viewBox", "")
        values = re.findall(r"[-+0-9.eE]+", viewbox)
        if len(values) == 4:
            try:
                x, y, width, height = (float(value) for value in values)
                if width > 0 and height > 0:
                    return x, y, x + width, y + height
            except ValueError:
                pass
        return None

    @staticmethod
    def _element_origin(element: etree._Element) -> Optional[tuple[float, float]]:
        transform = element.get("transform", "")
        match = re.fullmatch(
            r"\s*matrix\(\s*([-+0-9.eE]+)[, ]+([-+0-9.eE]+)[, ]+"
            r"([-+0-9.eE]+)[, ]+([-+0-9.eE]+)[, ]+"
            r"([-+0-9.eE]+)[, ]+([-+0-9.eE]+)\s*\)\s*",
            transform,
        )
        if match:
            return float(match.group(5)), float(match.group(6))
        try:
            return float(element.get("x", "0")), float(element.get("y", "0"))
        except ValueError:
            return None

    @staticmethod
    def _find_containing_rect(
        rects: list[tuple[float, float, float, float]],
        x: float,
        y: float,
        *,
        max_width: Optional[float] = None,
        max_height: Optional[float] = None,
    ) -> Optional[tuple[float, float, float, float]]:
        candidates = [
            rect
            for rect in rects
            if rect[0] - 1 <= x <= rect[0] + rect[2] + 1
            and rect[1] - 2 <= y <= rect[1] + rect[3] + 2
            and (max_width is None or rect[2] <= max_width)
            and (max_height is None or rect[3] <= max_height)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda rect: rect[2] * rect[3])

    def _font_size(
        self,
        element: etree._Element,
        styles: dict[str, dict[str, str]],
    ) -> float:
        value = element.get("font-size")
        inline = self._parse_inline_style(element.get("style", ""))
        value = inline.get("font-size", value)
        if value is None:
            for class_name in element.get("class", "").split():
                class_value = styles.get(class_name, {}).get("font-size")
                if class_value is not None:
                    value = class_value
        match = re.search(r"[-+0-9.]+", value or "")
        return max(float(match.group(0)), 1.0) if match else 10.0

    def _is_vertical_text(
        self,
        element: etree._Element,
        styles: dict[str, dict[str, str]],
    ) -> bool:
        inline = self._parse_inline_style(element.get("style", ""))
        writing_mode = inline.get("writing-mode") or element.get("writing-mode")
        if not writing_mode:
            for class_name in element.get("class", "").split():
                writing_mode = styles.get(class_name, {}).get("writing-mode") or writing_mode
        return bool(writing_mode and writing_mode.lower() not in {"lr", "horizontal-tb", "initial"})

    @staticmethod
    def _estimate_text_units(text: str) -> float:
        units = 0.0
        for char in text.strip():
            if "\u2e80" <= char <= "\u9fff":
                units += 1.0
            elif char.isspace():
                units += 0.32
            elif char in "ilI.,:;|'`!":
                units += 0.28
            elif char in "mwMW@%&":
                units += 0.85
            elif char.isupper():
                units += 0.64
            else:
                units += 0.53
        return max(units, 0.1)

    def _fit_font_size(self, text: str, current_size: float, available_width: float) -> float:
        required_at_one_px = self._estimate_text_units(text)
        fitted = available_width / required_at_one_px
        return max(min(current_size, fitted), min(4.0, current_size))

    def _place_text_in_rect(
        self,
        element: etree._Element,
        text: str,
        rect: tuple[float, float, float, float],
        font_size: float,
    ) -> None:
        x, y, width, height = rect
        fitted_size = self._fit_font_size(text, font_size, max(width - 6.0, 1.0))
        center_x = x + width / 2
        baseline_y = y + height / 2 + fitted_size * 0.35
        self._set_translation_transform(element, center_x, baseline_y)
        element.set("text-anchor", "middle")
        self._set_inline_style(element, "font-size", f"{fitted_size:.2f}px")

    def _place_vertical_text(
        self,
        element: etree._Element,
        text: str,
        rect: Optional[tuple[float, float, float, float]],
        fallback_x: float,
        fallback_y: float,
        font_size: float,
    ) -> None:
        if rect is not None:
            x, y, width, height = rect
            center_x = x + width / 2
            center_y = y + height / 2
            available_length = max(height - 6.0, 1.0)
        else:
            center_x, center_y = fallback_x, fallback_y
            available_length = max(self._estimate_text_units(text) * font_size, 1.0)
        fitted_size = self._fit_font_size(text, font_size, available_length)
        element.set("transform", f"matrix(0 1 -1 0 {center_x:.4f} {center_y:.4f})")
        element.set("text-anchor", "middle")
        element.set("dominant-baseline", "middle")
        self._set_inline_style(element, "font-size", f"{fitted_size:.2f}px")
        self._set_inline_style(element, "writing-mode", "horizontal-tb")
        self._set_inline_style(element, "glyph-orientation-vertical", "0deg")

    @staticmethod
    def _set_translation_transform(element: etree._Element, x: float, y: float) -> None:
        transform = element.get("transform", "")
        match = re.fullmatch(
            r"\s*matrix\(\s*([-+0-9.eE]+)[, ]+([-+0-9.eE]+)[, ]+"
            r"([-+0-9.eE]+)[, ]+([-+0-9.eE]+)[, ]+"
            r"[-+0-9.eE]+[, ]+[-+0-9.eE]+\s*\)\s*",
            transform,
        )
        if match:
            element.set(
                "transform",
                f"matrix({match.group(1)} {match.group(2)} {match.group(3)} "
                f"{match.group(4)} {x:.4f} {y:.4f})",
            )
        else:
            element.set("x", f"{x:.4f}")
            element.set("y", f"{y:.4f}")

    @staticmethod
    def _nearby_line_width(
        lines: list[tuple[float, float, float]],
        x: float,
        y: float,
        font_size: float,
    ) -> Optional[float]:
        candidates = [
            line
            for line in lines
            if line[0] - 3 <= x <= line[1] + 3
            and -1 <= line[2] - y <= max(font_size * 1.4, 5.0)
        ]
        if not candidates:
            return None
        return max(line[1] - max(x, line[0]) for line in candidates)

    def _next_same_baseline_width(
        self,
        translated_units: list[dict],
        current_item: dict,
        x: float,
        y: float,
        font_size: float,
    ) -> Optional[float]:
        """以同一基线上的下一个独立文本起点限制当前标题宽度。"""
        candidates: list[float] = []
        baseline_tolerance = max(font_size * 0.75, 3.0)
        for item in translated_units:
            if item is current_item or item.get("logical_group"):
                continue
            owner = item.get("owner")
            if owner is None or item.get("slot_kind") != "text":
                continue
            if etree.QName(owner).localname != "text":
                continue
            point = self._element_origin(owner)
            if point is None:
                continue
            other_x, other_y = point
            if other_x > x and abs(other_y - y) <= baseline_tolerance:
                candidates.append(other_x)
        if not candidates:
            return None
        return max(min(candidates) - x - max(font_size, 6.0), 1.0)

    def _find_vertical_character_groups(self, units: list[dict]) -> list[list[dict]]:
        groups: list[list[dict]] = []
        current: list[dict] = []
        previous_point: Optional[tuple[float, float]] = None

        def flush() -> None:
            nonlocal current
            if len(current) >= 3:
                groups.append(current)
            current = []

        for item in units:
            owner = item["owner"]
            original_text = str(item["original_text"])
            point = self._element_origin(owner) if item["slot_kind"] == "text" else None
            eligible = (
                etree.QName(owner).localname == "text"
                and len(original_text) == 1
                and "\u2e80" <= original_text <= "\u9fff"
                and point is not None
            )
            if not eligible:
                flush()
                previous_point = None
                continue

            if previous_point is None:
                current = [item]
            else:
                same_column = abs(point[0] - previous_point[0]) <= 1.0
                vertical_step = 4.0 <= point[1] - previous_point[1] <= 24.0
                if same_column and vertical_step:
                    current.append(item)
                else:
                    flush()
                    current = [item]
            previous_point = point
        flush()
        return groups

    def _find_horizontal_sibling_groups(self, units: list[dict]) -> list[list[dict]]:
        """识别同一 ``g`` 下、基线接近的分段标注，例如“增程”+“CAN”。"""
        groups: list[list[dict]] = []
        by_parent: dict[int, list[dict]] = {}
        for item in units:
            owner = item["owner"]
            if item["slot_kind"] != "text" or etree.QName(owner).localname != "text":
                continue
            parent = owner.getparent()
            if parent is None or etree.QName(parent).localname != "g":
                continue
            text_children = [
                child for child in parent
                if isinstance(child.tag, str) and etree.QName(child).localname == "text"
            ]
            if len(text_children) < 2:
                continue
            by_parent.setdefault(id(parent), []).append(item)

        for items in by_parent.values():
            if len(items) < 2:
                continue
            positioned = []
            for item in items:
                point = self._element_origin(item["owner"])
                if point is not None:
                    positioned.append((point, item))
            positioned.sort(key=lambda entry: entry[0][0])
            if len(positioned) < 2:
                continue
            y_values = [entry[0][1] for entry in positioned]
            if max(y_values) - min(y_values) <= 2.0:
                groups.append([entry[1] for entry in positioned])
        return groups

    @staticmethod
    def _parse_inline_style(style: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for declaration in style.split(";"):
            if ":" not in declaration:
                continue
            key, value = declaration.split(":", 1)
            result[key.strip().lower()] = value.strip()
        return result

    def _set_inline_style(self, element: etree._Element, key: str, value: str) -> None:
        properties = self._parse_inline_style(element.get("style", ""))
        properties[key] = value
        element.set("style", ";".join(f"{name}:{item}" for name, item in properties.items()))

    def _check_length_change(
        self,
        segment_id: str,
        original: str,
        translated: str,
        threshold: float = 0.3,
    ) -> Optional[dict]:
        """译文长度变化较大时返回版式风险提示。"""
        original_len = len(original.strip())
        translated_len = len(translated.strip())
        if original_len == 0:
            return None

        change_ratio = abs(translated_len - original_len) / original_len
        if change_ratio <= threshold:
            return None

        return {
            "segment_id": segment_id,
            "original_length": original_len,
            "translated_length": translated_len,
            "change_ratio": round(change_ratio * 100, 1),
            "message": f"文本长度变化 {round(change_ratio * 100, 1)}%，可能影响布局",
        }
