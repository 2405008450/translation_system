from __future__ import annotations

from io import BytesIO
from pathlib import PurePosixPath
import posixpath
from typing import Any, Iterable, List, Optional
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import BlockNode, DocumentAST, NodeType, ParseResult
from app.services.adapters.segment_extractor import extract_segments
from app.services.document_workspace import normalize_document_parse_options


A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CORE_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
EXTENDED_PROPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS = {
    "a": A_NS,
    "p": P_NS,
    "r": R_NS,
    "rels": RELS_NS,
    "cp": CORE_NS,
    "dc": DC_NS,
    "dcterms": DCTERMS_NS,
    "ep": EXTENDED_PROPS_NS,
    "c": C_NS,
}


class PptxAdapter(FormatAdapter):
    def supported_extensions(self) -> List[str]:
        return [".pptx"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        return self._parse(raw_bytes, options=None)

    def parse_with_options(
        self,
        raw_bytes: bytes,
        filename: str = "<unknown>",
        options: Optional[dict] = None,
    ) -> ParseResult:
        self.validate_file_size(raw_bytes, filename)
        return self._parse(raw_bytes, options=options)

    def _parse(self, raw_bytes: bytes, options: Optional[dict]) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".pptx"),
                segments=[],
                metadata={},
            )

        parse_options = normalize_document_parse_options(options)
        nodes: list[BlockNode] = []
        for part_index, (part_name, part_kind, root) in enumerate(self._iter_text_parts(raw_bytes, parse_options)):
            nodes.extend(self._parse_part(part_name, part_kind, part_index, root))
        if parse_options["pptx_translate_comments"]:
            nodes.extend(self._parse_comment_nodes(raw_bytes))
        if parse_options["pptx_translate_document_properties"]:
            nodes.extend(self._parse_document_property_nodes(raw_bytes))

        ast = DocumentAST(nodes=nodes, source_format=".pptx")
        return ParseResult(
            ast=ast,
            segments=extract_segments(ast),
            metadata={"part_count": len({node.metadata.get("part_name") for node in nodes})},
        )

    def _iter_text_parts(self, raw_bytes: bytes, options: dict[str, Any]) -> Iterable[tuple[str, str, ET.Element]]:
        try:
            with ZipFile(BytesIO(raw_bytes)) as archive:
                presentation = self._read_xml(archive, "ppt/presentation.xml")
                rels = self._read_relationships(archive, "ppt/presentation.xml")
                if presentation is None:
                    return

                seen_chart_parts: set[str] = set()
                for slide_id in presentation.findall(".//p:sldIdLst/p:sldId", NS):
                    rel_id = slide_id.get(f"{{{R_NS}}}id")
                    slide_part = rels.get(rel_id or "")
                    if not slide_part:
                        continue
                    slide_root = self._read_xml(archive, slide_part)
                    if slide_root is not None:
                        yield slide_part, "slide", slide_root

                    slide_rels = self._read_relationships(archive, slide_part)
                    for target in slide_rels.values():
                        if target.startswith("ppt/charts/") and target.endswith(".xml"):
                            if target in seen_chart_parts:
                                continue
                            seen_chart_parts.add(target)
                            chart_root = self._read_xml(archive, target)
                            if chart_root is not None:
                                yield target, "chart", chart_root

                    if not options["pptx_translate_notes"]:
                        continue
                    for target in slide_rels.values():
                        if "/notesSlides/" not in f"/{target}":
                            continue
                        notes_root = self._read_xml(archive, target)
                        if notes_root is not None:
                            yield target, "notes", notes_root
        except (BadZipFile, ET.ParseError) as exc:
            raise ParseError(filename="<unknown>", reason=f"无法解析 PPTX 文件: {exc}") from exc

    def _parse_part(
        self,
        part_name: str,
        part_kind: str,
        part_index: int,
        root: ET.Element,
    ) -> list[BlockNode]:
        if part_kind == "chart":
            return self._parse_chart(part_name, part_kind, part_index, root)

        nodes: list[BlockNode] = []
        table_paragraph_ids: set[int] = set()
        seen_table_ids: set[int] = set()
        seen_paragraph_ids: set[int] = set()
        table_indices = {id(table): index for index, table in enumerate(root.findall(".//a:tbl", NS))}
        paragraph_indices = {
            id(paragraph): index for index, paragraph in enumerate(root.findall(".//a:p", NS))
        }

        def append_table(table: ET.Element) -> None:
            table_id = id(table)
            if table_id in seen_table_ids:
                return
            seen_table_ids.add(table_id)
            table_index = table_indices.get(table_id, len(seen_table_ids) - 1)
            table_node, paragraph_ids = self._parse_table(
                table=table,
                part_name=part_name,
                part_kind=part_kind,
                part_index=part_index,
                table_index=table_index,
            )
            table_paragraph_ids.update(paragraph_ids)
            seen_paragraph_ids.update(paragraph_ids)
            if table_node.children:
                nodes.append(table_node)

        def append_paragraph(paragraph: ET.Element) -> None:
            paragraph_id = id(paragraph)
            if paragraph_id in seen_paragraph_ids or paragraph_id in table_paragraph_ids:
                return
            seen_paragraph_ids.add(paragraph_id)
            paragraph_index = paragraph_indices.get(paragraph_id, len(seen_paragraph_ids) - 1)
            text = self._collect_paragraph_text(paragraph)
            if not text:
                return
            nodes.append(
                BlockNode(
                    node_type=NodeType.NOTE if part_kind == "notes" else NodeType.PARAGRAPH,
                    text_content=text,
                    metadata={
                        "item_type": "paragraph",
                        "part_name": part_name,
                        "part_kind": part_kind,
                        "part_index": part_index,
                        "paragraph_index": paragraph_index,
                    },
                )
            )

        for container in _ordered_drawing_containers(root):
            for table in container.findall(".//a:tbl", NS):
                append_table(table)
            for paragraph in container.findall(".//a:p", NS):
                append_paragraph(paragraph)

        for table in root.findall(".//a:tbl", NS):
            append_table(table)

        for paragraph in root.findall(".//a:p", NS):
            append_paragraph(paragraph)

        return nodes

    def _parse_chart(
        self,
        part_name: str,
        part_kind: str,
        part_index: int,
        root: ET.Element,
    ) -> list[BlockNode]:
        nodes: list[BlockNode] = []

        for text_index, element in enumerate(root.findall(".//a:t", NS)):
            text = (element.text or "").strip()
            if not _is_translatable_chart_text(text):
                continue
            nodes.append(
                BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=text,
                    metadata={
                        "item_type": "chart_text",
                        "part_name": part_name,
                        "part_kind": part_kind,
                        "part_index": part_index,
                        "chart_text_kind": "a_t",
                        "chart_text_index": text_index,
                    },
                )
            )

        for text_index, element in enumerate(root.findall(".//c:v", NS)):
            text = (element.text or "").strip()
            if not _is_translatable_chart_text(text):
                continue
            nodes.append(
                BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=text,
                    metadata={
                        "item_type": "chart_text",
                        "part_name": part_name,
                        "part_kind": part_kind,
                        "part_index": part_index,
                        "chart_text_kind": "c_v",
                        "chart_text_index": text_index,
                    },
                )
            )

        return nodes

    def _parse_comment_nodes(self, raw_bytes: bytes) -> list[BlockNode]:
        nodes: list[BlockNode] = []
        for part_name, root in self._iter_xml_parts(raw_bytes, "ppt/comments"):
            comments = [element for element in root.iter() if _local_name(element.tag).lower() in {"cm", "comment"}]
            if not comments:
                comments = [root]
            for comment_index, comment in enumerate(comments):
                text_elements = _comment_text_elements(comment)
                text = self._collect_text(text_elements)
                if not text:
                    continue
                nodes.append(
                    BlockNode(
                        node_type=NodeType.NOTE,
                        text_content=text,
                        metadata={
                            "item_type": "comment",
                            "part_name": part_name,
                            "comment_index": comment_index,
                        },
                    )
                )
        return nodes

    def _parse_document_property_nodes(self, raw_bytes: bytes) -> list[BlockNode]:
        nodes: list[BlockNode] = []
        for part_name in ("docProps/core.xml", "docProps/app.xml"):
            root = self._read_archive_xml(raw_bytes, part_name)
            if root is None:
                continue
            for property_index, child in enumerate(list(root)):
                if list(child):
                    continue
                text = (child.text or "").strip()
                if not text:
                    continue
                nodes.append(
                    BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=text,
                        metadata={
                            "item_type": "document_property",
                            "part_name": part_name,
                            "property_index": property_index,
                            "property_name": _local_name(child.tag),
                        },
                    )
                )
        return nodes

    def _parse_table(
        self,
        table: ET.Element,
        part_name: str,
        part_kind: str,
        part_index: int,
        table_index: int,
    ) -> tuple[BlockNode, set[int]]:
        paragraph_ids: set[int] = set()
        rows: list[BlockNode] = []

        for row_index, row in enumerate(table.findall("./a:tr", NS)):
            cells: list[BlockNode] = []
            for cell_index, cell in enumerate(row.findall("./a:tc", NS)):
                paragraphs = cell.findall(".//a:p", NS)
                text_parts: list[str] = []
                for paragraph in paragraphs:
                    paragraph_ids.add(id(paragraph))
                    text = self._collect_paragraph_text(paragraph)
                    if text:
                        text_parts.append(text)

                if not text_parts:
                    continue
                cells.append(
                    BlockNode(
                        node_type=NodeType.TABLE_CELL,
                        text_content="\n".join(text_parts),
                        metadata={
                            "item_type": "table_cell",
                            "part_name": part_name,
                            "part_kind": part_kind,
                            "part_index": part_index,
                            "table_index": table_index,
                            "row": row_index,
                            "col": cell_index,
                        },
                    )
                )

            rows.append(BlockNode(node_type=NodeType.TABLE_ROW, children=cells))

        return (
            BlockNode(
                node_type=NodeType.TABLE,
                children=rows,
                metadata={
                    "item_type": "table",
                    "part_name": part_name,
                    "part_kind": part_kind,
                    "part_index": part_index,
                    "table_index": table_index,
                    "rows": len(rows),
                    "columns": max((len(row.children) for row in rows), default=0),
                },
            ),
            paragraph_ids,
        )

    def _collect_paragraph_text(self, paragraph: ET.Element) -> str:
        return "".join((element.text or "") for element in paragraph.findall(".//a:t", NS)).strip()

    def _collect_text(self, elements: Iterable[ET.Element]) -> str:
        return "".join((element.text or "") for element in elements).strip()

    def _iter_xml_parts(self, raw_bytes: bytes, prefix: str) -> Iterable[tuple[str, ET.Element]]:
        try:
            with ZipFile(BytesIO(raw_bytes)) as archive:
                for name in sorted(archive.namelist()):
                    if not name.startswith(prefix) or not name.endswith(".xml"):
                        continue
                    yield name, ET.fromstring(archive.read(name))
        except (BadZipFile, ET.ParseError) as exc:
            raise ParseError(filename="<unknown>", reason=f"无法解析 PPTX XML 内容: {exc}") from exc

    def _read_archive_xml(self, raw_bytes: bytes, part_name: str) -> ET.Element | None:
        try:
            with ZipFile(BytesIO(raw_bytes)) as archive:
                try:
                    return ET.fromstring(archive.read(part_name))
                except KeyError:
                    return None
        except (BadZipFile, ET.ParseError) as exc:
            raise ParseError(filename="<unknown>", reason=f"无法解析 PPTX XML 内容: {exc}") from exc

    def _read_xml(self, archive: ZipFile, part_name: str) -> ET.Element | None:
        try:
            return ET.fromstring(archive.read(part_name))
        except KeyError:
            return None

    def _read_relationships(self, archive: ZipFile, part_name: str) -> dict[str, str]:
        rels_name = _rels_part_name(part_name)
        root = self._read_xml(archive, rels_name)
        if root is None:
            return {}

        rels: dict[str, str] = {}
        for rel in root.findall("rels:Relationship", NS):
            rel_id = rel.get("Id")
            target = rel.get("Target")
            if not rel_id or not target:
                continue
            rels[rel_id] = _normalize_target(part_name, target)
        return rels

