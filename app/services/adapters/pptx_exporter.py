from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO
import math
from typing import Any, Iterable, Mapping
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.services.adapters.models import BlockNode, DocumentAST
from app.services.adapters.pptx_adapter import A_NS, CORE_NS, DC_NS, DCTERMS_NS, EXTENDED_PROPS_NS, NS, P_NS, R_NS, PptxAdapter


PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"
PPTX_AUTOFIT_MIN_SCALE = 0.78
PPTX_AUTOFIT_MIN_FONT_SIZE = 800
PPTX_AUTOFIT_OVERFLOW_TOLERANCE = 1.08
PPTX_AUTOFIT_LINE_SPACE_REDUCTION = 10000
PPTX_PERCENT_DENOMINATOR = 100000
PPTX_DEFAULT_FONT_SIZE = 1800
EMU_PER_POINT = 12700
PPTX_LINE_HEIGHT_FACTOR = 1.18


@dataclass
class Replacement:
    metadata: dict[str, Any]
    source_text: str
    parts: list[str] = field(default_factory=list)
    has_target: bool = False

    @property
    def text(self) -> str:
        separator = "\n" if "\n" in self.source_text else " "
        return separator.join(part for part in self.parts if part).strip()


@dataclass(frozen=True)
class TextBoxEstimate:
    width_emu: int | None = None
    height_emu: int | None = None


