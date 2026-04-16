from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from io import BytesIO
from itertools import count
from pathlib import Path
from typing import Any
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.services.document_workspace import (
    DocxPackage,
    NS,
    StoryPart,
    _build_story_parts,
    _build_trimmed_span,
    _decode_symbol,
    _iter_block_nodes,
    _local_name,
    _qn,
)
from app.services.normalizer import normalize_text
from app.services.sentence_splitter import SentenceSpan, split_sentence_spans


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"
EXPORT_FONT_FAMILY = "Times New Roman"
BlockKey = tuple[str, int, int | None, int | None]


@dataclass(frozen=True)
class ExportSegment:
    sentence_id: str
    source_text: str
    target_text: str


@dataclass
class TextToken:
    display_text: str
    source_text: str
    element: ET.Element | None = None
    run_element: ET.Element | None = None
    original_text: str = ""
    start: int = 0
    end: int = 0
    edits: list[tuple[int, int, str]] = field(default_factory=list)
    apply_export_font: bool = False


def export_translated_docx(raw_bytes: bytes, segments: Iterable[Any]) -> bytes:
    package = DocxPackage(raw_bytes)
    stories = _build_story_parts(package)
    segments_by_block = _group_segments_by_block(segments)
    block_counter = count(0)

    for story in stories:
        _export_block_sequence(
            container=story.root,
            story=story,
            block_counter=block_counter,
            segments_by_block=segments_by_block,
        )

    _localize_numbering_definitions(package)

    return _build_modified_docx(
        raw_bytes=raw_bytes,
        package=package,
        part_names={story.part_name for story in stories} | {"word/numbering.xml"},
    )


def build_translated_docx_filename(filename: str) -> str:
    source_path = Path(filename or "document.docx")
    return f"{source_path.stem}_translated.docx"


def _group_segments_by_block(segments: Iterable[Any]) -> dict[BlockKey, list[ExportSegment]]:
    grouped: dict[BlockKey, list[ExportSegment]] = defaultdict(list)

    for segment in segments:
        block_type = str(_get_segment_value(segment, "block_type", "paragraph") or "paragraph")
        block_index = int(_get_segment_value(segment, "block_index", 0) or 0)
        row_index = _to_optional_int(_get_segment_value(segment, "row_index"))
        cell_index = _to_optional_int(_get_segment_value(segment, "cell_index"))

        grouped[(block_type, block_index, row_index, cell_index)].append(
            ExportSegment(
                sentence_id=str(_get_segment_value(segment, "sentence_id", "") or ""),
                source_text=str(_get_segment_value(segment, "source_text", "") or ""),
                target_text=str(_get_segment_value(segment, "target_text", "") or ""),
            )
        )

    return grouped


def _export_block_sequence(
    container: ET.Element,
    story: StoryPart,
    block_counter,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    default_block_type: str = "paragraph",
    fixed_block_index: int | None = None,
    row_index: int | None = None,
    cell_index: int | None = None,
) -> None:
    for block in _iter_block_nodes(container):
        _export_block(
            block=block,
            story=story,
            block_counter=block_counter,
            segments_by_block=segments_by_block,
            default_block_type=default_block_type,
            fixed_block_index=fixed_block_index,
            row_index=row_index,
            cell_index=cell_index,
        )


def _export_block(
    block: ET.Element,
    story: StoryPart,
    block_counter,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    default_block_type: str,
    fixed_block_index: int | None,
    row_index: int | None,
    cell_index: int | None,
) -> None:
    block_name = _local_name(block.tag)
    if block_name == "p":
        block_index = fixed_block_index if fixed_block_index is not None else next(block_counter)
        _export_paragraph(
            paragraph=block,
            story=story,
            block_counter=block_counter,
            block_index=block_index,
            block_type=default_block_type,
            row_index=row_index,
            cell_index=cell_index,
            segments_by_block=segments_by_block,
        )
        return

    if block_name == "tbl":
        _export_table(
            table=block,
            story=story,
            block_counter=block_counter,
            segments_by_block=segments_by_block,
        )


