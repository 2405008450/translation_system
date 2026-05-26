from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import PurePosixPath
import posixpath
import re
from typing import Any, Iterable, Mapping
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.services.adapters.models import BlockNode, DocumentAST
from app.services.adapters.xlsx_adapter import MAIN_NS, NS, XlsxAdapter
from app.services.document_workspace import normalize_document_parse_options


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


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


class XlsxExporter:
    def export(
        self,
        original_bytes: bytes,
        segments: Iterable[Any],
        document_parse_options: Mapping[str, object] | str | None = None,
    ) -> bytes:
        parse_options = normalize_document_parse_options(document_parse_options)
        parsed = XlsxAdapter().parse_with_options(original_bytes, options=parse_options)
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
        if item_type == "cell":
            return ("cell", int(metadata.get("sheet_index", 0)), str(metadata.get("cell_ref", "")))
        if item_type == "comment":
            return ("comment", str(metadata.get("part_name", "")), int(metadata.get("comment_index", 0)))
        if item_type == "drawing_text":
            return ("drawing_text", str(metadata.get("part_name", "")), int(metadata.get("paragraph_index", 0)))
        if item_type == "sheet_name":
            return ("sheet_name", int(metadata.get("sheet_index", 0)))
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
        modified: dict[str, bytes] = {}
        with ZipFile(BytesIO(original_bytes), "r") as archive:
            sheet_parts = _get_sheet_parts(archive)
            cell_replacements = _group_replacements(replacements, "cell")
            for sheet_index, sheet_part in enumerate(sheet_parts):
                entries = cell_replacements.get(sheet_index, {})
                if entries:
                    original_xml = archive.read(sheet_part)
                    root = ET.fromstring(original_xml)
                    for cell in root.findall(".//main:c", NS):
                        cell_ref = cell.get("r", "")
                        replacement = entries.get(cell_ref)
                        if replacement is None:
                            continue
                        _set_cell_inline_string(cell, replacement.text)
                    modified[sheet_part] = _serialize_xml(root, original_xml)

            workbook_updates = self._update_workbook_xml(archive, replacements)
            if workbook_updates is not None:
                modified["xl/workbook.xml"] = workbook_updates

            for part_name, part_replacements in _group_by_part(replacements, "comment").items():
                original_xml = archive.read(part_name)
                root = ET.fromstring(original_xml)
                comments = root.findall(".//main:comment", NS)
                for (_, _, comment_index), replacement in part_replacements.items():
                    if comment_index < len(comments):
                        _replace_text_elements(comments[comment_index].findall(".//main:t", NS), replacement.text)
                modified[part_name] = _serialize_xml(root, original_xml)

            for part_name, part_replacements in _group_by_part(replacements, "drawing_text").items():
                original_xml = archive.read(part_name)
                root = ET.fromstring(original_xml)
                paragraphs = root.findall(".//a:p", NS)
                for (_, _, paragraph_index), replacement in part_replacements.items():
                    if paragraph_index < len(paragraphs):
                        _replace_text_elements(paragraphs[paragraph_index].findall(".//a:t", NS), replacement.text)
                modified[part_name] = _serialize_xml(root, original_xml)

            for part_name, part_replacements in _group_by_part(replacements, "document_property").items():
                original_xml = archive.read(part_name)
                root = ET.fromstring(original_xml)
                children = list(root)
                for (_, _, property_index), replacement in part_replacements.items():
                    if property_index < len(children):
                        children[property_index].text = _sanitize_xml_text(replacement.text)
                modified[part_name] = _serialize_xml(root, original_xml)

        return modified

    def _update_workbook_xml(
        self,
        archive: ZipFile,
        replacements: dict[tuple[Any, ...], Replacement],
    ) -> bytes | None:
        sheet_replacements = {
            key[1]: replacement
            for key, replacement in replacements.items()
            if key[0] == "sheet_name"
        }
        if not sheet_replacements:
            return None

        original_xml = archive.read("xl/workbook.xml")
        root = ET.fromstring(original_xml)
        sheets = root.findall(".//main:sheets/main:sheet", NS)
        existing_names = [
            sheet.get("name", "")
            for index, sheet in enumerate(sheets)
            if index not in sheet_replacements
        ]
        for sheet_index, replacement in sheet_replacements.items():
            if sheet_index >= len(sheets):
                continue
            safe_name = _sanitize_sheet_name(replacement.text, existing_names)
            sheets[sheet_index].set("name", safe_name)
            existing_names.append(safe_name)
        return _serialize_xml(root, original_xml)


def _group_replacements(
    replacements: dict[tuple[Any, ...], Replacement],
    kind: str,
) -> dict[int, dict[str, Replacement]]:
    grouped: dict[int, dict[str, Replacement]] = defaultdict(dict)
    for key, replacement in replacements.items():
        if key[0] == kind:
            grouped[int(key[1])][str(key[2])] = replacement
    return grouped


