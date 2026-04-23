from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from io import BytesIO
from itertools import count
from pathlib import Path
import re
from typing import Any
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.services.document_workspace import (
    CELL_GROUP_MAX_CHARS,
    CELL_NEXT_PARAGRAPH_MAX_CHARS,
    CELL_PARAGRAPH_BREAK_SENTINEL,
    CELL_SENTENCE_END_CHARS,
    CELL_SHORT_PARAGRAPH_MAX_CHARS,
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
    _resolve_paragraph_numbering_reference,
    _select_preferred_alternate_content_branch,
)
from app.services.normalizer import normalize_text
from app.services.sentence_splitter import SentenceSpan, split_sentence_spans


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"
EXPORT_FONT_FAMILY = "Times New Roman"
BlockKey = tuple[str, int, int | None, int | None]
MATH_PLACEHOLDER_RE = re.compile(r"⟦MATH_\d+⟧|\[\[MATH_\d+\]\]")


@dataclass(frozen=True)
class ExportSegment:
    sentence_id: str
    source_text: str
    target_text: str
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


@dataclass(frozen=True)
class CellParagraphTokens:
    paragraph: ET.Element
    tokens: list[TextToken]


def export_translated_docx(raw_bytes: bytes, segments: Iterable[Any]) -> bytes:
    package = DocxPackage(raw_bytes)
    stories = _build_story_parts(package)
    numbering_schema = _build_numbering_schema(package)
    source_workspace = get_cached_docx_workspace(raw_bytes)
    math_placeholders_by_sentence_id = {
        str(segment["sentence_id"]): dict(segment.get("math_placeholders") or {})
        for segment in source_workspace["segments"]
        if segment.get("sentence_id")
    }
    segments_by_block = _group_segments_by_block(segments, math_placeholders_by_sentence_id)
    block_counter = count(0)

    for story in stories:
        _export_block_sequence(
            container=story.root,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
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


def _group_segments_by_block(
    segments: Iterable[Any],
    math_placeholders_by_sentence_id: Mapping[str, dict[str, str]] | None = None,
) -> dict[BlockKey, list[ExportSegment]]:
    grouped: dict[BlockKey, list[ExportSegment]] = defaultdict(list)
    math_map = math_placeholders_by_sentence_id or {}

    for segment in segments:
        block_type = str(_get_segment_value(segment, "block_type", "paragraph") or "paragraph")
        block_index = int(_get_segment_value(segment, "block_index", 0) or 0)
        row_index = _to_optional_int(_get_segment_value(segment, "row_index"))
        cell_index = _to_optional_int(_get_segment_value(segment, "cell_index"))
        sentence_id = str(_get_segment_value(segment, "sentence_id", "") or "")

        grouped[(block_type, block_index, row_index, cell_index)].append(
            ExportSegment(
                sentence_id=sentence_id,
                source_text=str(_get_segment_value(segment, "source_text", "") or ""),
                target_text=str(_get_segment_value(segment, "target_text", "") or ""),
                math_placeholders=dict(math_map.get(sentence_id) or {}),
            )
        )

    return grouped


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

    for paragraph in paragraphs:
        if not paragraph.tokens:
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

        grouped_paragraphs.append((current_tokens, _count_token_sentence_spans(current_tokens)))
        current_tokens = paragraph_tokens

    if current_tokens:
        grouped_paragraphs.append((current_tokens, _count_token_sentence_spans(current_tokens)))

    return grouped_paragraphs


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
) -> list[TextToken]:
    placeholder_counter = math_placeholder_counter if math_placeholder_counter is not None else [0]
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
        )

    if node.tag == _qn("w", "r"):
        current_run = node
        current_run_container = parent_element

    if node.tag == _qn("w", "t"):
        text_value = node.text or ""
        return [
            TextToken(
                display_text=text_value,
                source_text=text_value,
                element=node,
                run_element=current_run,
                anchor_element=current_run or node,
                container_element=current_run_container or parent_element,
                original_text=text_value,
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
        _export_embedded_textboxes(
            node=node,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
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
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
                current_run=current_run,
                current_run_container=current_run_container,
                parent_element=node,
                math_placeholder_counter=placeholder_counter,
            )
        )

    return tokens


def _export_embedded_textboxes(
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
        sentence_source = _normalize_segment_source_text(_collect_span_text(tokens, span, use_source=True))
        if not sentence_source:
            continue

        if segment_index >= len(segments):
            break

        segment = segments[segment_index]
        segment_index += 1
        replacement = segment.target_text
        if not normalize_text(replacement):
            continue

        expected_math_placeholders = _extract_math_placeholders_from_tokens(tokens, span)
        if expected_math_placeholders:
            _queue_math_sentence_replacement(
                tokens=tokens,
                span=span,
                replacement=replacement,
                expected_math_placeholders=expected_math_placeholders,
            )
        else:
            _queue_sentence_replacement(tokens, span, replacement)

    _apply_token_edits(tokens)


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
    first_token, first_start, first_end = writable_overlaps[0]
    first_token.edits.append((first_start, first_end, replacement_text))
    first_token.apply_export_font = True

    for token, local_start, local_end in writable_overlaps[1:]:
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