def _export_table(
    table: ET.Element,
    story: StoryPart,
    block_counter,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> None:
    block_index = next(block_counter)

    for row_index, row in enumerate(table.findall("./w:tr", NS)):
        for cell_index, cell in enumerate(row.findall("./w:tc", NS)):
            _export_block_sequence(
                container=cell,
                story=story,
                block_counter=block_counter,
                segments_by_block=segments_by_block,
                default_block_type="table_cell",
                fixed_block_index=block_index,
                row_index=row_index,
                cell_index=cell_index,
            )


def _export_paragraph(
    paragraph: ET.Element,
    story: StoryPart,
    block_counter,
    block_index: int,
    block_type: str,
    row_index: int | None,
    cell_index: int | None,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> None:
    tokens = _collect_inline_tokens(
        node=paragraph,
        story=story,
        block_counter=block_counter,
        segments_by_block=segments_by_block,
    )

    _replace_block_tokens(
        tokens=tokens,
        segments=segments_by_block.get(
            (_resolve_segment_block_type(story.kind, block_type), block_index, row_index, cell_index),
            [],
        ),
    )


def _collect_inline_tokens(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    current_run: ET.Element | None = None,
) -> list[TextToken]:
    node_name = _local_name(node.tag)
    if node_name in {"pPr", "rPr", "tblPr", "tblGrid", "trPr", "tcPr", "sectPr"}:
        return []

    if node_name == "r":
        current_run = node

    if node_name == "t":
        text_value = node.text or ""
        return [
            TextToken(
                display_text=text_value,
                source_text=text_value,
                element=node,
                run_element=current_run,
                original_text=text_value,
            )
        ]

    if node_name == "tab":
        return [TextToken(display_text="\t", source_text="\t")]

    if node_name in {"br", "cr"}:
        return [TextToken(display_text="\n", source_text="\n")]

    if node_name == "noBreakHyphen":
        return [TextToken(display_text="-", source_text="-")]

    if node_name == "sym":
        symbol_text = _decode_symbol(node)
        if not symbol_text:
            return []
        return [TextToken(display_text=symbol_text, source_text=symbol_text)]

    if node_name in {"footnoteReference", "endnoteReference"}:
        note_id = node.get(_qn("w", "id"), "")
        marker = f"[{note_id}]" if note_id else "[*]"
        return [TextToken(display_text=marker, source_text=" " * len(marker))]

    if node_name in {"drawing", "pict"}:
        _export_embedded_textboxes(
            node=node,
            story=story,
            block_counter=block_counter,
            segments_by_block=segments_by_block,
        )
        return []

    if node_name == "instrText":
        return []

    tokens: list[TextToken] = []
    for child in list(node):
        tokens.extend(
            _collect_inline_tokens(
                node=child,
                story=story,
                block_counter=block_counter,
                segments_by_block=segments_by_block,
                current_run=current_run,
            )
        )

    return tokens


def _export_embedded_textboxes(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> None:
    textbox_contents = node.findall(".//w:txbxContent", NS)
    if textbox_contents:
        for textbox_content in textbox_contents:
            _export_block_sequence(
                container=textbox_content,
                story=story,
                block_counter=block_counter,
                segments_by_block=segments_by_block,
                default_block_type="textbox",
            )
        return

    text_elements = [element for element in node.findall(".//a:t", NS) if element.text]
    if not text_elements:
        return

    tokens: list[TextToken] = []
    for element in text_elements:
        text_value = element.text or ""
        if not text_value:
            continue
        if tokens:
            tokens.append(TextToken(display_text="\n", source_text="\n"))
        tokens.append(
            TextToken(
                display_text=text_value,
                source_text=text_value,
                element=element,
                original_text=text_value,
            )
        )

    display_text = "".join(token.display_text for token in tokens)
    if not normalize_text(display_text):
        return

    _replace_block_tokens(
        tokens=tokens,
        segments=segments_by_block.get(
            (_resolve_segment_block_type(story.kind, "textbox"), next(block_counter), None, None),
            [],
        ),
    )


def _replace_block_tokens(
    tokens: list[TextToken],
    segments: list[ExportSegment],
) -> None:
    if not tokens or not segments:
        return

    _assign_token_offsets(tokens)
    display_text = "".join(token.display_text for token in tokens)
    if not display_text:
        return

    spans = split_sentence_spans(display_text)
    if not spans and normalize_text(display_text):
        spans = [_build_trimmed_span(display_text)]

    segment_index = 0
    for span in spans:
        sentence_source = normalize_text(_collect_span_text(tokens, span, use_source=True))
        if not sentence_source:
            continue

        if segment_index >= len(segments):
            break

        segment = segments[segment_index]
        segment_index += 1
        replacement = segment.target_text
        if not normalize_text(replacement):
            continue

        _queue_sentence_replacement(tokens, span, replacement)

    _apply_token_edits(tokens)


def _assign_token_offsets(tokens: list[TextToken]) -> None:
    cursor = 0
    for token in tokens:
        token.start = cursor
        cursor += len(token.display_text)
        token.end = cursor


def _collect_span_text(
    tokens: list[TextToken],
    span: SentenceSpan,
    use_source: bool,
) -> str:
    pieces: list[str] = []
    for token in tokens:
        overlap_start = max(span.start, token.start)
        overlap_end = min(span.end, token.end)
        if overlap_end <= overlap_start:
            continue

        local_start = overlap_start - token.start
        local_end = overlap_end - token.start
        base_text = token.source_text if use_source else token.display_text
        pieces.append(base_text[local_start:local_end])

    return "".join(pieces)


def _queue_sentence_replacement(
    tokens: list[TextToken],
    span: SentenceSpan,
    replacement: str,
) -> None:
    writable_overlaps: list[tuple[TextToken, int, int]] = []
    for token in tokens:
        if token.element is None:
            continue

        overlap_start = max(span.start, token.start)
        overlap_end = min(span.end, token.end)
        if overlap_end <= overlap_start:
            continue

        writable_overlaps.append(
            (token, overlap_start - token.start, overlap_end - token.start)
        )

    if not writable_overlaps:
        return

    first_token, first_start, first_end = writable_overlaps[0]
    first_token.edits.append((first_start, first_end, replacement))
    first_token.apply_export_font = True

    for token, local_start, local_end in writable_overlaps[1:]:
        token.edits.append((local_start, local_end, ""))


def _apply_token_edits(tokens: list[TextToken]) -> None:
    for token in tokens:
        if token.element is None or not token.edits:
            continue

        text_value = token.original_text
        for start, end, replacement in sorted(token.edits, key=lambda item: item[0], reverse=True):
            text_value = f"{text_value[:start]}{replacement}{text_value[end:]}"

        token.element.text = text_value
        if _needs_space_preserve(text_value):
            token.element.set(XML_SPACE_ATTR, "preserve")
        else:
            token.element.attrib.pop(XML_SPACE_ATTR, None)
        if token.apply_export_font and token.run_element is not None:
            _apply_export_font(token.run_element)


def _needs_space_preserve(text: str) -> bool:
    return bool(text) and (text[0].isspace() or text[-1].isspace())


def _apply_export_font(run_element: ET.Element) -> None:
    run_tag = _local_name(run_element.tag)
    if run_tag != "r":
        return

    namespace_uri = _namespace_uri(run_element.tag)
    if namespace_uri == NS["w"]:
        _apply_word_run_font(run_element)
        return
    if namespace_uri == NS["a"]:
        _apply_drawingml_run_font(run_element)


def _apply_word_run_font(run_element: ET.Element) -> None:
    run_properties = run_element.find("w:rPr", NS)
    if run_properties is None:
        run_properties = ET.Element(_qn("w", "rPr"))
        run_element.insert(0, run_properties)

    fonts = run_properties.find("w:rFonts", NS)
    if fonts is None:
        fonts = ET.Element(_qn("w", "rFonts"))
        run_properties.insert(0, fonts)

    for attr_name in ("ascii", "hAnsi", "cs", "eastAsia"):
        fonts.set(_qn("w", attr_name), EXPORT_FONT_FAMILY)
    for theme_attr in ("asciiTheme", "hAnsiTheme", "csTheme", "eastAsiaTheme"):
        fonts.attrib.pop(_qn("w", theme_attr), None)


def _apply_drawingml_run_font(run_element: ET.Element) -> None:
    run_properties = run_element.find("a:rPr", NS)
    if run_properties is None:
        run_properties = ET.Element(_qn("a", "rPr"))
        run_element.insert(0, run_properties)

    for child_name in ("latin", "ea", "cs"):
        font_element = run_properties.find(f"a:{child_name}", NS)
        if font_element is None:
            font_element = ET.Element(_qn("a", child_name))
            run_properties.append(font_element)
        font_element.set("typeface", EXPORT_FONT_FAMILY)


def _namespace_uri(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _localize_numbering_definitions(package: DocxPackage) -> None:
    numbering_root = package.read_xml("word/numbering.xml")
    if numbering_root is None:
        return

    for level in numbering_root.findall(".//w:lvl", NS):
        localized_definition = _build_localized_numbering_definition(level)
        if localized_definition is None:
            continue

        num_fmt_value, lvl_text_value, suffix_value = localized_definition
        _set_level_child_value(level, "numFmt", num_fmt_value)
        _set_level_child_value(level, "lvlText", lvl_text_value)
        _set_level_child_value(level, "suff", suffix_value)
        _apply_numbering_level_font(level)


def _build_localized_numbering_definition(
    level: ET.Element,
) -> tuple[str, str, str] | None:
    lvl_text_element = level.find("./w:lvlText", NS)
    if lvl_text_element is None:
        return None

    lvl_text_value = lvl_text_element.get(_qn("w", "val"), "")
    if not lvl_text_value:
        return None

    num_fmt_element = level.find("./w:numFmt", NS)
    num_fmt_value = "decimal" if num_fmt_element is None else num_fmt_element.get(_qn("w", "val"), "decimal")
    suffix_element = level.find("./w:suff", NS)
    suffix_value = "tab" if suffix_element is None else suffix_element.get(_qn("w", "val"), "tab")

    for chinese_marker, english_label in (
        ("章", "Chapter"),
        ("节", "Section"),
        ("条", "Article"),
        ("款", "Clause"),
    ):
        if chinese_marker in lvl_text_value and "%1" in lvl_text_value:
            return "decimal", f"{english_label} %1", "space"

    normalized_text = _normalize_numbering_pattern(lvl_text_value)
    if normalized_text != lvl_text_value or num_fmt_value in {
        "chineseCounting",
        "chineseLegalSimplified",
        "ideographDigital",
        "chineseCountingThousand",
    }:
        if _has_only_placeholders_and_ascii_punctuation(normalized_text):
            normalized_text = normalized_text or "%1."
            return "decimal", normalized_text, "space"

    if any(token in lvl_text_value for token in ("、", "（", "）", "．", "。")) and _has_only_placeholders_and_ascii_punctuation(normalized_text):
        return "decimal", normalized_text or "%1.", "space"

    return None


def _normalize_numbering_pattern(lvl_text: str) -> str:
    normalized = lvl_text
    for source, target in (
        ("（", "("),
        ("）", ")"),
        ("【", "["),
        ("】", "]"),
        ("、", "."),
        ("．", "."),
        ("。", "."),
        ("第", ""),
        ("章", ""),
        ("节", ""),
        ("条", ""),
        ("款", ""),
    ):
        normalized = normalized.replace(source, target)
    return normalized.strip()


def _has_only_placeholders_and_ascii_punctuation(text: str) -> bool:
    stripped = text
    for match in ("%1", "%2", "%3", "%4", "%5", "%6", "%7", "%8", "%9"):
        stripped = stripped.replace(match, "")
    stripped = stripped.replace("(", "").replace(")", "").replace("[", "").replace("]", "")
    stripped = stripped.replace(".", "").replace("-", "").replace(" ", "")
    return stripped == ""


def _set_level_child_value(level: ET.Element, child_name: str, value: str) -> None:
    child = level.find(f"./w:{child_name}", NS)
    if child is None:
        child = ET.Element(_qn("w", child_name))
        level.append(child)
    child.set(_qn("w", "val"), value)


def _apply_numbering_level_font(level: ET.Element) -> None:
    run_properties = level.find("./w:rPr", NS)
    if run_properties is None:
        run_properties = ET.Element(_qn("w", "rPr"))
        level.append(run_properties)

    fonts = run_properties.find("./w:rFonts", NS)
    if fonts is None:
        fonts = ET.Element(_qn("w", "rFonts"))
        run_properties.insert(0, fonts)

    for attr_name in ("ascii", "hAnsi", "cs", "eastAsia"):
        fonts.set(_qn("w", attr_name), EXPORT_FONT_FAMILY)
    for theme_attr in ("asciiTheme", "hAnsiTheme", "csTheme", "eastAsiaTheme"):
        fonts.attrib.pop(_qn("w", theme_attr), None)


def _build_modified_docx(
    raw_bytes: bytes,
    package: DocxPackage,
    part_names: set[str],
) -> bytes:
    modified_xml: dict[str, bytes] = {}

    with ZipFile(BytesIO(raw_bytes)) as source_archive:
        for part_name in part_names:
            normalized_name = part_name.lstrip("/")
            root = package.read_xml(normalized_name)
            if root is None:
                continue

            _register_namespaces(source_archive.read(normalized_name))
            modified_xml[normalized_name] = ET.tostring(
                root,
                encoding="utf-8",
                xml_declaration=True,
            )

        output = BytesIO()
        with ZipFile(output, "w") as target_archive:
            for info in source_archive.infolist():
                target_archive.writestr(
                    info,
                    modified_xml.get(info.filename, source_archive.read(info.filename)),
                )

    return output.getvalue()


def _register_namespaces(xml_bytes: bytes) -> None:
    seen_namespaces: set[tuple[str, str]] = set()
    for _, namespace in ET.iterparse(BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = namespace
        key = (prefix or "", uri)
        if key in seen_namespaces:
            continue
        seen_namespaces.add(key)
        ET.register_namespace(prefix or "", uri)


def _resolve_segment_block_type(story_kind: str, block_type: str) -> str:
    if block_type in {"table_cell", "textbox"}:
        return block_type
    if story_kind in {"header", "footer", "footnote", "endnote"}:
        return story_kind
    return "paragraph"


def _get_segment_value(segment: Any, name: str, default: Any = None) -> Any:
    if isinstance(segment, Mapping):
        return segment.get(name, default)
    return getattr(segment, name, default)


def _to_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