def _group_by_part(
    replacements: dict[tuple[Any, ...], Replacement],
    kind: str,
) -> dict[str, dict[tuple[Any, ...], Replacement]]:
    grouped: dict[str, dict[tuple[Any, ...], Replacement]] = defaultdict(dict)
    for key, replacement in replacements.items():
        if key[0] == kind:
            grouped[str(key[1])][key] = replacement
    return grouped


def _get_sheet_parts(archive: ZipFile) -> list[str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = _read_relationships(archive, "xl/workbook.xml")
    parts: list[str] = []
    for sheet in workbook.findall(".//main:sheets/main:sheet", NS):
        rel_id = sheet.get(f"{{{R_NS}}}id")
        if rel_id and rel_id in rels:
            parts.append(rels[rel_id])
    return parts


def _read_relationships(archive: ZipFile, part_name: str) -> dict[str, str]:
    rels_name = _rels_part_name(part_name)
    try:
        root = ET.fromstring(archive.read(rels_name))
    except KeyError:
        return {}
    result: dict[str, str] = {}
    for rel in root.findall(f"{{{RELS_NS}}}Relationship"):
        rel_id = rel.get("Id")
        target = rel.get("Target")
        if rel_id and target:
            result[rel_id] = _normalize_target(part_name, target)
    return result


def _set_cell_inline_string(cell: ET.Element, text: str) -> None:
    text = _sanitize_xml_text(text)
    for child in list(cell):
        cell.remove(child)
    cell.set("t", "inlineStr")
    inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
    text_element = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
    text_element.text = text
    if text[:1].isspace() or text[-1:].isspace():
        text_element.set(XML_SPACE_ATTR, "preserve")


def _replace_text_elements(elements: list[ET.Element], text: str) -> None:
    if not elements:
        return
    text = _sanitize_xml_text(text)
    elements[0].text = text
    if text[:1].isspace() or text[-1:].isspace():
        elements[0].set(XML_SPACE_ATTR, "preserve")
    for element in elements[1:]:
        element.text = ""


def _sanitize_sheet_name(name: str, existing_names: list[str]) -> str:
    name = _sanitize_xml_text(name)
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip() or "Sheet"
    base = base[:31]
    candidate = base
    index = 2
    existing = {item.lower() for item in existing_names}
    while candidate.lower() in existing:
        suffix = f" {index}"
        candidate = f"{base[: 31 - len(suffix)]}{suffix}"
        index += 1
    return candidate


def _rels_part_name(part_name: str) -> str:
    path = PurePosixPath(part_name)
    return str(path.parent / "_rels" / f"{path.name}.rels")


def _normalize_target(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))


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


def _sanitize_xml_text(text: str) -> str:
    return "".join(
        char
        for char in str(text)
        if char in "\t\n\r"
        or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
    )


def _serialize_xml(root: ET.Element, original_xml: bytes | None = None) -> bytes:
    if original_xml:
        _restore_compatibility_namespace_declarations(root, original_xml)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _register_namespaces() -> None:
    ET.register_namespace("", MAIN_NS)
    ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
    ET.register_namespace("r", R_NS)
    ET.register_namespace("cp", "http://schemas.openxmlformats.org/package/2006/metadata/core-properties")
    ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
    ET.register_namespace("dcterms", "http://purl.org/dc/terms/")
    ET.register_namespace("ep", "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties")
    ET.register_namespace("mc", MC_NS)


def _restore_compatibility_namespace_declarations(root: ET.Element, original_xml: bytes) -> None:
    namespaces = _extract_namespace_declarations(original_xml)
    for prefix, uri in namespaces.items():
        _register_original_namespace(prefix, uri)

    compatibility_prefixes: set[str] = set()
    for attr_name, attr_value in root.attrib.items():
        if not attr_name.startswith(f"{{{MC_NS}}}") or not attr_value:
            continue
        for token in str(attr_value).split():
            prefix = token.split(":", 1)[0].strip()
            if prefix:
                compatibility_prefixes.add(prefix)

    for prefix in compatibility_prefixes:
        uri = namespaces.get(prefix)
        if not uri or _namespace_uri_is_used(root, uri):
            continue
        root.set(f"xmlns:{prefix}", uri)


def _extract_namespace_declarations(xml_bytes: bytes) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    for _, namespace in ET.iterparse(BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = namespace
        namespaces.setdefault(prefix or "", uri)
    return namespaces


def _register_original_namespace(prefix: str, uri: str) -> None:
    if not prefix or prefix == "xml" or re.fullmatch(r"ns\d+", prefix):
        return
    try:
        ET.register_namespace(prefix, uri)
    except ValueError:
        return


def _namespace_uri_is_used(root: ET.Element, uri: str) -> bool:
    namespace_prefix = f"{{{uri}}}"
    for element in root.iter():
        if element.tag.startswith(namespace_prefix):
            return True
        for attr_name in element.attrib:
            if attr_name.startswith(namespace_prefix):
                return True
    return False