def _rels_part_name(part_name: str) -> str:
    path = PurePosixPath(part_name)
    return str(path.parent / "_rels" / f"{path.name}.rels")


def _normalize_target(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))


def _ordered_drawing_containers(root: ET.Element) -> list[ET.Element]:
    shape_trees = root.findall(".//p:spTree", NS)
    if not shape_trees:
        return []
    containers: list[ET.Element] = []
    for shape_tree in shape_trees:
        containers.extend(_ordered_child_containers(list(shape_tree), sort_by_position=True))
    return containers


def _ordered_child_containers(children: list[ET.Element], sort_by_position: bool) -> list[ET.Element]:
    ordered: list[tuple[tuple[int, int, int], int, ET.Element]] = []
    for fallback_index, child in enumerate(children):
        if not _is_drawing_container(child):
            continue
        sort_key = _drawing_sort_key(child) if sort_by_position else (0, fallback_index, 0)
        ordered.append((sort_key, fallback_index, child))

    containers: list[ET.Element] = []
    for _, _, child in sorted(ordered, key=lambda item: (item[0], item[1])):
        if _local_name(child.tag) == "grpSp":
            containers.extend(_ordered_child_containers(list(child), sort_by_position=False))
        elif _has_pptx_text_container(child):
            containers.append(child)
    return containers


