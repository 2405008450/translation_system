from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from itertools import count
from pathlib import Path
import re
from typing import Any
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.services.automatic_numbering import (
    build_localized_docx_numbering_definition,
    strip_automatic_numbering_prefix,
)
from app.services.document_workspace import (
    CELL_GROUP_MAX_CHARS,
    CELL_NEXT_PARAGRAPH_MAX_CHARS,
    CELL_PARAGRAPH_BREAK_SENTINEL,
    CELL_SENTENCE_END_CHARS,
    CELL_SHORT_PARAGRAPH_MAX_CHARS,
    DOCUMENT_PARSE_MODE_FULL,
    DocxPackage,
    MATH_PLACEHOLDER_TEMPLATE,
    NS,
    NumberingSchema,
    OMML_ATOMIC_TAGS,
    StoryPart,
    get_cached_docx_workspace,
    _build_numbering_schema,
    _build_story_parts,
    _build_trimmed_span,
    _decode_symbol,
    _iter_block_nodes,
    _local_name,
    _normalize_segment_source_text,
    _qn,
    _resolve_internal_reference_field_target,
    _resolve_paragraph_numbering_reference,
    _select_preferred_alternate_content_branch,
    normalize_document_parse_options,
    normalize_document_parse_mode,
)
from app.services.normalizer import normalize_text
from app.services.sentence_splitter import SentenceSpan, split_sentence_spans


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
EXPORT_FONT_FAMILY = "Times New Roman"
BILINGUAL_LAYOUT_SOURCE_FIRST = "source_first"
BILINGUAL_LAYOUT_TARGET_FIRST = "target_first"
BlockKey = tuple[str, int, int | None, int | None]
MATH_PLACEHOLDER_RE = re.compile(r"⟦MATH_\d+⟧|\[\[MATH_\d+\]\]")
ENGLISH_BOUNDARY_TRAILING_RE = re.compile(r"[,;:.!?][\"')\]\}]*$")
ENGLISH_WORD_LEADING_RE = re.compile(r"^[\"'“‘(\[]*[A-Za-z0-9]")
# 支持的格式标签
FORMAT_TAG_RE = re.compile(r"<(/?)(b|strong|i|em|u|s|strike|del|sub|sup)>", re.IGNORECASE)


@dataclass(frozen=True)
class FormattedTextFragment:
    """带格式的文本片段"""
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    subscript: bool = False
    superscript: bool = False


def _has_format_tags(html: str | None) -> bool:
    """检查 HTML 是否包含格式标签"""
    if not html:
        return False
    return bool(FORMAT_TAG_RE.search(html))


def _parse_formatted_html(html: str) -> list[FormattedTextFragment]:
    """解析带格式的 HTML，返回格式化文本片段列表"""
    fragments: list[FormattedTextFragment] = []

    class FormatHTMLParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.format_stack: list[set[str]] = [set()]
            self.current_formats: set[str] = set()

        def handle_starttag(self, tag: str, attrs):
            tag_lower = tag.lower()
            if tag_lower in ('b', 'strong'):
                self.current_formats.add('bold')
            elif tag_lower in ('i', 'em'):
                self.current_formats.add('italic')
            elif tag_lower == 'u':
                self.current_formats.add('underline')
            elif tag_lower in ('s', 'strike', 'del'):
                self.current_formats.add('strike')
            elif tag_lower == 'sub':
                self.current_formats.add('subscript')
            elif tag_lower == 'sup':
                self.current_formats.add('superscript')
            self.format_stack.append(self.current_formats.copy())

        def handle_endtag(self, tag: str):
            tag_lower = tag.lower()
            if tag_lower in ('b', 'strong'):
                self.current_formats.discard('bold')
            elif tag_lower in ('i', 'em'):
                self.current_formats.discard('italic')
            elif tag_lower == 'u':
                self.current_formats.discard('underline')
            elif tag_lower in ('s', 'strike', 'del'):
                self.current_formats.discard('strike')
            elif tag_lower == 'sub':
                self.current_formats.discard('subscript')
            elif tag_lower == 'sup':
                self.current_formats.discard('superscript')
            if self.format_stack:
                self.format_stack.pop()

        def handle_data(self, data: str):
            if data:
                fragments.append(FormattedTextFragment(
                    text=data,
                    bold='bold' in self.current_formats,
                    italic='italic' in self.current_formats,
                    underline='underline' in self.current_formats,
                    strike='strike' in self.current_formats,
                    subscript='subscript' in self.current_formats,
                    superscript='superscript' in self.current_formats,
                ))

    parser = FormatHTMLParser()
    parser.feed(html)
    return fragments


@dataclass(frozen=True)
class ExportSegment:
    sentence_id: str
    source_text: str
    target_text: str
    display_text: str = ""
    numbering_text: str = ""
    matched_source_text: str = ""
    target_html: str | None = None
    math_placeholders: dict[str, str] = field(default_factory=dict)


@dataclass
class TextToken:
    display_text: str
    source_text: str
    element: ET.Element | None = None
    run_element: ET.Element | None = None
    anchor_element: ET.Element | None = None
    container_element: ET.Element | None = None
    original_text: str = ""
    start: int = 0
    end: int = 0
    edits: list[tuple[int, int, str]] = field(default_factory=list)
    apply_export_font: bool = False
    is_math: bool = False
    is_hyperlink: bool = False
    hyperlink_element: object | None = None


@dataclass(frozen=True)
class CellParagraphTokens:
    paragraph: ET.Element
    tokens: list[TextToken]
    parent: ET.Element | None = None


@dataclass
class ExportTrackedField:
    instruction_parts: list[str] = field(default_factory=list)
    collecting_instruction: bool = True
    hyperlink_key: object | None = None


def export_bilingual_docx_with_layout(
    raw_bytes: bytes,
    segments: Iterable[Any],
    order: str = BILINGUAL_LAYOUT_SOURCE_FIRST,
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: Mapping[str, object] | str | None = None,
    target_language: str | None = None,
) -> bytes:
    order = _normalize_bilingual_layout_order(order)
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)
    package = DocxPackage(raw_bytes)
    stories = _build_story_parts(
        package,
        document_parse_mode=document_parse_mode,
        document_parse_options=document_parse_options,
    )
    numbering_schema = _build_numbering_schema(package)
    source_workspace = get_cached_docx_workspace(
        raw_bytes,
        document_parse_mode=document_parse_mode,
        document_parse_options=document_parse_options,
    )
    source_segments = source_workspace["segments"]
    math_placeholders_by_sentence_id = {
        str(segment["sentence_id"]): dict(segment.get("math_placeholders") or {})
        for segment in source_segments
        if segment.get("sentence_id")
    }
    segments_by_block = _group_segments_by_block(
        segments,
        math_placeholders_by_sentence_id,
        source_segments=source_segments,
    )
    block_counter = count(0)

    for story in stories:
        _export_bilingual_block_sequence(
            container=story.root,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
            segments_by_block=segments_by_block,
            order=order,
        )

    _localize_numbering_definitions(
        package,
        target_language=target_language,
        strategy=document_parse_options.get("docx_numbering_localization"),
    )
    if document_parse_options.get("clean_format"):
        _clean_story_formatting(stories)
    if not document_parse_options.get("preserve_hyperlinks", True):
        _strip_story_hyperlinks(stories)

    return _build_modified_docx(
        raw_bytes=raw_bytes,
        package=package,
        part_names={story.part_name for story in stories} | {"word/numbering.xml"},
    )