class PptxExporter:
    def export(
        self,
        original_bytes: bytes,
        segments: Iterable[Any],
        document_parse_options: Mapping[str, object] | str | None = None,
    ) -> bytes:
        parsed = PptxAdapter().parse_with_options(
            original_bytes,
            filename="source.pptx",
            options=document_parse_options if isinstance(document_parse_options, dict) else None,
        )
        replacements = self._build_replacements(parsed.ast, parsed.segments, segments)
        if not replacements:
            return original_bytes

        modified_xml = self._build_modified_xml(original_bytes, replacements)
        if not modified_xml:
            return original_bytes

        output = BytesIO()
        with ZipFile(BytesIO(original_bytes), "r") as source_archive:
            with ZipFile(output, "w") as target_archive:
                for info in source_archive.infolist():
                    target_archive.writestr(
                        info,
                        modified_xml.get(info.filename, source_archive.read(info.filename)),
                    )
        return output.getvalue()

    def _build_replacements(
        self,
        ast: DocumentAST,
        parsed_segments: Iterable[Any],
        translated_segments: Iterable[Any],
    ) -> dict[tuple[Any, ...], Replacement]:
        targets = {
            str(_get_segment_value(segment, "sentence_id", _get_segment_value(segment, "segment_id", ""))): str(
                _get_segment_value(segment, "target_text", "") or ""
            )
            for segment in translated_segments
        }
        replacements: dict[tuple[Any, ...], Replacement] = {}

        for parsed_segment in parsed_segments:
            node = _resolve_node_by_path(ast, parsed_segment.block_path)
            if node is None:
                continue
            key = self._container_key(node.metadata)
            if key is None:
                continue
            replacement = replacements.setdefault(
                key,
                Replacement(metadata=dict(node.metadata), source_text=node.text_content or ""),
            )
            target_text = targets.get(parsed_segment.segment_id, "")
            if target_text.strip():
                replacement.parts.append(target_text.strip())
                replacement.has_target = True
            else:
                replacement.parts.append(parsed_segment.display_text)

        return {
            key: replacement
            for key, replacement in replacements.items()
            if replacement.has_target and replacement.text
        }

    def _container_key(self, metadata: dict[str, Any]) -> tuple[Any, ...] | None:
        item_type = metadata.get("item_type")
        if item_type == "paragraph":
            return ("paragraph", str(metadata.get("part_name", "")), int(metadata.get("paragraph_index", 0)))
        if item_type == "table_cell":
            return (
                "table_cell",
                str(metadata.get("part_name", "")),
                int(metadata.get("table_index", 0)),
                int(metadata.get("row", 0)),
                int(metadata.get("col", 0)),
            )
        if item_type == "comment":
            return ("comment", str(metadata.get("part_name", "")), int(metadata.get("comment_index", 0)))
        if item_type == "document_property":
            return (
                "document_property",
                str(metadata.get("part_name", "")),
                int(metadata.get("property_index", 0)),
            )
        return None

    def _build_modified_xml(
        self,
        original_bytes: bytes,
        replacements: dict[tuple[Any, ...], Replacement],
    ) -> dict[str, bytes]:
        _register_namespaces()
        by_part: dict[str, dict[tuple[Any, ...], Replacement]] = defaultdict(dict)
        for key, replacement in replacements.items():
            by_part[str(key[1])][key] = replacement

        modified: dict[str, bytes] = {}
        with ZipFile(BytesIO(original_bytes), "r") as archive:
            for part_name, part_replacements in by_part.items():
                root = ET.fromstring(archive.read(part_name))
                parent_map = _build_parent_map(root)

                paragraphs = root.findall(".//a:p", NS)
                for (_, _, paragraph_index), replacement in (
                    (key, value)
                    for key, value in part_replacements.items()
                    if key[0] == "paragraph"
                ):
                    if paragraph_index < len(paragraphs):
                        paragraph = paragraphs[paragraph_index]
                        _replace_text_elements(paragraph.findall(".//a:t", NS), replacement.text)
                        _apply_autofit(paragraph, replacement, parent_map)

                tables = root.findall(".//a:tbl", NS)
                for (_, _, table_index, row_index, cell_index), replacement in (
                    (key, value)
                    for key, value in part_replacements.items()
                    if key[0] == "table_cell"
                ):
                    if table_index >= len(tables):
                        continue
                    rows = tables[table_index].findall("./a:tr", NS)
                    if row_index >= len(rows):
                        continue
                    cells = rows[row_index].findall("./a:tc", NS)
                    if cell_index >= len(cells):
                        continue
                    cell = cells[cell_index]
                    _replace_text_elements(cell.findall(".//a:t", NS), replacement.text)
                    _apply_autofit(cell, replacement, parent_map)

                comments = [element for element in root.iter() if _local_name(element.tag).lower() in {"cm", "comment"}]
                if not comments and any(key[0] == "comment" for key in part_replacements):
                    comments = [root]
                for (_, _, comment_index), replacement in (
                    (key, value)
                    for key, value in part_replacements.items()
                    if key[0] == "comment"
                ):
                    if comment_index < len(comments):
                        _replace_text_elements(_comment_text_elements(comments[comment_index]), replacement.text)

                children = list(root)
                for (_, _, property_index), replacement in (
                    (key, value)
                    for key, value in part_replacements.items()
                    if key[0] == "document_property"
                ):
                    if property_index < len(children):
                        children[property_index].text = replacement.text

                modified[part_name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

        return modified


def _replace_text_elements(elements: list[ET.Element], text: str) -> None:
    if not elements:
        return
    elements[0].text = text
    if text[:1].isspace() or text[-1:].isspace():
        elements[0].set(XML_SPACE_ATTR, "preserve")
    else:
        elements[0].attrib.pop(XML_SPACE_ATTR, None)
    for element in elements[1:]:
        element.text = ""


def _apply_autofit(
    container: ET.Element,
    replacement: Replacement,
    parent_map: dict[ET.Element, ET.Element],
) -> None:
    body_pr = _find_body_properties(container, parent_map)
    font_size = _resolve_container_font_size(container, parent_map) or PPTX_DEFAULT_FONT_SIZE
    box = _estimate_text_box(container, parent_map, body_pr)
    scale = _calculate_autofit_scale(
        source_text=replacement.source_text,
        target_text=replacement.text,
        font_size=font_size,
        box=box,
    )
    if scale >= 1:
        return

    if body_pr is not None:
        _set_normal_autofit(body_pr, scale)
        return

    _scale_explicit_run_fonts(container, parent_map, scale)


def _calculate_autofit_scale(
    source_text: str,
    target_text: str,
    font_size: int,
    box: TextBoxEstimate | None,
) -> float:
    estimated_scale = _estimate_scale_for_box(target_text, font_size, box)
    if estimated_scale is not None:
        return estimated_scale

    source_units = _visual_text_units(source_text)
    target_units = _visual_text_units(target_text)
    if source_units <= 0 or target_units <= source_units * PPTX_AUTOFIT_OVERFLOW_TOLERANCE:
        return 1
    return max(PPTX_AUTOFIT_MIN_SCALE, min(1.0, (source_units / target_units) ** 0.35))


def _estimate_scale_for_box(
    text: str,
    font_size: int,
    box: TextBoxEstimate | None,
) -> float | None:
    if box is None or not box.width_emu or not box.height_emu or font_size <= 0:
        return None
    if _text_fits_box(text, font_size, box, scale=1.0):
        return 1
    if not _text_fits_box(text, font_size, box, scale=PPTX_AUTOFIT_MIN_SCALE):
        return PPTX_AUTOFIT_MIN_SCALE

    low = PPTX_AUTOFIT_MIN_SCALE
    high = 1.0
    for _ in range(12):
        midpoint = (low + high) / 2
        if _text_fits_box(text, font_size, box, scale=midpoint):
            low = midpoint
        else:
            high = midpoint
    return low


def _text_fits_box(
    text: str,
    font_size: int,
    box: TextBoxEstimate,
    scale: float,
) -> bool:
    width_units = _box_width_units(box.width_emu, font_size, scale)
    max_lines = _box_line_capacity(box.height_emu, font_size, scale)
    if width_units <= 0 or max_lines <= 0:
        return False
    return _wrapped_line_count(text, width_units) <= max_lines + 0.15


def _box_width_units(width_emu: int | None, font_size: int, scale: float) -> float:
    if not width_emu:
        return 0
    font_emu = (font_size / 100) * EMU_PER_POINT * scale
    return width_emu / font_emu if font_emu > 0 else 0


def _box_line_capacity(height_emu: int | None, font_size: int, scale: float) -> float:
    if not height_emu:
        return 0
    line_height_emu = (font_size / 100) * EMU_PER_POINT * PPTX_LINE_HEIGHT_FACTOR * scale
    return height_emu / line_height_emu if line_height_emu > 0 else 0


def _wrapped_line_count(text: str, width_units: float) -> int:
    line_count = 0
    for line in text.splitlines() or [text]:
        units = _visual_text_units(line)
        line_count += max(1, math.ceil(units / width_units))
    return line_count


def _visual_text_units(text: str) -> float:
    return sum(_visual_character_units(char) for char in text)


def _visual_character_units(char: str) -> float:
    codepoint = ord(char)
    if char in "\r\n":
        return 0.35
    if char.isspace():
        return 0.33
    if (
        0x2E80 <= codepoint <= 0xA4CF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0xFF00 <= codepoint <= 0xFFEF
        or 0x20000 <= codepoint <= 0x2FA1F
    ):
        return 1.0
    if char.isalpha():
        return 0.62 if char.islower() else 0.72
    if char.isdigit():
        return 0.62
    return 0.42


def _find_body_properties(
    container: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
) -> ET.Element | None:
    body_pr = container.find(".//a:bodyPr", NS)
    if body_pr is not None:
        return body_pr

    current = parent_map.get(container)
    while current is not None:
        body_pr = current.find("./a:bodyPr", NS)
        if body_pr is not None:
            return body_pr
        current = parent_map.get(current)
    return None


def _estimate_text_box(
    container: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
    body_pr: ET.Element | None,
) -> TextBoxEstimate | None:
    if _local_name(container.tag) == "tc":
        return _estimate_table_cell_box(container, parent_map)

    tx_body = _find_ancestor(container, parent_map, "txBody")
    if tx_body is None:
        return None
    shape = parent_map.get(tx_body)
    if shape is None:
        return None
    ext = shape.find("./p:spPr/a:xfrm/a:ext", NS)
    if ext is None:
        return None

    width = _parse_positive_int(ext.get("cx"))
    height = _parse_positive_int(ext.get("cy"))
    return _apply_text_body_insets(TextBoxEstimate(width, height), body_pr)


def _estimate_table_cell_box(
    cell: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
) -> TextBoxEstimate | None:
    row = parent_map.get(cell)
    if row is None:
        return None
    table = _find_ancestor(cell, parent_map, "tbl")
    cells = row.findall("./a:tc", NS)
    cell_index = cells.index(cell) if cell in cells else -1
    grid_columns = table.findall("./a:tblGrid/a:gridCol", NS) if table is not None else []
    width = None
    if 0 <= cell_index < len(grid_columns):
        width = _parse_positive_int(grid_columns[cell_index].get("w"))
    height = _parse_positive_int(row.get("h"))
    return _apply_table_cell_margins(TextBoxEstimate(width, height), cell)


def _apply_text_body_insets(
    box: TextBoxEstimate,
    body_pr: ET.Element | None,
) -> TextBoxEstimate:
    if body_pr is None:
        return box
    horizontal = _parse_non_negative_int(body_pr.get("lIns")) + _parse_non_negative_int(body_pr.get("rIns"))
    vertical = _parse_non_negative_int(body_pr.get("tIns")) + _parse_non_negative_int(body_pr.get("bIns"))
    return TextBoxEstimate(
        width_emu=_subtract_insets(box.width_emu, horizontal),
        height_emu=_subtract_insets(box.height_emu, vertical),
    )


def _apply_table_cell_margins(box: TextBoxEstimate, cell: ET.Element) -> TextBoxEstimate:
    cell_properties = cell.find("./a:tcPr", NS)
    if cell_properties is None:
        return box
    horizontal = _parse_non_negative_int(cell_properties.get("marL")) + _parse_non_negative_int(
        cell_properties.get("marR")
    )
    vertical = _parse_non_negative_int(cell_properties.get("marT")) + _parse_non_negative_int(
        cell_properties.get("marB")
    )
    return TextBoxEstimate(
        width_emu=_subtract_insets(box.width_emu, horizontal),
        height_emu=_subtract_insets(box.height_emu, vertical),
    )


def _subtract_insets(value: int | None, inset: int) -> int | None:
    if value is None:
        return None
    return max(1, value - inset)


def _parse_positive_int(value: str | None) -> int | None:
    parsed = _parse_non_negative_int(value)
    return parsed if parsed > 0 else None


def _parse_non_negative_int(value: str | None) -> int:
    if not value:
        return 0
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return max(0, parsed)


def _set_normal_autofit(body_pr: ET.Element, scale: float) -> None:
    for child in list(body_pr):
        if _local_name(child.tag) in {"noAutofit", "normAutofit", "spAutoFit"}:
            body_pr.remove(child)

    normal_autofit = ET.Element(_qn("a", "normAutofit"))
    normal_autofit.set("fontScale", str(int(round(scale * PPTX_PERCENT_DENOMINATOR))))
    normal_autofit.set("lnSpcReduction", str(PPTX_AUTOFIT_LINE_SPACE_REDUCTION))
    body_pr.insert(_autofit_insert_index(body_pr), normal_autofit)


def _autofit_insert_index(body_pr: ET.Element) -> int:
    for index, child in enumerate(list(body_pr)):
        if _local_name(child.tag) in {"scene3d", "sp3d", "flatTx", "extLst"}:
            return index
    return len(body_pr)


def _scale_explicit_run_fonts(
    container: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
    scale: float,
) -> None:
    for text_element in container.findall(".//a:t", NS):
        if not (text_element.text or "").strip():
            continue
        run_element = _find_text_run(text_element, parent_map)
        if run_element is None:
            continue
        font_size = _resolve_font_size(run_element, parent_map)
        if font_size is None:
            continue
        scaled_size = max(PPTX_AUTOFIT_MIN_FONT_SIZE, int(round(font_size * scale)))
        if scaled_size < font_size:
            _set_run_font_size(run_element, scaled_size)


def _resolve_container_font_size(
    container: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
) -> int | None:
    for text_element in container.findall(".//a:t", NS):
        if not (text_element.text or "").strip():
            continue
        run_element = _find_text_run(text_element, parent_map)
        if run_element is None:
            continue
        font_size = _resolve_font_size(run_element, parent_map)
        if font_size is not None:
            return font_size
    return None


def _find_text_run(
    text_element: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
) -> ET.Element | None:
    current = parent_map.get(text_element)
    while current is not None:
        if _local_name(current.tag) in {"r", "fld"}:
            return current
        current = parent_map.get(current)
    return None


def _resolve_font_size(
    run_element: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
) -> int | None:
    run_properties = run_element.find("./a:rPr", NS)
    font_size = _parse_font_size(run_properties.get("sz") if run_properties is not None else None)
    if font_size is not None:
        return font_size

    paragraph = _find_ancestor(run_element, parent_map, "p")
    if paragraph is None:
        return None
    for path in ("./a:pPr/a:defRPr", "./a:endParaRPr"):
        properties = paragraph.find(path, NS)
        font_size = _parse_font_size(properties.get("sz") if properties is not None else None)
        if font_size is not None:
            return font_size
    return None


def _parse_font_size(value: str | None) -> int | None:
    if not value:
        return None
    try:
        size = int(value)
    except ValueError:
        return None
    return size if size > 0 else None


def _set_run_font_size(run_element: ET.Element, size: int) -> None:
    run_properties = run_element.find("./a:rPr", NS)
    if run_properties is None:
        run_properties = ET.Element(_qn("a", "rPr"))
        run_element.insert(0, run_properties)
    run_properties.set("sz", str(size))


def _find_ancestor(
    element: ET.Element,
    parent_map: dict[ET.Element, ET.Element],
    local_name: str,
) -> ET.Element | None:
    current = parent_map.get(element)
    while current is not None:
        if _local_name(current.tag) == local_name:
            return current
        current = parent_map.get(current)
    return None


def _build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _resolve_node_by_path(ast: DocumentAST, block_path: str) -> BlockNode | None:
    if not block_path:
        return None
    parts = block_path.split(".")
    try:
        node = ast.nodes[int(parts[0])]
    except (IndexError, TypeError, ValueError):
        return None
    index = 1
    while index < len(parts):
        if parts[index] != "children" or index + 1 >= len(parts):
            return None
        try:
            node = node.children[int(parts[index + 1])]
        except (IndexError, TypeError, ValueError):
            return None
        index += 2
    return node


def _get_segment_value(segment: Any, field_name: str, default: Any = None) -> Any:
    if segment is None:
        return default
    if isinstance(segment, Mapping):
        return segment.get(field_name, default)
    return getattr(segment, field_name, default)


def _comment_text_elements(comment: ET.Element) -> list[ET.Element]:
    preferred = [
        element
        for element in comment.iter()
        if _local_name(element.tag).lower() == "text"
    ]
    if preferred:
        return preferred
    return [
        element
        for element in comment.iter()
        if _local_name(element.tag).lower() == "t"
    ]


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _qn(prefix: str, tag: str) -> str:
    namespaces = {"a": A_NS, "p": P_NS, "r": R_NS}
    return f"{{{namespaces[prefix]}}}{tag}"


def _register_namespaces() -> None:
    ET.register_namespace("a", A_NS)
    ET.register_namespace("p", P_NS)
    ET.register_namespace("r", R_NS)
    ET.register_namespace("cp", CORE_NS)
    ET.register_namespace("dc", DC_NS)
    ET.register_namespace("dcterms", DCTERMS_NS)
    ET.register_namespace("ep", EXTENDED_PROPS_NS)