def _is_drawing_container(element: ET.Element) -> bool:
    return _local_name(element.tag) in {"sp", "grpSp", "graphicFrame", "cxnSp"}


def _has_pptx_text_container(element: ET.Element) -> bool:
    return (
        element.find(".//a:t", NS) is not None
        or element.find(".//a:tbl", NS) is not None
    )


def _drawing_sort_key(element: ET.Element) -> tuple[int, int, int]:
    x, y = _drawing_position(element)
    if x is None or y is None:
        return (1, 0, 0)
    return (0, y, x)


def _drawing_position(element: ET.Element) -> tuple[int | None, int | None]:
    for path in ("./p:spPr/a:xfrm/a:off", "./p:xfrm/a:off", "./p:grpSpPr/a:xfrm/a:off"):
        offset = element.find(path, NS)
        if offset is None:
            continue
        x = _parse_int(offset.get("x"))
        y = _parse_int(offset.get("y"))
        if x is not None and y is not None:
            return x, y

    child_positions = [
        position
        for child in element
        for position in [_drawing_position(child)]
        if position[0] is not None and position[1] is not None
    ]
    if not child_positions:
        return None, None
    return min(x for x, _ in child_positions), min(y for _, y in child_positions)


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _is_translatable_chart_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped or _is_number_like(stripped):
        return False
    return any(char.isalpha() or _is_cjk(char) for char in stripped)


def _is_number_like(text: str) -> bool:
    normalized = text.replace(",", "").replace("%", "").strip()
    if not normalized:
        return False
    try:
        float(normalized)
        return True
    except ValueError:
        return False


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x2E80 <= codepoint <= 0xA4CF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0xFF00 <= codepoint <= 0xFFEF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


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