def export_translated_docx(
    raw_bytes: bytes,
    segments: Iterable[Any],
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: Mapping[str, object] | str | None = None,
    target_language: str | None = None,
) -> bytes:
    document_parse_mode = normalize_document_parse_mode(document_parse_mode)
    document_parse_options = normalize_document_parse_options(document_parse_options, document_parse_mode)
    package = DocxPackage(raw_bytes)
    stories = _build_story_parts(
        package,
        document_parse_mode=document_parse_mode,
        document_parse_options=document_parse_options,
    )
    numbering_schema = _build_numbering_schema(package)
    source_workspace = get_cached_docx_workspace(
        raw_bytes,
        document_parse_mode=document_parse_mode,
        document_parse_options=document_parse_options,
    )
    source_segments = source_workspace["segments"]
    math_placeholders_by_sentence_id = {
        str(segment["sentence_id"]): dict(segment.get("math_placeholders") or {})
        for segment in source_segments
        if segment.get("sentence_id")
    }
    segments_by_block = _group_segments_by_block(
        segments,
        math_placeholders_by_sentence_id,
        source_segments=source_segments,
    )
    block_counter = count(0)

    for story in stories:
        _export_block_sequence(
            container=story.root,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
            segments_by_block=segments_by_block,
        )

    _localize_numbering_definitions(
        package,
        target_language=target_language,
        strategy=document_parse_options.get("docx_numbering_localization"),
    )
    if document_parse_options.get("clean_format"):
        _clean_story_formatting(stories)
    if not document_parse_options.get("preserve_hyperlinks", True):
        _strip_story_hyperlinks(stories)

    return _build_modified_docx(
        raw_bytes=raw_bytes,
        package=package,
        part_names={story.part_name for story in stories} | {"word/numbering.xml"},
    )


def build_translated_docx_filename(filename: str) -> str:
    source_path = Path(filename or "document.docx")
    return f"{source_path.stem}_translated.docx"


def build_bilingual_docx_filename(filename: str, order: str = BILINGUAL_LAYOUT_SOURCE_FIRST) -> str:
    order = _normalize_bilingual_layout_order(order)
    source_path = Path(filename or "document.docx")
    suffix = "bilingual_source_first" if order == BILINGUAL_LAYOUT_SOURCE_FIRST else "bilingual_target_first"
    return f"{source_path.stem}_{suffix}.docx"


def _normalize_bilingual_layout_order(order: str) -> str:
    if order in {BILINGUAL_LAYOUT_SOURCE_FIRST, BILINGUAL_LAYOUT_TARGET_FIRST}:
        return order
    raise ValueError(f"Unsupported bilingual DOCX layout order: {order}")


def _group_segments_by_block(
    segments: Iterable[Any],
    math_placeholders_by_sentence_id: Mapping[str, dict[str, str]] | None = None,
    source_segments: Iterable[Mapping[str, Any]] | None = None,
) -> dict[BlockKey, list[ExportSegment]]:
    grouped: dict[BlockKey, list[ExportSegment]] = defaultdict(list)
    math_map = math_placeholders_by_sentence_id or {}
    source_segment_list = list(source_segments or [])
    source_segment_by_sentence_id = _build_source_segment_lookup_by_sentence_id(source_segment_list)
    source_segment_by_text_key = _build_unique_source_segment_lookup_by_text(source_segment_list)

    for segment in segments:
        block_type = str(_get_segment_value(segment, "block_type", "paragraph") or "paragraph")
        block_index = int(_get_segment_value(segment, "block_index", 0) or 0)
        row_index = _to_optional_int(_get_segment_value(segment, "row_index"))
        cell_index = _to_optional_int(_get_segment_value(segment, "cell_index"))
        sentence_id = str(_get_segment_value(segment, "sentence_id", "") or "")
        target_html = _get_segment_value(segment, "target_html")
        block_key = _resolve_export_segment_block_key(
            segment=segment,
            fallback=(block_type, block_index, row_index, cell_index),
            source_segment_by_sentence_id=source_segment_by_sentence_id,
            source_segment_by_text_key=source_segment_by_text_key,
        )

        grouped[block_key].append(
            ExportSegment(
                sentence_id=sentence_id,
                source_text=str(_get_segment_value(segment, "source_text", "") or ""),
                target_text=str(_get_segment_value(segment, "target_text", "") or ""),
                display_text=str(_get_segment_value(segment, "display_text", "") or ""),
                numbering_text=str(_get_segment_value(segment, "numbering_text", "") or ""),
                matched_source_text=str(_get_segment_value(segment, "matched_source_text", "") or ""),
                target_html=str(target_html) if target_html else None,
                math_placeholders=dict(math_map.get(sentence_id) or {}),
            )
        )

    if source_segment_list:
        return _order_segment_groups_by_source(grouped, source_segment_list)

    return grouped


