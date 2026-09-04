"""SVG 可翻译文本单元识别。

区分真正的 SVG 竖排文本和 Illustrator 用多个单字节点模拟的竖排文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from lxml import etree


SVG_NS = "http://www.w3.org/2000/svg"
NSMAP = {"svg": SVG_NS}


@dataclass(frozen=True)
class SvgTextSlot:
    owner: etree._Element
    slot_kind: str
    text: str
    text_element: etree._Element
    text_index: int


def collect_text_slots(root: etree._Element) -> list[SvgTextSlot]:
    slots: list[SvgTextSlot] = []
    text_elements = root.xpath("//svg:text | //text", namespaces=NSMAP)
    for text_index, text_element in enumerate(text_elements):
        for owner, slot_kind, text in _iter_element_slots(text_element):
            slots.append(
                SvgTextSlot(
                    owner=owner,
                    slot_kind=slot_kind,
                    text=text,
                    text_element=text_element,
                    text_index=text_index,
                )
            )
    return slots


def group_logical_text_units(slots: list[SvgTextSlot]) -> list[list[SvgTextSlot]]:
    """把分散但属于同一标签的 SVG 文本槽位组合成一个翻译单元。"""
    units: list[list[SvgTextSlot]] = []
    index = 0
    while index < len(slots):
        vertical = _vertical_run(slots, index)
        if len(vertical) >= 3:
            units.append(vertical)
            index += len(vertical)
            continue

        horizontal = _horizontal_sibling_run(slots, index)
        if len(horizontal) >= 2:
            units.append(horizontal)
            index += len(horizontal)
            continue

        units.append([slots[index]])
        index += 1
    return units


def is_simulated_vertical_unit(unit: list[SvgTextSlot]) -> bool:
    if len(unit) < 3:
        return False
    points = [element_origin(slot.owner) for slot in unit]
    if any(point is None for point in points):
        return False
    return all(
        abs(points[index][0] - points[index - 1][0]) <= 1.0
        and 4.0 <= points[index][1] - points[index - 1][1] <= 24.0
        for index in range(1, len(points))
    )


def element_origin(element: etree._Element) -> Optional[tuple[float, float]]:
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


def unit_source_text(unit: Iterable[SvgTextSlot]) -> str:
    return "".join(slot.text.strip() for slot in unit)


def _iter_element_slots(element: etree._Element):
    if element.text and element.text.strip():
        yield element, "text", element.text
    for child in element:
        yield from _iter_element_slots(child)
        if child.tail and child.tail.strip():
            yield child, "tail", child.tail


def _vertical_run(slots: list[SvgTextSlot], start: int) -> list[SvgTextSlot]:
    first = slots[start]
    if not _is_single_cjk_text_element(first):
        return []
    parent = first.owner.getparent()
    if parent is None or etree.QName(parent).localname != "g":
        return []

    result = [first]
    previous = element_origin(first.owner)
    for slot in slots[start + 1:]:
        if not _is_single_cjk_text_element(slot) or slot.owner.getparent() is not parent:
            break
        point = element_origin(slot.owner)
        if point is None or previous is None:
            break
        if abs(point[0] - previous[0]) > 1.0 or not 4.0 <= point[1] - previous[1] <= 24.0:
            break
        result.append(slot)
        previous = point
    return result


def _horizontal_sibling_run(slots: list[SvgTextSlot], start: int) -> list[SvgTextSlot]:
    first = slots[start]
    if first.slot_kind != "text" or etree.QName(first.owner).localname != "text":
        return []
    parent = first.owner.getparent()
    if parent is None or etree.QName(parent).localname != "g":
        return []
    text_children = [
        child
        for child in parent
        if isinstance(child.tag, str) and etree.QName(child).localname == "text"
    ]
    if len(text_children) < 2:
        return []

    first_point = element_origin(first.owner)
    if first_point is None:
        return []
    result = [first]
    previous_slot = first
    previous_point = first_point
    for slot in slots[start + 1:]:
        if (
            slot.owner.getparent() is not parent
            or slot.slot_kind != "text"
            or etree.QName(slot.owner).localname != "text"
        ):
            break
        # 只合并真正连续的文本兄弟节点。若中间夹有 rect/path 等图形元素，
        # SVG 会按绘制顺序让这些元素覆盖前面的文本，不能把两侧文本写入首节点。
        if previous_slot.owner.getnext() is not slot.owner:
            break
        point = element_origin(slot.owner)
        if point is None or abs(point[1] - first_point[1]) > 2.0:
            break
        horizontal_gap = point[0] - previous_point[0]
        font_size = _element_font_size(previous_slot.owner)
        max_adjacent_gap = min(
            max(
                _rough_text_units(previous_slot.text) * font_size
                + max(font_size, 6.0),
                16.0,
            ),
            160.0,
        )
        if horizontal_gap <= 0 or horizontal_gap > max_adjacent_gap:
            break
        result.append(slot)
        previous_slot = slot
        previous_point = point
    return result


def _is_single_cjk_text_element(slot: SvgTextSlot) -> bool:
    text = slot.text.strip()
    return (
        slot.slot_kind == "text"
        and etree.QName(slot.owner).localname == "text"
        and len(text) == 1
        and "\u2e80" <= text <= "\u9fff"
    )


def _rough_text_units(text: str) -> float:
    units = 0.0
    for char in text.strip():
        if "\u2e80" <= char <= "\u9fff":
            units += 1.0
        elif char.isspace():
            units += 0.35
        else:
            units += 0.6
    return max(units, 1.0)


def _element_font_size(element: etree._Element) -> float:
    value = element.get("font-size", "")
    style = element.get("style", "")
    match = re.search(r"(?:^|;)\s*font-size\s*:\s*([-+0-9.]+)", style, re.I)
    if match:
        value = match.group(1)
    match = re.search(r"[-+0-9.]+", value)
    return max(float(match.group(0)), 1.0) if match else 10.0
