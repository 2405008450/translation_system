from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Iterable, Mapping
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.services.adapters.models import BlockNode, DocumentAST
from app.services.adapters.pptx_adapter import A_NS, CORE_NS, DC_NS, DCTERMS_NS, EXTENDED_PROPS_NS, NS, P_NS, R_NS, PptxAdapter


PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"


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

                paragraphs = root.findall(".//a:p", NS)
                for (_, _, paragraph_index), replacement in (
                    (key, value)
                    for key, value in part_replacements.items()
                    if key[0] == "paragraph"
                ):
                    if paragraph_index < len(paragraphs):
                        _replace_text_elements(paragraphs[paragraph_index].findall(".//a:t", NS), replacement.text)

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
                    _replace_text_elements(cells[cell_index].findall(".//a:t", NS), replacement.text)

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


def _register_namespaces() -> None:
    ET.register_namespace("a", A_NS)
    ET.register_namespace("p", P_NS)
    ET.register_namespace("r", R_NS)
    ET.register_namespace("cp", CORE_NS)
    ET.register_namespace("dc", DC_NS)
    ET.register_namespace("dcterms", DCTERMS_NS)
    ET.register_namespace("ep", EXTENDED_PROPS_NS)