def _build_source_segment_lookup_by_sentence_id(
    source_segments: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    if source_segments is None:
        return {}
    return {
        str(segment.get("sentence_id") or ""): segment
        for segment in source_segments
        if segment.get("sentence_id")
    }


def _build_unique_source_segment_lookup_by_text(
    source_segments: Iterable[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    if source_segments is None:
        return {}

    source_by_key: dict[str, Mapping[str, Any] | None] = {}
    for segment in source_segments:
        for text_key in _source_segment_text_keys(segment):
            if text_key not in source_by_key:
                source_by_key[text_key] = segment
            elif source_by_key[text_key] is not segment:
                source_by_key[text_key] = None

    return {
        text_key: segment
        for text_key, segment in source_by_key.items()
        if segment is not None
    }


def _resolve_export_segment_block_key(
    *,
    segment: Any,
    fallback: BlockKey,
    source_segment_by_sentence_id: Mapping[str, Mapping[str, Any]],
    source_segment_by_text_key: Mapping[str, Mapping[str, Any]],
) -> BlockKey:
    sentence_id = str(_get_segment_value(segment, "sentence_id", "") or "")
    source_segment = source_segment_by_sentence_id.get(sentence_id)
    if source_segment is None:
        for text_key in _segment_text_keys(
            _get_segment_value(segment, "source_text", ""),
            _get_segment_value(segment, "display_text", ""),
        ):
            source_segment = source_segment_by_text_key.get(text_key)
            if source_segment is not None:
                break

    if source_segment is None:
        return fallback

    return _source_segment_block_key(source_segment)


def _order_segment_groups_by_source(
    grouped: dict[BlockKey, list[ExportSegment]],
    source_segments: Iterable[Mapping[str, Any]],
) -> dict[BlockKey, list[ExportSegment]]:
    source_by_block: dict[BlockKey, list[Mapping[str, Any]]] = defaultdict(list)
    for segment in source_segments:
        block_key = _source_segment_block_key(segment)
        source_by_block[block_key].append(segment)

    ordered: dict[BlockKey, list[ExportSegment]] = {}
    for block_key, block_segments in grouped.items():
        source_block_segments = source_by_block.get(block_key)
        if not source_block_segments:
            ordered[block_key] = block_segments
            continue
        ordered[block_key] = _order_export_segments_for_source_block(block_segments, source_block_segments)
    return ordered


def _source_segment_block_key(segment: Mapping[str, Any]) -> BlockKey:
    block_type = str(segment.get("block_type") or "paragraph")
    block_index = int(segment.get("block_index") or 0)
    row_index = _to_optional_int(segment.get("row_index"))
    cell_index = _to_optional_int(segment.get("cell_index"))
    return (block_type, block_index, row_index, cell_index)


def _order_export_segments_for_source_block(
    block_segments: list[ExportSegment],
    source_segments: list[Mapping[str, Any]],
) -> list[ExportSegment]:
    used_indexes: set[int] = set()
    ordered: list[ExportSegment] = []

    for source_segment in source_segments:
        match_index = _find_export_segment_by_sentence_id(
            block_segments,
            source_segment,
            used_indexes,
        )
        if match_index is None:
            match_index = _find_export_segment_by_text(block_segments, source_segment, used_indexes)
        if match_index is None:
            continue
        used_indexes.add(match_index)
        ordered.append(block_segments[match_index])

    ordered.extend(
        segment
        for index, segment in enumerate(block_segments)
        if index not in used_indexes
    )
    return ordered


def _find_export_segment_by_sentence_id(
    block_segments: list[ExportSegment],
    source_segment: Mapping[str, Any],
    used_indexes: set[int],
) -> int | None:
    sentence_id = str(source_segment.get("sentence_id") or "")
    if not sentence_id:
        return None

    for index, segment in enumerate(block_segments):
        if index in used_indexes:
            continue
        if segment.sentence_id == sentence_id:
            return index
    return None


def _find_export_segment_by_text(
    block_segments: list[ExportSegment],
    source_segment: Mapping[str, Any],
    used_indexes: set[int],
) -> int | None:
    source_keys = _source_segment_text_keys(source_segment)
    if not source_keys:
        return None

    for index, segment in enumerate(block_segments):
        if index in used_indexes:
            continue
        if source_keys & _export_segment_text_keys(segment):
            return index
    return None


def _source_segment_text_keys(segment: Mapping[str, Any]) -> set[str]:
    return _segment_text_keys(
        segment.get("source_text"),
        segment.get("display_text"),
    )


def _export_segment_text_keys(segment: ExportSegment) -> set[str]:
    return _segment_text_keys(segment.source_text, segment.display_text)


def _segment_text_keys(*values: object) -> set[str]:
    keys: set[str] = set()
    for value in values:
        text = _normalize_segment_source_text(str(value or ""))
        if text:
            keys.add(text)
    return keys


def _export_bilingual_block_sequence(
    container: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    order: str,
    default_block_type: str = "paragraph",
    fixed_block_index: int | None = None,
    row_index: int | None = None,
    cell_index: int | None = None,
) -> None:
    for child in list(container):
        child_name = _local_name(child.tag)
        if child_name == "p":
            block_index = fixed_block_index if fixed_block_index is not None else next(block_counter)
            _export_bilingual_paragraph(
                parent=container,
                paragraph=child,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                block_index=block_index,
                block_type=default_block_type,
                row_index=row_index,
                cell_index=cell_index,
                segments_by_block=segments_by_block,
                order=order,
            )
            _export_bilingual_embedded_textboxes(
                node=child,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                order=order,
            )
            continue

        if child_name == "tbl":
            _export_bilingual_table(
                table=child,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                order=order,
            )
            continue

        if child_name == "sdt":
            content = child.find("w:sdtContent", NS)
            if content is not None:
                _export_bilingual_block_sequence(
                    container=content,
                    story=story,
                    block_counter=block_counter,
                    numbering_schema=numbering_schema,
                    segments_by_block=segments_by_block,
                    order=order,
                    default_block_type=default_block_type,
                    fixed_block_index=fixed_block_index,
                    row_index=row_index,
                    cell_index=cell_index,
                )
            continue

        if child_name in {"customXml", "ins", "moveFrom", "moveTo", "smartTag"}:
            _export_bilingual_block_sequence(
                container=child,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                order=order,
                default_block_type=default_block_type,
                fixed_block_index=fixed_block_index,
                row_index=row_index,
                cell_index=cell_index,
            )
            continue

        if child_name == "AlternateContent":
            preferred_branch = _select_preferred_alternate_content_branch(child)
            if preferred_branch is not None:
                _export_bilingual_block_sequence(
                    container=preferred_branch,
                    story=story,
                    block_counter=block_counter,
                    numbering_schema=numbering_schema,
                    segments_by_block=segments_by_block,
                    order=order,
                    default_block_type=default_block_type,
                    fixed_block_index=fixed_block_index,
                    row_index=row_index,
                    cell_index=cell_index,
                )


def _export_block_sequence(
    container: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
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
            numbering_schema=numbering_schema,
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
    numbering_schema: NumberingSchema,
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
            numbering_schema=numbering_schema,
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
            numbering_schema=numbering_schema,
            segments_by_block=segments_by_block,
        )


def _iter_block_nodes_with_parent(container: ET.Element):
    for child in list(container):
        child_name = _local_name(child.tag)
        if child_name in {"p", "tbl"}:
            yield container, child
            continue

        if child_name == "sdt":
            content = child.find("w:sdtContent", NS)
            if content is not None:
                yield from _iter_block_nodes_with_parent(content)
            continue

        if child_name in {"customXml", "ins", "moveFrom", "moveTo", "smartTag"}:
            yield from _iter_block_nodes_with_parent(child)
            continue

        if child_name == "AlternateContent":
            preferred_branch = _select_preferred_alternate_content_branch(child)
            if preferred_branch is not None:
                yield from _iter_block_nodes_with_parent(preferred_branch)


def _export_bilingual_table(
    table: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    order: str,
) -> None:
    block_index = next(block_counter)

    for row_index, row in enumerate(table.findall("./w:tr", NS)):
        for cell_index, cell in enumerate(row.findall("./w:tc", NS)):
            _export_bilingual_table_cell(
                cell=cell,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                block_index=block_index,
                row_index=row_index,
                cell_index=cell_index,
                order=order,
            )


def _export_table(
    table: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> None:
    block_index = next(block_counter)

    for row_index, row in enumerate(table.findall("./w:tr", NS)):
        for cell_index, cell in enumerate(row.findall("./w:tc", NS)):
            _export_table_cell(
                cell=cell,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                block_index=block_index,
                row_index=row_index,
                cell_index=cell_index,
            )


def _table_cell_text_length(tokens: list[TextToken]) -> int:
    return len(normalize_text("".join(token.display_text for token in tokens)))


def _table_cell_tokens_look_incomplete(tokens: list[TextToken]) -> bool:
    text = "".join(token.display_text for token in tokens).rstrip()
    if not text:
        return False
    return text[-1] not in CELL_SENTENCE_END_CHARS


def _should_merge_table_cell_paragraphs(
    current_tokens: list[TextToken],
    next_paragraph: CellParagraphTokens,
    numbering_schema: NumberingSchema,
) -> bool:
    if not current_tokens or not next_paragraph.tokens:
        return False
    if _resolve_paragraph_numbering_reference(next_paragraph.paragraph, numbering_schema) is not None:
        return False
    if not _table_cell_tokens_look_incomplete(current_tokens):
        return False

    next_length = _table_cell_text_length(next_paragraph.tokens)
    if next_length == 0 or next_length > CELL_NEXT_PARAGRAPH_MAX_CHARS:
        return False
    if next_length <= CELL_SHORT_PARAGRAPH_MAX_CHARS:
        return True

    return _table_cell_text_length(current_tokens) + next_length <= CELL_GROUP_MAX_CHARS


def _count_token_sentence_spans(tokens: list[TextToken]) -> int:
    _assign_token_offsets(tokens)
    display_text = "".join(token.display_text for token in tokens)
    if not display_text:
        return 0

    spans = split_sentence_spans(display_text)
    if not spans and normalize_text(display_text):
        spans = [_build_trimmed_span(display_text)]

    count = 0
    for span in spans:
        sentence_source = _normalize_segment_source_text(_collect_span_text(tokens, span, use_source=True))
        if sentence_source:
            count += 1
    return count


def _group_table_cell_paragraphs(
    paragraphs: list[CellParagraphTokens],
    numbering_schema: NumberingSchema,
) -> list[tuple[list[TextToken], int]]:
    grouped_paragraphs: list[tuple[list[TextToken], int]] = []
    current_tokens: list[TextToken] = []

    def flush_current_tokens() -> None:
        nonlocal current_tokens
        if not current_tokens:
            return
        grouped_paragraphs.append((current_tokens, _count_token_sentence_spans(current_tokens)))
        current_tokens = []

    for paragraph in paragraphs:
        if not paragraph.tokens:
            flush_current_tokens()
            continue

        paragraph_tokens = list(paragraph.tokens)
        if not current_tokens:
            current_tokens = paragraph_tokens
            continue

        if _should_merge_table_cell_paragraphs(current_tokens, paragraph, numbering_schema):
            current_tokens.append(
                TextToken(
                    display_text="\n",
                    source_text=CELL_PARAGRAPH_BREAK_SENTINEL,
                )
            )
            current_tokens.extend(paragraph_tokens)
            continue

        flush_current_tokens()
        current_tokens = paragraph_tokens

    flush_current_tokens()

    return grouped_paragraphs


def _group_table_cell_paragraph_groups(
    paragraphs: list[CellParagraphTokens],
    numbering_schema: NumberingSchema,
) -> list[tuple[list[CellParagraphTokens], int]]:
    grouped_paragraphs: list[tuple[list[CellParagraphTokens], int]] = []
    current_paragraphs: list[CellParagraphTokens] = []
    current_tokens: list[TextToken] = []

    def flush_current_paragraphs() -> None:
        nonlocal current_paragraphs, current_tokens
        if not current_paragraphs:
            return
        grouped_paragraphs.append((current_paragraphs, _count_token_sentence_spans(current_tokens)))
        current_paragraphs = []
        current_tokens = []

    for paragraph in paragraphs:
        if not paragraph.tokens:
            flush_current_paragraphs()
            continue

        paragraph_tokens = list(paragraph.tokens)
        if not current_paragraphs:
            current_paragraphs = [paragraph]
            current_tokens = paragraph_tokens
            continue

        if _should_merge_table_cell_paragraphs(current_tokens, paragraph, numbering_schema):
            current_tokens.append(
                TextToken(
                    display_text="\n",
                    source_text=CELL_PARAGRAPH_BREAK_SENTINEL,
                )
            )
            current_tokens.extend(paragraph_tokens)
            current_paragraphs.append(paragraph)
            continue

        flush_current_paragraphs()
        current_paragraphs = [paragraph]
        current_tokens = paragraph_tokens

    flush_current_paragraphs()

    return grouped_paragraphs


def _export_bilingual_table_cell(
    cell: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    block_index: int,
    row_index: int,
    cell_index: int,
    order: str,
) -> None:
    cell_segments = segments_by_block.get(
        (_resolve_segment_block_type(story.kind, "table_cell"), block_index, row_index, cell_index),
        [],
    )
    segment_cursor = 0
    paragraph_buffer: list[CellParagraphTokens] = []

    def flush_paragraphs() -> None:
        nonlocal paragraph_buffer, segment_cursor
        if not paragraph_buffer:
            return

        for paragraph_group, sentence_count in _group_table_cell_paragraph_groups(paragraph_buffer, numbering_schema):
            if sentence_count == 0:
                continue

            group_segments = cell_segments[segment_cursor : segment_cursor + sentence_count]
            segment_cursor += sentence_count
            if not group_segments:
                continue

            target_paragraphs = [_clone_bilingual_paragraph(item.paragraph) for item in paragraph_group]
            target_tokens = _collect_cell_group_tokens(
                paragraphs=target_paragraphs,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
            )
            _replace_block_tokens(
                tokens=target_tokens,
                segments=group_segments,
                keep_source_when_empty=False,
            )
            _insert_cloned_table_cell_paragraphs(
                cell=cell,
                paragraph_group=paragraph_group,
                target_paragraphs=target_paragraphs,
                order=order,
            )

        paragraph_buffer = []

    for parent, block in _iter_block_nodes_with_parent(cell):
        block_name = _local_name(block.tag)
        if block_name == "p":
            paragraph_buffer.append(
                CellParagraphTokens(
                    paragraph=block,
                    tokens=_collect_inline_tokens(
                        node=block,
                        story=story,
                        block_counter=block_counter,
                        numbering_schema=numbering_schema,
                        segments_by_block=segments_by_block,
                        math_placeholder_counter=[0],
                        process_embedded_textboxes=False,
                    ),
                    parent=parent,
                )
            )
            _export_bilingual_embedded_textboxes(
                node=block,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                order=order,
            )
            continue

        if block_name == "tbl":
            flush_paragraphs()
            _export_bilingual_table(
                table=block,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                order=order,
            )

    flush_paragraphs()


def _insert_cloned_table_cell_paragraphs(
    cell: ET.Element,
    paragraph_group: list[CellParagraphTokens],
    target_paragraphs: list[ET.Element],
    order: str,
) -> None:
    if not paragraph_group or not target_paragraphs:
        return

    parents = [
        item.parent if item.parent is not None else cell
        for item in paragraph_group
    ]
    first_parent = parents[0]
    if all(parent is first_parent for parent in parents):
        _insert_cloned_blocks(
            parent=first_parent,
            anchors=[item.paragraph for item in paragraph_group],
            clones=target_paragraphs,
            order=order,
        )
        return

    for item, clone, parent in zip(paragraph_group, target_paragraphs, parents, strict=False):
        _insert_cloned_blocks(
            parent=parent,
            anchors=[item.paragraph],
            clones=[clone],
            order=order,
        )


def _export_table_cell(
    cell: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    block_index: int,
    row_index: int,
    cell_index: int,
) -> None:
    cell_segments = segments_by_block.get(
        (_resolve_segment_block_type(story.kind, "table_cell"), block_index, row_index, cell_index),
        [],
    )
    segment_cursor = 0
    paragraph_buffer: list[CellParagraphTokens] = []

    def flush_paragraphs() -> None:
        nonlocal paragraph_buffer, segment_cursor
        if not paragraph_buffer:
            return

        for token_group, sentence_count in _group_table_cell_paragraphs(paragraph_buffer, numbering_schema):
            if sentence_count == 0:
                continue
            _replace_block_tokens(
                tokens=token_group,
                segments=cell_segments[segment_cursor : segment_cursor + sentence_count],
            )
            segment_cursor += sentence_count

        paragraph_buffer = []

    for block in _iter_block_nodes(cell):
        block_name = _local_name(block.tag)
        if block_name == "p":
            paragraph_buffer.append(
                CellParagraphTokens(
                    paragraph=block,
                    tokens=_collect_inline_tokens(
                        node=block,
                        story=story,
                        block_counter=block_counter,
                        numbering_schema=numbering_schema,
                        segments_by_block=segments_by_block,
                        math_placeholder_counter=[0],
                    ),
                )
            )
            continue

        if block_name == "tbl":
            flush_paragraphs()
            _export_table(
                table=block,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
            )

    flush_paragraphs()


def _export_paragraph(
    paragraph: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
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
        numbering_schema=numbering_schema,
        segments_by_block=segments_by_block,
        math_placeholder_counter=[0],
    )

    _replace_block_tokens(
        tokens=tokens,
        segments=segments_by_block.get(
            (_resolve_segment_block_type(story.kind, block_type), block_index, row_index, cell_index),
            [],
        ),
    )


def _export_bilingual_paragraph(
    parent: ET.Element,
    paragraph: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    block_index: int,
    block_type: str,
    row_index: int | None,
    cell_index: int | None,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    order: str,
) -> None:
    tokens = _collect_inline_tokens(
        node=paragraph,
        story=story,
        block_counter=block_counter,
        numbering_schema=numbering_schema,
        segments_by_block=segments_by_block,
        math_placeholder_counter=[0],
        process_embedded_textboxes=False,
    )
    sentence_count = _count_token_sentence_spans(tokens)
    if sentence_count == 0:
        return

    block_segments = segments_by_block.get(
        (_resolve_segment_block_type(story.kind, block_type), block_index, row_index, cell_index),
        [],
    )
    if not block_segments:
        return

    target_paragraph = _clone_bilingual_paragraph(paragraph)
    target_tokens = _collect_inline_tokens(
        node=target_paragraph,
        story=story,
        block_counter=block_counter,
        numbering_schema=numbering_schema,
        segments_by_block=segments_by_block,
        math_placeholder_counter=[0],
        process_embedded_textboxes=False,
    )
    _replace_block_tokens(
        tokens=target_tokens,
        segments=block_segments[:sentence_count],
        keep_source_when_empty=False,
    )
    _insert_cloned_blocks(
        parent=parent,
        anchors=[paragraph],
        clones=[target_paragraph],
        order=order,
    )


def _collect_inline_tokens(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    current_run: ET.Element | None = None,
    current_run_container: ET.Element | None = None,
    parent_element: ET.Element | None = None,
    math_placeholder_counter: list[int] | None = None,
    inside_hyperlink: bool = False,
    current_hyperlink: object | None = None,
    field_stack: list[ExportTrackedField] | None = None,
    process_embedded_textboxes: bool = True,
) -> list[TextToken]:
    placeholder_counter = math_placeholder_counter if math_placeholder_counter is not None else [0]
    active_field_stack = field_stack if field_stack is not None else []
    if node.tag in OMML_ATOMIC_TAGS:
        placeholder_counter[0] += 1
        placeholder = MATH_PLACEHOLDER_TEMPLATE.format(index=placeholder_counter[0])
        return [
            TextToken(
                display_text=placeholder,
                source_text=placeholder,
                anchor_element=node,
                container_element=parent_element,
                is_math=True,
            )
        ]

    node_name = _local_name(node.tag)
    if node_name in {"pPr", "rPr", "tblPr", "tblGrid", "trPr", "tcPr", "sectPr"}:
        return []

    if node_name == "AlternateContent":
        preferred_branch = _select_preferred_alternate_content_branch(node)
        if preferred_branch is None:
            return []
        return _collect_inline_tokens(
            node=preferred_branch,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
            segments_by_block=segments_by_block,
            current_run=current_run,
            current_run_container=current_run_container,
            parent_element=parent_element,
            math_placeholder_counter=placeholder_counter,
            inside_hyperlink=inside_hyperlink,
            current_hyperlink=current_hyperlink,
            field_stack=active_field_stack,
            process_embedded_textboxes=process_embedded_textboxes,
        )

    if node.tag == _qn("w", "fldSimple") and story.parse_options.get("preserve_hyperlinks", True):
        instruction = node.get(_qn("w", "instr"), "")
        if _resolve_internal_reference_field_target(instruction):
            inside_hyperlink = True
            current_hyperlink = node

    if node.tag == _qn("w", "hyperlink") and story.parse_options.get("preserve_hyperlinks", True):
        inside_hyperlink = True
        current_hyperlink = node

    if node.tag == _qn("w", "r"):
        current_run = node
        current_run_container = parent_element

    if node.tag == _qn("w", "fldChar"):
        _update_export_field_state(node, active_field_stack)
        return []

    if node_name == "instrText":
        if active_field_stack and active_field_stack[-1].collecting_instruction:
            active_field_stack[-1].instruction_parts.append(node.text or "")
        return []

    field_hyperlink = _current_export_field_hyperlink(active_field_stack)
    if field_hyperlink is not None and story.parse_options.get("preserve_hyperlinks", True):
        inside_hyperlink = True
        current_hyperlink = field_hyperlink

    if node.tag == _qn("w", "t"):
        text_value = node.text or ""
        return [
            TextToken(
                display_text=text_value,
                source_text=text_value,
                element=node,
                run_element=current_run,
                anchor_element=current_run if current_run is not None else node,
                container_element=current_run_container if current_run_container is not None else parent_element,
                original_text=text_value,
                is_hyperlink=inside_hyperlink,
                hyperlink_element=current_hyperlink,
            )
        ]

    if node.tag == _qn("w", "tab"):
        return [TextToken(display_text="\t", source_text="\t")]

    if node.tag in {_qn("w", "br"), _qn("w", "cr")}:
        return [TextToken(display_text="\n", source_text="\n")]

    if node.tag == _qn("w", "noBreakHyphen"):
        return [TextToken(display_text="-", source_text="-")]

    if node.tag == _qn("w", "sym"):
        symbol_text = _decode_symbol(node)
        if not symbol_text:
            return []
        return [TextToken(display_text=symbol_text, source_text=symbol_text)]

    if node.tag in {_qn("w", "footnoteReference"), _qn("w", "endnoteReference")}:
        note_id = node.get(_qn("w", "id"), "")
        marker = f"[{note_id}]" if note_id else "[*]"
        return [TextToken(display_text=marker, source_text=" " * len(marker))]

    if node.tag in {_qn("w", "drawing"), _qn("w", "pict")}:
        if not process_embedded_textboxes:
            return []
        _export_embedded_textboxes(
            node=node,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
            segments_by_block=segments_by_block,
        )
        return []

    tokens: list[TextToken] = []
    for child in list(node):
        tokens.extend(
            _collect_inline_tokens(
                node=child,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                current_run=current_run,
                current_run_container=current_run_container,
                parent_element=node,
                math_placeholder_counter=placeholder_counter,
                inside_hyperlink=inside_hyperlink,
                current_hyperlink=current_hyperlink,
                field_stack=active_field_stack,
                process_embedded_textboxes=process_embedded_textboxes,
            )
        )

    return tokens


def _update_export_field_state(
    node: ET.Element,
    field_stack: list[ExportTrackedField],
) -> None:
    field_type = node.get(_qn("w", "fldCharType"))
    if field_type == "begin":
        field_stack.append(ExportTrackedField())
        return

    if not field_stack:
        return

    current_field = field_stack[-1]
    if field_type == "separate":
        current_field.collecting_instruction = False
        if _resolve_internal_reference_field_target("".join(current_field.instruction_parts)):
            current_field.hyperlink_key = current_field
        return

    if field_type == "end":
        field_stack.pop()


def _current_export_field_hyperlink(field_stack: list[ExportTrackedField]) -> object | None:
    for tracked_field in reversed(field_stack):
        if not tracked_field.collecting_instruction and tracked_field.hyperlink_key is not None:
            return tracked_field.hyperlink_key
    return None


def _export_bilingual_embedded_textboxes(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    order: str,
) -> None:
    for embedded_node in _iter_embedded_object_nodes_for_export(node):
        _export_bilingual_embedded_textbox_object(
            node=embedded_node,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
            segments_by_block=segments_by_block,
            order=order,
        )


def _export_bilingual_embedded_textbox_object(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    order: str,
) -> None:
    textbox_contents = node.findall(".//w:txbxContent", NS)
    if textbox_contents:
        for textbox_content in textbox_contents:
            _export_bilingual_block_sequence(
                container=textbox_content,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                order=order,
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

    block_segments = segments_by_block.get(
        (_resolve_segment_block_type(story.kind, "textbox"), next(block_counter), None, None),
        [],
    )
    if not block_segments:
        return

    _replace_block_tokens(
        tokens=tokens,
        segments=_build_inline_bilingual_segments(block_segments, order),
        keep_source_when_empty=False,
    )


def _export_embedded_textboxes(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> None:
    for embedded_node in _iter_embedded_object_nodes_for_export(node):
        _export_embedded_textbox_object(
            node=embedded_node,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
            segments_by_block=segments_by_block,
        )


def _export_embedded_textbox_object(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> None:
    textbox_contents = node.findall(".//w:txbxContent", NS)
    if textbox_contents:
        for textbox_content in textbox_contents:
            _export_block_sequence(
                container=textbox_content,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
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


def _iter_embedded_object_nodes_for_export(node: ET.Element):
    node_name = _local_name(node.tag)
    if node_name in {"pPr", "rPr", "tblPr", "tblGrid", "trPr", "tcPr", "sectPr"}:
        return

    if node_name == "AlternateContent":
        preferred_branch = _select_preferred_alternate_content_branch(node)
        if preferred_branch is not None:
            yield from _iter_embedded_object_nodes_for_export(preferred_branch)
        return

    if node.tag in {_qn("w", "drawing"), _qn("w", "pict")}:
        yield node
        return

    for child in list(node):
        yield from _iter_embedded_object_nodes_for_export(child)


def _replace_block_tokens(
    tokens: list[TextToken],
    segments: list[ExportSegment],
    keep_source_when_empty: bool = True,
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
    previous_replacement = ""
    previous_span: SentenceSpan | None = None
    for span in spans:
        sentence_source = _normalize_segment_source_text(_collect_span_text(tokens, span, use_source=True))
        if not sentence_source:
            continue

        if segment_index >= len(segments):
            break

        segment = segments[segment_index]
        segment_index += 1
        replacement = (
            segment.target_text
            if _is_target_placeholder(segment.target_text)
            else strip_automatic_numbering_prefix(
                segment.target_text,
                source_text=segment.source_text,
                display_text=segment.display_text,
                numbering_text=segment.numbering_text,
                reference_texts=[segment.matched_source_text],
            )
        )
        if previous_span is not None:
            boundary_text = display_text[previous_span.end:span.start]
            replacement = _normalize_adjacent_english_target_boundary(
                previous_replacement=previous_replacement,
                current_replacement=replacement,
                boundary_text=boundary_text,
            )
        if not normalize_text(replacement):
            if _is_target_placeholder(segment.target_text) or not keep_source_when_empty:
                _queue_sentence_replacement(
                    tokens,
                    span,
                    replacement if _is_target_placeholder(segment.target_text) else "",
                )
            previous_replacement = replacement if _is_target_placeholder(segment.target_text) else ""
            previous_span = span
            continue

        expected_math_placeholders = _extract_math_placeholders_from_tokens(tokens, span)

        # 检查是否有自定义格式（target_html 包含格式标签）
        has_custom_format = _has_format_tags(segment.target_html)

        if expected_math_placeholders:
            _queue_math_sentence_replacement(
                tokens=tokens,
                span=span,
                replacement=replacement,
                expected_math_placeholders=expected_math_placeholders,
            )
        elif has_custom_format:
            # 使用带格式的替换
            _queue_formatted_sentence_replacement(tokens, span, segment.target_html)
        else:
            _queue_sentence_replacement(tokens, span, replacement)

        previous_replacement = replacement
        previous_span = span

    _apply_token_edits(tokens)


def _is_target_placeholder(text: str | None) -> bool:
    return bool(text) and not normalize_text(text or "")


def _collect_cell_group_tokens(
    paragraphs: list[ET.Element],
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> list[TextToken]:
    tokens: list[TextToken] = []
    for index, paragraph in enumerate(paragraphs):
        if index > 0:
            tokens.append(
                TextToken(
                    display_text="\n",
                    source_text=CELL_PARAGRAPH_BREAK_SENTINEL,
                )
            )
        tokens.extend(
            _collect_inline_tokens(
                node=paragraph,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                math_placeholder_counter=[0],
                process_embedded_textboxes=False,
            )
        )
    return tokens


def _clone_bilingual_paragraph(paragraph: ET.Element) -> ET.Element:
    clone = deepcopy(paragraph)
    _remove_paragraph_section_properties(clone)
    return clone


def _remove_paragraph_section_properties(paragraph: ET.Element) -> None:
    paragraph_properties = paragraph.find("w:pPr", NS)
    if paragraph_properties is None:
        return
    for section_properties in list(paragraph_properties.findall("w:sectPr", NS)):
        paragraph_properties.remove(section_properties)


def _insert_cloned_blocks(
    parent: ET.Element,
    anchors: list[ET.Element],
    clones: list[ET.Element],
    order: str,
) -> None:
    if not anchors or not clones:
        return

    children = list(parent)
    if order == BILINGUAL_LAYOUT_TARGET_FIRST:
        insert_index = children.index(anchors[0])
    else:
        insert_index = children.index(anchors[-1]) + 1

    for offset, clone in enumerate(clones):
        parent.insert(insert_index + offset, clone)


def _build_inline_bilingual_segments(
    segments: list[ExportSegment],
    order: str,
) -> list[ExportSegment]:
    bilingual_segments: list[ExportSegment] = []
    for segment in segments:
        source_text = segment.source_text
        target_text = segment.target_text
        has_target = bool(normalize_text(target_text))
        if order == BILINGUAL_LAYOUT_TARGET_FIRST:
            replacement = f"{target_text}\n{source_text}" if has_target else f"\n{source_text}"
        else:
            replacement = f"{source_text}\n{target_text}" if has_target else f"{source_text}\n"
        bilingual_segments.append(
            ExportSegment(
                sentence_id=segment.sentence_id,
                source_text=segment.source_text,
                target_text=replacement,
                display_text=segment.display_text,
                numbering_text=segment.numbering_text,
                matched_source_text=segment.matched_source_text,
                target_html=None,
                math_placeholders=segment.math_placeholders,
            )
        )
    return bilingual_segments


def _normalize_adjacent_english_target_boundary(
    previous_replacement: str,
    current_replacement: str,
    boundary_text: str,
) -> str:
    if not _should_insert_english_boundary_space(previous_replacement, current_replacement, boundary_text):
        return current_replacement
    return f" {current_replacement}"


def _should_insert_english_boundary_space(
    previous_replacement: str,
    current_replacement: str,
    boundary_text: str,
) -> bool:
    if not previous_replacement or not current_replacement:
        return False
    if previous_replacement[-1].isspace() or current_replacement[0].isspace():
        return False
    if any(char.isspace() for char in boundary_text):
        return False
    return bool(
        ENGLISH_BOUNDARY_TRAILING_RE.search(previous_replacement)
        and ENGLISH_WORD_LEADING_RE.match(current_replacement)
    )


def _extract_math_placeholders_from_tokens(
    tokens: list[TextToken],
    span: SentenceSpan,
) -> list[str]:
    return [
        token.source_text
        for token in tokens
        if token.is_math and token.start < span.end and token.end > span.start
    ]


def _queue_math_sentence_replacement(
    tokens: list[TextToken],
    span: SentenceSpan,
    replacement: str,
    expected_math_placeholders: list[str],
) -> None:
    text_parts = _split_replacement_around_math_placeholders(replacement, expected_math_placeholders)
    if text_parts is None:
        raise ValueError("导出失败：译文中的数学公式占位符顺序或数量与原文不一致。")

    sentence_tokens = [
        token
        for token in tokens
        if token.start < span.end and token.end > span.start
    ]
    math_tokens = [token for token in sentence_tokens if token.is_math]
    if len(math_tokens) != len(expected_math_placeholders):
        raise ValueError("导出失败：段落中的数学公式结构与句段映射不一致。")

    for index, replacement_text in enumerate(text_parts):
        region_start = span.start if index == 0 else math_tokens[index - 1].end
        region_end = span.end if index == len(math_tokens) else math_tokens[index].start
        before_token = math_tokens[index - 1] if index > 0 else None
        after_token = math_tokens[index] if index < len(math_tokens) else _find_first_token_starting_at_or_after(tokens, span.end)
        _queue_text_region_replacement(
            tokens=tokens,
            region_start=region_start,
            region_end=region_end,
            replacement_text=replacement_text,
            before_token=before_token,
            after_token=after_token,
        )


def _split_replacement_around_math_placeholders(
    replacement: str,
    expected_math_placeholders: list[str],
) -> list[str] | None:
    matches = list(MATH_PLACEHOLDER_RE.finditer(replacement))
    actual_placeholders = [match.group(0) for match in matches]
    if actual_placeholders != expected_math_placeholders:
        return None

    text_parts: list[str] = []
    cursor = 0
    for match in matches:
        text_parts.append(replacement[cursor:match.start()])
        cursor = match.end()
    text_parts.append(replacement[cursor:])
    return text_parts


def _queue_text_region_replacement(
    tokens: list[TextToken],
    region_start: int,
    region_end: int,
    replacement_text: str,
    before_token: TextToken | None,
    after_token: TextToken | None,
) -> None:
    writable_overlaps: list[tuple[TextToken, int, int]] = []
    for token in tokens:
        if token.element is None:
            continue
        overlap_start = max(region_start, token.start)
        overlap_end = min(region_end, token.end)
        if overlap_end <= overlap_start:
            continue
        writable_overlaps.append((token, overlap_start - token.start, overlap_end - token.start))

    if writable_overlaps:
        _queue_text_range_edit(writable_overlaps, replacement_text)
        return

    if not replacement_text:
        return

    _insert_text_run_between_tokens(replacement_text, before_token=before_token, after_token=after_token)


def _queue_text_range_edit(
    writable_overlaps: list[tuple[TextToken, int, int]],
    replacement_text: str,
) -> None:
    if not writable_overlaps:
        return

    replacement_index = 0
    for index, (token, _, _) in enumerate(writable_overlaps):
        if token.is_hyperlink:
            replacement_index = index
            break

    for index, (token, local_start, local_end) in enumerate(writable_overlaps):
        if index == replacement_index:
            token.edits.append((local_start, local_end, replacement_text))
            token.apply_export_font = bool(replacement_text)
        else:
            token.edits.append((local_start, local_end, ""))


def _insert_text_run_between_tokens(
    text: str,
    before_token: TextToken | None,
    after_token: TextToken | None,
) -> None:
    if not text:
        return

    if after_token is not None and after_token.container_element is not None and after_token.anchor_element is not None:
        parent = after_token.container_element
        index = list(parent).index(after_token.anchor_element)
        reference_run = _pick_reference_run(before_token, after_token)
        parent.insert(index, _build_inserted_word_run(text, reference_run))
        return

    if before_token is not None and before_token.container_element is not None and before_token.anchor_element is not None:
        parent = before_token.container_element
        index = list(parent).index(before_token.anchor_element) + 1
        reference_run = _pick_reference_run(before_token, after_token)
        parent.insert(index, _build_inserted_word_run(text, reference_run))


def _pick_reference_run(
    before_token: TextToken | None,
    after_token: TextToken | None,
) -> ET.Element | None:
    for token in (before_token, after_token):
        if token is not None and token.run_element is not None:
            return token.run_element
    return None


def _build_inserted_word_run(text: str, reference_run: ET.Element | None) -> ET.Element:
    text = _sanitize_xml_text(text)
    if reference_run is not None and _namespace_uri(reference_run.tag) == NS["w"]:
        run_element = deepcopy(reference_run)
        for child in list(run_element):
            if _local_name(child.tag) != "rPr":
                run_element.remove(child)
    else:
        run_element = ET.Element(_qn("w", "r"))

    text_element = ET.Element(_qn("w", "t"))
    text_element.text = text
    if _needs_space_preserve(text):
        text_element.set(XML_SPACE_ATTR, "preserve")
    run_element.append(text_element)
    _apply_export_font(run_element)
    return run_element


def _find_first_token_starting_at_or_after(tokens: list[TextToken], position: int) -> TextToken | None:
    for token in tokens:
        if token.start >= position and token.anchor_element is not None and token.container_element is not None:
            return token
    return None


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


def _queue_formatted_sentence_replacement(
    tokens: list[TextToken],
    span: SentenceSpan,
    target_html: str,
) -> None:
    """使用带格式的 HTML 替换句子"""
    # 解析 HTML 获取格式化片段
    fragments = _parse_formatted_html(target_html)
    if not fragments:
        return

    # 找到可写入的 token
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

    # 清空所有重叠 token 的文本
    first_token, first_start, first_end = writable_overlaps[0]
    for token, local_start, local_end in writable_overlaps:
        token.edits.append((local_start, local_end, ""))

    # 在第一个 token 位置插入格式化的 runs
    if first_token.run_element is not None and first_token.container_element is not None:
        parent = first_token.container_element
        anchor = first_token.anchor_element or first_token.run_element
        insert_index = list(parent).index(anchor)

        # 为每个格式化片段创建一个 run
        for i, fragment in enumerate(fragments):
            if not fragment.text:
                continue
            run = _build_formatted_word_run(fragment, first_token.run_element)
            parent.insert(insert_index + i, run)


def _build_formatted_word_run(
    fragment: FormattedTextFragment,
    reference_run: ET.Element | None,
) -> ET.Element:
    """根据格式化片段构建 Word run 元素"""
    # 复制参考 run 或创建新的
    if reference_run is not None and _namespace_uri(reference_run.tag) == NS["w"]:
        run_element = deepcopy(reference_run)
        # 移除非 rPr 的子元素
        for child in list(run_element):
            if _local_name(child.tag) != "rPr":
                run_element.remove(child)
    else:
        run_element = ET.Element(_qn("w", "r"))

    # 获取或创建 rPr（run properties）
    run_properties = run_element.find("w:rPr", NS)
    if run_properties is None:
        run_properties = ET.Element(_qn("w", "rPr"))
        run_element.insert(0, run_properties)

    # 应用格式
    if fragment.bold:
        _set_run_property(run_properties, "b")
    if fragment.italic:
        _set_run_property(run_properties, "i")
    if fragment.underline:
        _set_run_underline(run_properties)
    if fragment.strike:
        _set_run_property(run_properties, "strike")
    if fragment.subscript:
        _set_run_vertical_align(run_properties, "subscript")
    if fragment.superscript:
        _set_run_vertical_align(run_properties, "superscript")

    # 创建文本元素
    fragment_text = _sanitize_xml_text(fragment.text)
    text_element = ET.Element(_qn("w", "t"))
    text_element.text = fragment_text
    if _needs_space_preserve(fragment_text):
        text_element.set(XML_SPACE_ATTR, "preserve")
    run_element.append(text_element)

    # 应用导出字体
    _apply_export_font(run_element)

    return run_element


def _set_run_property(run_properties: ET.Element, prop_name: str) -> None:
    """设置 run 属性（如 bold, italic, strike）"""
    prop = run_properties.find(f"w:{prop_name}", NS)
    if prop is None:
        prop = ET.Element(_qn("w", prop_name))
        run_properties.append(prop)
    # 确保属性启用（移除 val="false" 如果存在）
    prop.attrib.pop(_qn("w", "val"), None)


def _set_run_underline(run_properties: ET.Element) -> None:
    """设置下划线"""
    underline = run_properties.find("w:u", NS)
    if underline is None:
        underline = ET.Element(_qn("w", "u"))
        run_properties.append(underline)
    underline.set(_qn("w", "val"), "single")


def _set_run_vertical_align(run_properties: ET.Element, align_type: str) -> None:
    """设置垂直对齐（上标/下标）"""
    vert_align = run_properties.find("w:vertAlign", NS)
    if vert_align is None:
        vert_align = ET.Element(_qn("w", "vertAlign"))
        run_properties.append(vert_align)
    vert_align.set(_qn("w", "val"), align_type)


def _queue_sentence_replacement(
    tokens: list[TextToken],
    span: SentenceSpan,
    replacement: str,
) -> None:
    if _queue_sentence_replacement_preserving_hyperlink_scope(tokens, span, replacement):
        return

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

    _queue_text_range_edit(writable_overlaps, replacement)


@dataclass(frozen=True)
class _HyperlinkReplacementGroup:
    start: int
    end: int
    text: str
    first_token: TextToken
    last_token: TextToken


def _queue_sentence_replacement_preserving_hyperlink_scope(
    tokens: list[TextToken],
    span: SentenceSpan,
    replacement: str,
) -> bool:
    hyperlink_groups = _collect_sentence_hyperlink_groups(tokens, span)
    if not hyperlink_groups:
        return False

    matches = _match_hyperlink_texts_in_replacement(hyperlink_groups, replacement)
    if matches is None:
        return False

    source_cursor = span.start
    replacement_cursor = 0
    previous_token: TextToken | None = None

    for group, (match_start, match_end) in zip(hyperlink_groups, matches, strict=False):
        _queue_text_region_replacement(
            tokens=tokens,
            region_start=source_cursor,
            region_end=group.start,
            replacement_text=replacement[replacement_cursor:match_start],
            before_token=previous_token,
            after_token=group.first_token,
        )
        _queue_text_region_replacement(
            tokens=tokens,
            region_start=group.start,
            region_end=group.end,
            replacement_text=replacement[match_start:match_end],
            before_token=None,
            after_token=group.first_token,
        )
        source_cursor = group.end
        replacement_cursor = match_end
        previous_token = group.last_token

    _queue_text_region_replacement(
        tokens=tokens,
        region_start=source_cursor,
        region_end=span.end,
        replacement_text=replacement[replacement_cursor:],
        before_token=previous_token,
        after_token=_find_first_token_starting_at_or_after(tokens, span.end),
    )
    return True


def _collect_sentence_hyperlink_groups(
    tokens: list[TextToken],
    span: SentenceSpan,
) -> list[_HyperlinkReplacementGroup]:
    groups: list[_HyperlinkReplacementGroup] = []
    current_element: object | None = None
    current_tokens: list[TextToken] = []
    current_text_parts: list[str] = []
    current_start = 0
    current_end = 0

    def flush_current_group() -> None:
        nonlocal current_element, current_tokens, current_text_parts, current_start, current_end
        if current_element is not None and current_tokens:
            linked_text = "".join(current_text_parts)
            if linked_text:
                groups.append(
                    _HyperlinkReplacementGroup(
                        start=current_start,
                        end=current_end,
                        text=linked_text,
                        first_token=current_tokens[0],
                        last_token=current_tokens[-1],
                    )
                )
        current_element = None
        current_tokens = []
        current_text_parts = []
        current_start = 0
        current_end = 0

    for token in tokens:
        overlap_start = max(span.start, token.start)
        overlap_end = min(span.end, token.end)
        if overlap_end <= overlap_start:
            continue

        hyperlink_element = token.hyperlink_element if token.is_hyperlink else None
        if hyperlink_element is None:
            flush_current_group()
            continue

        local_start = overlap_start - token.start
        local_end = overlap_end - token.start
        token_text = token.display_text[local_start:local_end]
        if hyperlink_element is not current_element:
            flush_current_group()
            current_element = hyperlink_element
            current_start = overlap_start

        current_tokens.append(token)
        current_text_parts.append(token_text)
        current_end = overlap_end

    flush_current_group()
    return groups


def _match_hyperlink_texts_in_replacement(
    groups: list[_HyperlinkReplacementGroup],
    replacement: str,
) -> list[tuple[int, int]] | None:
    matches: list[tuple[int, int]] = []
    cursor = 0
    for group in groups:
        linked_text = group.text
        if not linked_text:
            return None
        match_start = replacement.find(linked_text, cursor)
        if match_start < 0:
            return None
        match_end = match_start + len(linked_text)
        matches.append((match_start, match_end))
        cursor = match_end
    return matches


def _apply_token_edits(tokens: list[TextToken]) -> None:
    for token in tokens:
        if token.element is None or not token.edits:
            continue

        text_value = token.original_text
        for start, end, replacement in sorted(token.edits, key=lambda item: item[0], reverse=True):
            text_value = f"{text_value[:start]}{replacement}{text_value[end:]}"

        text_value = _sanitize_xml_text(text_value)
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


def _localize_numbering_definitions(
    package: DocxPackage,
    *,
    target_language: str | None = None,
    strategy: object = None,
) -> None:
    numbering_root = package.read_xml("word/numbering.xml")
    if numbering_root is None:
        return

    for level in numbering_root.findall(".//w:lvl", NS):
        localized_definition = _build_localized_numbering_definition(
            level,
            target_language=target_language,
            strategy=strategy,
        )
        if localized_definition is None:
            continue

        num_fmt_value, lvl_text_value, suffix_value = localized_definition
        _set_level_child_value(level, "numFmt", num_fmt_value)
        _set_level_child_value(level, "lvlText", lvl_text_value)
        _set_level_child_value(level, "suff", suffix_value)
        _apply_numbering_level_font(level)


def _build_localized_numbering_definition(
    level: ET.Element,
    *,
    target_language: str | None = None,
    strategy: object = None,
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

    return build_localized_docx_numbering_definition(
        num_fmt=num_fmt_value,
        lvl_text=lvl_text_value,
        suffix=suffix_value,
        target_language=target_language,
        strategy=strategy,
    )

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

            original_xml = source_archive.read(normalized_name)
            _register_namespaces(original_xml)
            modified_xml[normalized_name] = _serialize_xml(root, original_xml)

        output = BytesIO()
        with ZipFile(output, "w") as target_archive:
            for info in source_archive.infolist():
                target_archive.writestr(
                    info,
                    modified_xml.get(info.filename, source_archive.read(info.filename)),
                )

    return output.getvalue()


FORMATTING_ELEMENT_NAMES = {
    "pPr",
    "rPr",
    "tblPr",
    "tblGrid",
    "trPr",
    "tcPr",
}


def _clean_story_formatting(stories: Iterable[StoryPart]) -> None:
    for story in stories:
        _remove_formatting_elements(story.root)


def _strip_story_hyperlinks(stories: Iterable[StoryPart]) -> None:
    for story in stories:
        _unwrap_hyperlink_elements(story.root)


def _unwrap_hyperlink_elements(node: ET.Element) -> None:
    for child in list(node):
        _unwrap_hyperlink_elements(child)
        if child.tag != _qn("w", "hyperlink"):
            continue

        index = list(node).index(child)
        node.remove(child)
        for offset, grandchild in enumerate(list(child)):
            node.insert(index + offset, grandchild)


def _remove_formatting_elements(node: ET.Element) -> None:
    for child in list(node):
        if _local_name(child.tag) in FORMATTING_ELEMENT_NAMES:
            node.remove(child)
            continue
        _remove_formatting_elements(child)


def _register_namespaces(xml_bytes: bytes) -> None:
    seen_namespaces: set[tuple[str, str]] = set()
    for _, namespace in ET.iterparse(BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = namespace
        key = (prefix or "", uri)
        if key in seen_namespaces:
            continue
        seen_namespaces.add(key)
        ET.register_namespace(prefix or "", uri)


def _serialize_xml(root: ET.Element, original_xml: bytes) -> bytes:
    _restore_compatibility_namespace_declarations(root, original_xml)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _restore_compatibility_namespace_declarations(root: ET.Element, original_xml: bytes) -> None:
    namespaces = _extract_namespace_declarations(original_xml)
    compatibility_prefixes = _collect_markup_compatibility_prefixes(root)

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


def _collect_markup_compatibility_prefixes(root: ET.Element) -> set[str]:
    prefixes: set[str] = set()
    for element in root.iter():
        if element.tag == f"{{{MC_NS}}}Choice":
            prefixes.update(_extract_prefixes_from_compatibility_value(element.get("Requires", "")))
        for attr_name, attr_value in element.attrib.items():
            if attr_name.startswith(f"{{{MC_NS}}}") and attr_value:
                prefixes.update(_extract_prefixes_from_compatibility_value(str(attr_value)))
    return prefixes


def _extract_prefixes_from_compatibility_value(value: str) -> set[str]:
    prefixes: set[str] = set()
    for token in value.split():
        prefix = token.split(":", 1)[0].strip()
        if prefix:
            prefixes.add(prefix)
    return prefixes


def _namespace_uri_is_used(root: ET.Element, uri: str) -> bool:
    namespace_prefix = f"{{{uri}}}"
    for element in root.iter():
        if element.tag.startswith(namespace_prefix):
            return True
        for attr_name in element.attrib:
            if attr_name.startswith(namespace_prefix):
                return True
    return False


def _sanitize_xml_text(text: str) -> str:
    return "".join(
        char
        for char in str(text)
        if char in "\t\n\r"
        or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
    )


def _resolve_segment_block_type(story_kind: str, block_type: str) -> str:
    if block_type in {"table_cell", "textbox"}:
        return block_type
    if story_kind in {"header", "footer", "footnote", "endnote", "comment"}:
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
