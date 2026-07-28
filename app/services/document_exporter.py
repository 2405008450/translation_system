from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from hashlib import sha1
from html import escape as escape_html
from html.parser import HTMLParser
from io import BytesIO
from itertools import count
from pathlib import Path
import logging
import os
import re
from typing import Any
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from app.services.automatic_numbering import (
    build_localized_docx_numbering_definition,
    strip_automatic_numbering_prefix,
)
from app.services.document_workspace import (
    CELL_PARAGRAPH_BREAK_SENTINEL,
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
    _iter_chart_text_elements,
    _iter_block_nodes,
    _iter_related_chart_parts,
    _local_name,
    _normalize_segment_source_text,
    _qn,
    _resolve_internal_reference_field_target,
    _resolve_paragraph_numbering_reference,
    _select_preferred_alternate_content_branch,
    normalize_document_parse_options,
    normalize_document_parse_mode,
    should_merge_table_cell_paragraph_texts,
)
from app.services.normalizer import compact_match_core, normalize_text
from app.services.sentence_splitter import SentenceSpan, split_sentence_spans


# ── 临时诊断：译文回填位置排查（默认关闭，设置环境变量 EXPORT_POSITION_DEBUG=1 开启）──
logger = logging.getLogger(__name__)
_EXPORT_POSITION_DEBUG = os.environ.get("EXPORT_POSITION_DEBUG", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _pos_debug(message: str) -> None:
    if _EXPORT_POSITION_DEBUG:
        logger.warning("[EXPORT-POS] %s", message)


def _pos_window_bound(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# 只打印 block_index 落在 [EXPORT_POS_MIN, EXPORT_POS_MAX] 区间内的日志（不设则全部打印），
# 用于把解析侧(SOURCE)与导出侧(WRITE)的同一段区间对齐排查。
_POS_MIN = _pos_window_bound("EXPORT_POS_MIN")
_POS_MAX = _pos_window_bound("EXPORT_POS_MAX")


def _pos_in_window(block_index: int | None) -> bool:
    if block_index is None:
        return _POS_MIN is None and _POS_MAX is None
    if _POS_MIN is not None and block_index < _POS_MIN:
        return False
    if _POS_MAX is not None and block_index > _POS_MAX:
        return False
    return True


def _text_preview(text: Any, limit: int = 24) -> str:
    collapsed = " ".join(str(text or "").split())
    return collapsed[:limit] + ("…" if len(collapsed) > limit else "")


def _sort_block_keys(keys: Iterable[BlockKey]) -> list[BlockKey]:
    def _norm(value: int | None) -> int:
        return value if value is not None else -1

    return sorted(keys, key=lambda k: (str(k[0]), int(k[1]), _norm(k[2]), _norm(k[3])))


def _dump_source_and_target_blocks(
    label: str,
    source_segments: Iterable[Mapping[str, Any]],
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> None:
    if not _EXPORT_POSITION_DEBUG:
        return
    source_list = list(source_segments or [])
    _pos_debug(
        f"===== {label}: {len(source_list)} 个源句段 / {len(segments_by_block)} 个译文块分组 ====="
    )
    for seg in source_list:
        key = _source_segment_block_key(seg)
        if not _pos_in_window(key[1]):
            continue
        _pos_debug(
            "SOURCE key=%s sid=%s text=%r"
            % (
                key,
                seg.get("sentence_id"),
                _text_preview(seg.get("source_text") or seg.get("display_text")),
            )
        )
    for key in _sort_block_keys(segments_by_block.keys()):
        if not _pos_in_window(key[1]):
            continue
        group = segments_by_block[key]
        _pos_debug(
            "TARGET key=%s n=%d src0=%r tgt0=%r"
            % (
                key,
                len(group),
                _text_preview(group[0].source_text if group else ""),
                _text_preview(group[0].target_text if group else ""),
            )
        )

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
EXPORT_FONT_FAMILY = "Times New Roman"
BILINGUAL_LAYOUT_SOURCE_FIRST = "source_first"
BILINGUAL_LAYOUT_TARGET_FIRST = "target_first"
BlockKey = tuple[str, int, int | None, int | None]
MATH_PLACEHOLDER_RE = re.compile(r"⟦MATH_\d+⟧|\[\[MATH_\d+\]\]")
MATH_PLACEHOLDER_TOKEN_RE = re.compile(r"^(?:⟦|\[\[)(MATH_\d+)(?:⟧|\]\])$")
ENGLISH_BOUNDARY_TRAILING_RE = re.compile(r"[,;:.!?][\"')\]\}]*$")
ENGLISH_WORD_LEADING_RE = re.compile(r"^[\"'“‘(\[]*[A-Za-z0-9]")
# 支持的格式标签
FORMAT_TAG_RE = re.compile(r"<(/?)(b|strong|i|em|u|s|strike|del|sub|sup)>", re.IGNORECASE)
REVISION_DIFF_TOKEN_RE = re.compile(
    r"[a-zA-Z0-9]+(?:[-'][a-zA-Z0-9]+)*|[\u4e00-\u9fff]|\s+|[^\s\w\u4e00-\u9fff]+"
)
REVISION_MARKER_PREFIX = "\ue000DOCX_REVISION_"
REVISION_MARKER_SUFFIX = "\ue001"
EXPLICIT_FORMAT_RUN_PROPERTIES = {
    "b",
    "bCs",
    "i",
    "iCs",
    "u",
    "strike",
    "dstrike",
    "vertAlign",
}


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

    @property
    def formats(self) -> tuple[bool, bool, bool, bool, bool, bool]:
        return (
            self.bold,
            self.italic,
            self.underline,
            self.strike,
            self.subscript,
            self.superscript,
        )


def _has_format_tags(html: str | None) -> bool:
    """检查 HTML 是否包含格式标签"""
    if not html:
        return False
    return bool(FORMAT_TAG_RE.search(html))


def _parse_formatted_html(html: str) -> list[FormattedTextFragment]:
    """解析编辑器或源文档 HTML，返回仅包含受支持基础格式的文本片段。"""
    fragments: list[FormattedTextFragment] = []

    class FormatHTMLParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.format_stack: list[set[str]] = []
            self.current_formats: set[str] = set()

        def handle_starttag(self, tag: str, attrs):
            tag_lower = tag.lower()
            self.format_stack.append(self.current_formats.copy())
            next_formats = self.current_formats.copy()
            if tag_lower in ('b', 'strong'):
                next_formats.add('bold')
            elif tag_lower in ('i', 'em'):
                next_formats.add('italic')
            elif tag_lower == 'u':
                next_formats.add('underline')
            elif tag_lower in ('s', 'strike', 'del'):
                next_formats.add('strike')
            elif tag_lower == 'sub':
                next_formats.add('subscript')
            elif tag_lower == 'sup':
                next_formats.add('superscript')

            style = dict(attrs).get("style") or ""
            next_formats.update(_formats_from_inline_css(style))
            self.current_formats = next_formats

        def handle_endtag(self, tag: str):
            if self.format_stack:
                self.current_formats = self.format_stack.pop()
            else:
                self.current_formats = set()

        def handle_data(self, data: str):
            if data:
                fragment = FormattedTextFragment(
                    text=data,
                    bold='bold' in self.current_formats,
                    italic='italic' in self.current_formats,
                    underline='underline' in self.current_formats,
                    strike='strike' in self.current_formats,
                    subscript='subscript' in self.current_formats,
                    superscript='superscript' in self.current_formats,
                )
                if fragments and fragments[-1].formats == fragment.formats:
                    previous = fragments[-1]
                    fragments[-1] = FormattedTextFragment(
                        text=previous.text + fragment.text,
                        bold=fragment.bold,
                        italic=fragment.italic,
                        underline=fragment.underline,
                        strike=fragment.strike,
                        subscript=fragment.subscript,
                        superscript=fragment.superscript,
                    )
                else:
                    fragments.append(fragment)

    parser = FormatHTMLParser()
    parser.feed(html)
    return fragments


def _formats_from_inline_css(style: str) -> set[str]:
    declarations: dict[str, str] = {}
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        declarations[name.strip().lower()] = value.strip().lower()

    formats: set[str] = set()
    font_weight = declarations.get("font-weight", "")
    if font_weight in {"bold", "bolder"}:
        formats.add("bold")
    else:
        try:
            if int(font_weight) >= 600:
                formats.add("bold")
        except ValueError:
            pass

    font_style = declarations.get("font-style", "")
    if "italic" in font_style or "oblique" in font_style:
        formats.add("italic")

    text_decoration = " ".join(
        (
            declarations.get("text-decoration", ""),
            declarations.get("text-decoration-line", ""),
        )
    )
    if "underline" in text_decoration:
        formats.add("underline")
    if "line-through" in text_decoration:
        formats.add("strike")

    vertical_align = declarations.get("vertical-align", "")
    if vertical_align == "sub":
        formats.add("subscript")
    elif vertical_align in {"super", "sup"}:
        formats.add("superscript")
    return formats


def _derive_target_html_from_source(source_html: str | None, target_text: str) -> str | None:
    """保守地把源文档的基础字符格式投影到译文。

    文本完全相同时逐 run 保留；文本变化后只保留全段共同格式，并迁移在译文中
    唯一出现的带格式原文片段。这样可以保留姓名、日期等锚点，同时避免首个 run
    的下划线或粗体错误扩散到整句。
    """
    if (
        not source_html
        or not target_text
        or "\n" in target_text
        or "\r" in target_text
        or re.search(r"<\s*a\b", source_html, re.IGNORECASE)
    ):
        return None

    source_fragments = _parse_formatted_html(source_html)
    if not source_fragments or not any(any(fragment.formats) for fragment in source_fragments):
        return None

    source_text = "".join(fragment.text for fragment in source_fragments)
    if (
        source_text == target_text
        or _collapse_html_projection_whitespace(source_text)
        == _collapse_html_projection_whitespace(target_text)
    ):
        return "".join(_formatted_fragment_to_html(fragment) for fragment in source_fragments)

    meaningful_fragments = [fragment for fragment in source_fragments if fragment.text.strip()]
    common_formats = tuple(
        bool(meaningful_fragments) and all(fragment.formats[index] for fragment in meaningful_fragments)
        for index in range(6)
    )
    target_formats = [set(_format_names_from_flags(common_formats)) for _ in target_text]

    candidates = sorted(
        (
            fragment for fragment in source_fragments
            if any(fragment.formats) and len(re.sub(r"\W", "", fragment.text, flags=re.UNICODE)) >= 2
        ),
        key=lambda fragment: len(fragment.text),
        reverse=True,
    )
    for fragment in candidates:
        matches = _find_unique_whitespace_flexible_match(target_text, fragment.text)
        if len(matches) != 1:
            continue
        start, end = matches[0]
        names = _format_names_from_flags(fragment.formats)
        for index in range(start, end):
            target_formats[index].update(names)

    if not any(target_formats):
        return None
    return _render_text_with_format_sets(target_text, target_formats)


FORMAT_NAMES = ("bold", "italic", "underline", "strike", "subscript", "superscript")


def _collapse_html_projection_whitespace(text: str) -> str:
    return " ".join(text.split())


def _format_names_from_flags(flags: tuple[bool, bool, bool, bool, bool, bool]) -> tuple[str, ...]:
    return tuple(name for name, enabled in zip(FORMAT_NAMES, flags, strict=True) if enabled)


def _find_unique_whitespace_flexible_match(text: str, candidate: str) -> list[tuple[int, int]]:
    stripped = candidate.strip()
    if not stripped:
        return []
    pieces = re.split(r"\s+", stripped)
    pattern = r"\s+".join(re.escape(piece) for piece in pieces)
    return [(match.start(), match.end()) for match in re.finditer(pattern, text)]


def _formatted_fragment_to_html(fragment: FormattedTextFragment) -> str:
    return _wrap_html_with_formats(escape_html(fragment.text), _format_names_from_flags(fragment.formats))


def _render_text_with_format_sets(text: str, format_sets: list[set[str]]) -> str:
    parts: list[str] = []
    start = 0
    while start < len(text):
        current_formats = format_sets[start]
        end = start + 1
        while end < len(text) and format_sets[end] == current_formats:
            end += 1
        ordered_formats = tuple(name for name in FORMAT_NAMES if name in current_formats)
        parts.append(_wrap_html_with_formats(escape_html(text[start:end]), ordered_formats))
        start = end
    return "".join(parts)


def _wrap_html_with_formats(content: str, formats: tuple[str, ...]) -> str:
    tag_by_format = {
        "bold": "b",
        "italic": "i",
        "underline": "u",
        "strike": "s",
        "subscript": "sub",
        "superscript": "sup",
    }
    for format_name in reversed(FORMAT_NAMES):
        if format_name not in formats:
            continue
        tag = tag_by_format[format_name]
        content = f"<{tag}>{content}</{tag}>"
    return content


@dataclass(frozen=True)
class ExportSegment:
    sentence_id: str
    source_text: str
    target_text: str
    display_text: str = ""
    numbering_text: str = ""
    matched_source_text: str = ""
    target_html: str | None = None
    source_html: str | None = None
    math_placeholders: dict[str, str] = field(default_factory=dict)
    sequence_index: int | None = None
    source_structure_changed: bool = False
    revision: "ExportRevisionInfo | None" = None


@dataclass(frozen=True)
class ExportRevisionInfo:
    revision_key: str
    before_text: str
    after_text: str
    author: str
    created_at: str | None = None


@dataclass(frozen=True)
class RevisionDiffPart:
    kind: str
    text: str


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
        part_names={story.part_name for story in stories}
        | _collect_related_chart_part_names(stories)
        | {"word/numbering.xml"},
    )


def export_translated_docx(
    raw_bytes: bytes,
    segments: Iterable[Any],
    document_parse_mode: str = DOCUMENT_PARSE_MODE_FULL,
    document_parse_options: Mapping[str, object] | str | None = None,
    target_language: str | None = None,
    revisions: Iterable[Any] | None = None,
    include_revision_marks: bool = False,
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
    revisions_by_sentence_id = (
        _build_export_revision_lookup(revisions)
        if include_revision_marks
        else {}
    )
    math_placeholders_by_sentence_id = {
        str(segment["sentence_id"]): dict(segment.get("math_placeholders") or {})
        for segment in source_segments
        if segment.get("sentence_id")
    }
    segments_by_block = _group_segments_by_block(
        segments,
        math_placeholders_by_sentence_id,
        source_segments=source_segments,
        revisions_by_sentence_id=revisions_by_sentence_id,
    )
    _dump_source_and_target_blocks("译文导出(translated)", source_segments, segments_by_block)
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

    modified_part_names = (
        {story.part_name for story in stories}
        | _collect_related_chart_part_names(stories)
        | {"word/numbering.xml"}
    )
    if revisions_by_sentence_id and _enable_word_revision_tracking(package):
        modified_part_names.add("word/settings.xml")

    return _build_modified_docx(
        raw_bytes=raw_bytes,
        package=package,
        part_names=modified_part_names,
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


def _build_export_revision_lookup(
    revisions: Iterable[Any] | None,
) -> dict[str, ExportRevisionInfo]:
    lookup: dict[str, ExportRevisionInfo] = {}
    for revision in revisions or []:
        status = str(_get_segment_value(revision, "status", "pending") or "pending")
        source = str(_get_segment_value(revision, "source", "manual") or "manual")
        sentence_id = str(_get_segment_value(revision, "sentence_id", "") or "")
        if status != "pending" or source != "manual" or not sentence_id or sentence_id in lookup:
            continue

        before_text = str(_get_segment_value(revision, "before_text", "") or "")
        after_text = str(_get_segment_value(revision, "after_text", "") or "")
        if before_text == after_text:
            continue

        revision_key = str(_get_segment_value(revision, "id", sentence_id) or sentence_id)
        created_at_value = _get_segment_value(revision, "created_at")
        if hasattr(created_at_value, "isoformat"):
            created_at = created_at_value.isoformat()
        else:
            created_at = str(created_at_value) if created_at_value else None

        lookup[sentence_id] = ExportRevisionInfo(
            revision_key=revision_key,
            before_text=before_text,
            after_text=after_text,
            author=_resolve_revision_author_name(_get_segment_value(revision, "author")),
            created_at=created_at,
        )
    return lookup


def _resolve_revision_author_name(author: Any) -> str:
    if author is None:
        return "AI Translation System"
    if isinstance(author, str):
        return author.strip() or "AI Translation System"
    for field_name in ("nickname", "username", "name"):
        value = _get_segment_value(author, field_name)
        if value and str(value).strip():
            return str(value).strip()
    return "AI Translation System"


def _enable_word_revision_tracking(package: DocxPackage) -> bool:
    settings_root = package.read_xml("word/settings.xml")
    if settings_root is None:
        return False
    revision_view = settings_root.find("./w:revisionView", NS)
    if revision_view is None:
        revision_view = ET.Element(_qn("w", "revisionView"))
        settings_root.append(revision_view)
    revision_view.set(_qn("w", "markup"), "1")
    revision_view.set(_qn("w", "insDel"), "1")

    track_revisions = settings_root.find("./w:trackRevisions", NS)
    if track_revisions is None:
        settings_root.append(ET.Element(_qn("w", "trackRevisions")))
    return True


def _group_segments_by_block(
    segments: Iterable[Any],
    math_placeholders_by_sentence_id: Mapping[str, dict[str, str]] | None = None,
    source_segments: Iterable[Mapping[str, Any]] | None = None,
    revisions_by_sentence_id: Mapping[str, ExportRevisionInfo] | None = None,
) -> dict[BlockKey, list[ExportSegment]]:
    grouped: dict[BlockKey, list[ExportSegment]] = defaultdict(list)
    math_map = math_placeholders_by_sentence_id or {}
    source_segment_list = list(source_segments or [])
    source_segment_by_sentence_id = _build_source_segment_lookup_by_sentence_id(source_segment_list)
    source_segment_by_text_key = _build_unique_source_segment_lookup_by_text(source_segment_list)
    revision_map = revisions_by_sentence_id or {}

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
        source_segment_by_id = source_segment_by_sentence_id.get(sentence_id)
        source_segment_by_text = _find_source_segment_by_export_text(
            segment,
            source_segment_by_text_key,
        )
        has_original_source_match = source_segment_by_text is not None or bool(
            source_segment_by_id
            and _export_segment_text_matches_source_segment(segment, source_segment_by_id)
        )
        source_html = _get_segment_value(segment, "source_html")
        if not source_html and has_original_source_match and source_segment_by_id is not None:
            source_html = source_segment_by_id.get("source_html")
        if not source_html and has_original_source_match and source_segment_by_text is not None:
            source_html = source_segment_by_text.get("source_html")
        resolved_target_html = str(target_html) if target_html else _derive_target_html_from_source(
            str(source_html) if source_html else None,
            str(_get_segment_value(segment, "target_text", "") or ""),
        )
        target_text = str(_get_segment_value(segment, "target_text", "") or "")
        revision = revision_map.get(sentence_id)
        if revision is not None and revision.after_text != target_text:
            # 只导出仍对应当前译文的待审修订，避免把过期快照写入 Word。
            revision = None

        grouped[block_key].append(
            ExportSegment(
                sentence_id=sentence_id,
                source_text=str(_get_segment_value(segment, "source_text", "") or ""),
                target_text=target_text,
                display_text=str(_get_segment_value(segment, "display_text", "") or ""),
                numbering_text=str(_get_segment_value(segment, "numbering_text", "") or ""),
                matched_source_text=str(_get_segment_value(segment, "matched_source_text", "") or ""),
                target_html=resolved_target_html,
                source_html=str(source_html) if source_html else None,
                math_placeholders=dict(math_map.get(sentence_id) or {}),
                sequence_index=_get_export_sequence_index(segment),
                source_structure_changed=bool(source_segment_list) and not has_original_source_match,
                revision=revision,
            )
        )

    # 原格式译文和双语 Word 共用这一排序入口。即使源块元数据无法重新定位，
    # 仍应优先使用持久化的 sequence_index，不能保留调用方或哈希 ID 带来的乱序。
    return _order_segment_groups_by_source(grouped, source_segment_list)


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
    source_segment_by_id = source_segment_by_sentence_id.get(sentence_id)
    source_segment_by_text = _find_source_segment_by_export_text(
        segment,
        source_segment_by_text_key,
    )

    if source_segment_by_text is not None and (
        source_segment_by_id is None
        or not _export_segment_text_matches_source_segment(segment, source_segment_by_id)
    ):
        return _source_segment_block_key(source_segment_by_text)

    if source_segment_by_id is None:
        return fallback

    return _source_segment_block_key(source_segment_by_id)


def _find_source_segment_by_export_text(
    segment: Any,
    source_segment_by_text_key: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    for text_key in _export_segment_text_keys_from_any(segment):
        source_segment = source_segment_by_text_key.get(text_key)
        if source_segment is not None:
            return source_segment
    return None


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
        ordered[block_key] = _order_export_segments_for_source_block(
            block_segments,
            source_by_block.get(block_key, []),
        )
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
    if _has_complete_explicit_sequence(block_segments):
        return sorted(block_segments, key=lambda segment: int(segment.sequence_index or 0))

    used_indexes: set[int] = set()
    ordered: list[ExportSegment] = []

    for source_segment in source_segments:
        match_index = _find_export_segment_by_sentence_id(
            block_segments,
            source_segment,
            used_indexes,
            require_text_compatibility=True,
        )
        if match_index is None:
            match_index = _find_unique_export_segment_by_text(block_segments, source_segment, used_indexes)
        if match_index is None:
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
    require_text_compatibility: bool = False,
) -> int | None:
    sentence_id = str(source_segment.get("sentence_id") or "")
    if not sentence_id:
        return None

    for index, segment in enumerate(block_segments):
        if index in used_indexes:
            continue
        if segment.sentence_id == sentence_id:
            if require_text_compatibility and not _export_segment_text_matches_source_segment(segment, source_segment):
                continue
            return index
    return None


def _find_unique_export_segment_by_text(
    block_segments: list[ExportSegment],
    source_segment: Mapping[str, Any],
    used_indexes: set[int],
) -> int | None:
    source_keys = _source_segment_text_keys(source_segment)
    if not source_keys:
        return None

    matches = [
        index
        for index, segment in enumerate(block_segments)
        if index not in used_indexes and source_keys & _export_segment_text_keys(segment)
    ]
    return matches[0] if len(matches) == 1 else None


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


def _export_segment_text_keys_from_any(segment: Any) -> set[str]:
    if isinstance(segment, ExportSegment):
        return _export_segment_text_keys(segment)
    return _segment_text_keys(
        _get_segment_value(segment, "source_text", ""),
        _get_segment_value(segment, "display_text", ""),
    )


def _export_segment_text_matches_source_segment(
    segment: Any,
    source_segment: Mapping[str, Any],
) -> bool:
    source_keys = _source_segment_text_keys(source_segment)
    export_keys = _export_segment_text_keys_from_any(segment)
    if not source_keys or not export_keys:
        return True
    return bool(source_keys & export_keys)


def _segment_text_keys(*values: object) -> set[str]:
    keys: set[str] = set()
    for value in values:
        text = _normalize_segment_source_text(str(value or ""))
        if text:
            keys.add(f"exact:{text}")
            compact_text = compact_match_core(text)
            if compact_text:
                keys.add(f"compact:{compact_text}")
    return keys


def _get_export_sequence_index(segment: Any) -> int | None:
    value = _get_segment_value(segment, "sequence_index")
    if value is None or value == "":
        return None
    try:
        sequence_index = int(value)
    except (TypeError, ValueError):
        return None
    return sequence_index if sequence_index >= 0 else None


def _has_complete_explicit_sequence(segments: Iterable[ExportSegment]) -> bool:
    sequence_indexes = [segment.sequence_index for segment in segments]
    return bool(sequence_indexes) and all(
        sequence_index is not None for sequence_index in sequence_indexes
    ) and len(sequence_indexes) == len(set(sequence_indexes))


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
            _export_bilingual_embedded_charts(
                node=child,
                story=story,
                block_counter=block_counter,
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


def _should_merge_table_cell_paragraphs(
    current_tokens: list[TextToken],
    next_paragraph: CellParagraphTokens,
    numbering_schema: NumberingSchema,
) -> bool:
    if not current_tokens or not next_paragraph.tokens:
        return False
    return should_merge_table_cell_paragraph_texts(
        "".join(token.display_text for token in current_tokens),
        "".join(token.display_text for token in next_paragraph.tokens),
        next_has_numbering=_resolve_paragraph_numbering_reference(
            next_paragraph.paragraph,
            numbering_schema,
        )
        is not None,
    )


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


def _cell_paragraph_group_tokens(paragraph_group: list[CellParagraphTokens]) -> list[TextToken]:
    tokens: list[TextToken] = []
    for index, item in enumerate(paragraph_group):
        if index > 0:
            tokens.append(
                TextToken(
                    display_text="\n",
                    source_text=CELL_PARAGRAPH_BREAK_SENTINEL,
                )
            )
        tokens.extend(item.tokens)
    return tokens


def _try_distribute_table_cell_paragraph_lines(
    cell: ET.Element,
    paragraph_group: list[CellParagraphTokens],
    group_segments: list[ExportSegment],
    story: StoryPart,
    block_counter,
    numbering_schema: NumberingSchema,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> bool:
    if len(paragraph_group) < 2 or len(group_segments) != 1:
        return False

    segment = group_segments[0]
    if segment.target_html is not None or segment.math_placeholders:
        return False

    replacement = _resolve_segment_replacement_text(segment)
    replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in replacement or not normalize_text(replacement):
        return False

    lines = replacement.split("\n")
    if len(lines) < 2:
        return False

    extra_paragraph_template = deepcopy(paragraph_group[-1].paragraph)
    for index, item in enumerate(paragraph_group):
        line = lines[index] if index < len(lines) else ""
        _replace_entire_paragraph_tokens_with_text(item.tokens, line)

    if len(lines) <= len(paragraph_group):
        return True

    parent = paragraph_group[-1].parent or cell
    anchor = paragraph_group[-1].paragraph
    try:
        insert_index = list(parent).index(anchor) + 1
    except ValueError:
        parent = cell
        insert_index = list(parent).index(anchor) + 1 if anchor in list(parent) else len(parent)

    for line in lines[len(paragraph_group):]:
        clone = _clone_bilingual_paragraph(extra_paragraph_template)
        parent.insert(insert_index, clone)
        insert_index += 1
        clone_tokens = _collect_inline_tokens(
            node=clone,
            story=story,
            block_counter=block_counter,
            numbering_schema=numbering_schema,
            segments_by_block=segments_by_block,
            math_placeholder_counter=[0],
            process_embedded_textboxes=False,
        )
        _replace_entire_paragraph_tokens_with_text(clone_tokens, line)

    return True


def _replace_entire_paragraph_tokens_with_text(tokens: list[TextToken], text: str) -> None:
    if not tokens:
        return

    _assign_token_offsets(tokens)
    display_text = "".join(token.display_text for token in tokens)
    if not display_text:
        return

    _queue_sentence_replacement(
        tokens,
        SentenceSpan(start=0, end=len(display_text)),
        text,
    )
    _apply_token_edits(tokens)


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

        paragraph_groups = _group_table_cell_paragraph_groups(paragraph_buffer, numbering_schema)
        buffer_sentence_count = sum(sentence_count for _, sentence_count in paragraph_groups)
        buffer_tokens = _cell_paragraph_group_tokens(paragraph_buffer)
        buffer_segments, buffer_consumed_count = _take_segments_matching_token_source(
            cell_segments,
            segment_cursor,
            buffer_tokens,
            buffer_sentence_count,
        )
        if _tokens_have_structural_segment_change(buffer_tokens, buffer_segments):
            target_paragraphs = [_clone_bilingual_paragraph(item.paragraph) for item in paragraph_buffer]
            target_tokens = _collect_cell_group_tokens(
                paragraphs=target_paragraphs,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
            )
            _replace_block_tokens(
                tokens=target_tokens,
                segments=buffer_segments,
                keep_source_when_empty=False,
            )
            _insert_cloned_table_cell_paragraphs(
                cell=cell,
                paragraph_group=paragraph_buffer,
                target_paragraphs=target_paragraphs,
                order=order,
            )
            segment_cursor += buffer_consumed_count
            paragraph_buffer = []
            return

        for paragraph_group, sentence_count in paragraph_groups:
            if sentence_count == 0:
                continue

            target_tokens = _cell_paragraph_group_tokens(paragraph_group)
            group_segments, consumed_count = _take_segments_matching_token_source(
                cell_segments,
                segment_cursor,
                target_tokens,
                sentence_count,
            )
            segment_cursor += consumed_count
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
            _export_bilingual_embedded_charts(
                node=block,
                story=story,
                block_counter=block_counter,
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
    if _EXPORT_POSITION_DEBUG and cell_segments and _pos_in_window(block_index):
        first = cell_segments[0]
        _pos_debug(
            "WRITE-CELL story=%s key=%s hit=%d seg_src=%r seg_tgt=%r sid=%s"
            % (
                story.kind,
                (_resolve_segment_block_type(story.kind, "table_cell"), block_index, row_index, cell_index),
                len(cell_segments),
                _text_preview(first.source_text),
                _text_preview(first.target_text),
                first.sentence_id,
            )
        )
    segment_cursor = 0
    paragraph_buffer: list[CellParagraphTokens] = []

    def flush_paragraphs() -> None:
        nonlocal paragraph_buffer, segment_cursor
        if not paragraph_buffer:
            return

        paragraph_groups = _group_table_cell_paragraph_groups(paragraph_buffer, numbering_schema)
        buffer_sentence_count = sum(sentence_count for _, sentence_count in paragraph_groups)
        buffer_tokens = _cell_paragraph_group_tokens(paragraph_buffer)
        buffer_segments, buffer_consumed_count = _take_segments_matching_token_source(
            cell_segments,
            segment_cursor,
            buffer_tokens,
            buffer_sentence_count,
        )
        if _tokens_have_structural_segment_change(buffer_tokens, buffer_segments):
            _replace_block_tokens(
                tokens=buffer_tokens,
                segments=buffer_segments,
            )
            segment_cursor += buffer_consumed_count
            paragraph_buffer = []
            return

        for paragraph_group, sentence_count in paragraph_groups:
            if sentence_count == 0:
                continue
            token_group = _cell_paragraph_group_tokens(paragraph_group)
            group_segments, consumed_count = _take_segments_matching_token_source(
                cell_segments,
                segment_cursor,
                token_group,
                sentence_count,
            )
            if _try_distribute_table_cell_paragraph_lines(
                cell=cell,
                paragraph_group=paragraph_group,
                group_segments=group_segments,
                story=story,
                block_counter=block_counter,
                numbering_schema=numbering_schema,
                segments_by_block=segments_by_block,
            ):
                segment_cursor += consumed_count
                continue
            _replace_block_tokens(
                tokens=token_group,
                segments=group_segments,
            )
            segment_cursor += consumed_count

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
                    ),
                    parent=parent,
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

    lookup_key = (
        _resolve_segment_block_type(story.kind, block_type),
        block_index,
        row_index,
        cell_index,
    )
    matched_segments = segments_by_block.get(lookup_key, [])
    if _EXPORT_POSITION_DEBUG and _pos_in_window(block_index):
        token_text = "".join(token.display_text for token in tokens)
        first = matched_segments[0] if matched_segments else None
        mismatch = bool(first) and (
            _normalize_segment_source_text(token_text)
            != _normalize_segment_source_text(first.source_text)
        )
        _pos_debug(
            "WRITE-P%s story=%s key=%s hit=%d para_src=%r seg_src=%r seg_tgt=%r sid=%s"
            % (
                " !!MISMATCH" if mismatch else "",
                story.kind,
                lookup_key,
                len(matched_segments),
                _text_preview(token_text),
                _text_preview(first.source_text if first else ""),
                _text_preview(first.target_text if first else ""),
                first.sentence_id if first else "",
            )
        )

    _replace_block_tokens(
        tokens=tokens,
        segments=matched_segments,
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
        segments=block_segments,
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
        return [
            TextToken(
                display_text="\n",
                source_text="\n",
                element=node,
                run_element=current_run,
                anchor_element=current_run if current_run is not None else node,
                container_element=current_run_container if current_run_container is not None else parent_element,
                original_text="",
            )
        ]

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
        _export_embedded_charts(
            node=node,
            story=story,
            block_counter=block_counter,
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


def _export_bilingual_embedded_charts(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    order: str,
) -> None:
    for embedded_node in _iter_embedded_object_nodes_for_export(node):
        _export_embedded_chart_object(
            node=embedded_node,
            story=story,
            block_counter=block_counter,
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


def _export_embedded_charts(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
) -> None:
    for embedded_node in _iter_embedded_object_nodes_for_export(node):
        _export_embedded_chart_object(
            node=embedded_node,
            story=story,
            block_counter=block_counter,
            segments_by_block=segments_by_block,
        )


def _export_embedded_chart_object(
    node: ET.Element,
    story: StoryPart,
    block_counter,
    segments_by_block: dict[BlockKey, list[ExportSegment]],
    order: str | None = None,
) -> None:
    for _, chart_root in _iter_related_chart_parts(node, story):
        for text_element in _iter_chart_text_elements(chart_root):
            text_value = text_element.text or ""
            block_segments = segments_by_block.get(
                (_resolve_segment_block_type(story.kind, "chart_text"), next(block_counter), None, None),
                [],
            )
            if not block_segments:
                continue

            tokens = [
                TextToken(
                    display_text=text_value,
                    source_text=text_value,
                    element=text_element,
                    original_text=text_value,
                )
            ]
            replacement_segments = (
                _build_inline_bilingual_segments(block_segments, order)
                if order is not None
                else block_segments
            )
            _replace_block_tokens(
                tokens=tokens,
                segments=replacement_segments,
                keep_source_when_empty=order is None,
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

    source_spans = [
        (span, _normalize_segment_source_text(_collect_span_text(tokens, span, use_source=True)))
        for span in spans
    ]
    source_spans = [(span, source_text) for span, source_text in source_spans if source_text]
    if _should_replace_structurally_modified_block(source_spans, segments):
        _replace_structurally_modified_block(
            tokens=tokens,
            source_spans=source_spans,
            segments=segments,
            keep_source_when_empty=keep_source_when_empty,
        )
        return

    used_segment_indexes: set[int] = set()
    fallback_segment_index = 0
    use_explicit_sequence = _has_complete_explicit_sequence(segments)
    previous_replacement = ""
    previous_span: SentenceSpan | None = None
    pending_revision_markers: list[tuple[str, ExportSegment, str]] = []
    for span in spans:
        sentence_source = _normalize_segment_source_text(_collect_span_text(tokens, span, use_source=True))
        if not sentence_source:
            continue

        match_index = None
        if not use_explicit_sequence:
            match_index = _find_export_segment_index_for_span_source(
                segments=segments,
                sentence_source=sentence_source,
                used_indexes=used_segment_indexes,
                preferred_index=fallback_segment_index,
            )
        if match_index is None:
            match_index = _next_unused_segment_index(segments, used_segment_indexes, fallback_segment_index)
        if match_index is None:
            break

        used_segment_indexes.add(match_index)
        fallback_segment_index = _next_unused_segment_index(
            segments,
            used_segment_indexes,
            fallback_segment_index,
        ) or len(segments)
        segment = segments[match_index]
        replacement = _resolve_segment_replacement_text(segment)
        if previous_span is not None:
            boundary_text = display_text[previous_span.end:span.start]
            replacement = _normalize_adjacent_english_target_boundary(
                previous_replacement=previous_replacement,
                current_replacement=replacement,
                boundary_text=boundary_text,
            )
        if _can_queue_word_revision_marker(tokens, span, segment):
            marker = _build_revision_marker(
                segment.revision.revision_key,
                len(pending_revision_markers),
            )
            _queue_sentence_replacement(tokens, span, marker)
            pending_revision_markers.append((marker, segment, replacement))
            previous_replacement = replacement
            previous_span = span
            continue
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

        has_custom_target_html = segment.target_html is not None

        if expected_math_placeholders:
            _queue_math_sentence_replacement(
                tokens=tokens,
                span=span,
                replacement=replacement,
                expected_math_placeholders=expected_math_placeholders,
            )
        elif has_custom_target_html:
            _queue_formatted_sentence_replacement(tokens, span, segment.target_html)
        else:
            _queue_sentence_replacement(tokens, span, replacement)

        previous_replacement = replacement
        previous_span = span

    _apply_token_edits(tokens)
    _expand_word_revision_markers(tokens, pending_revision_markers)


def _can_queue_word_revision_marker(
    tokens: list[TextToken],
    span: SentenceSpan,
    segment: ExportSegment,
) -> bool:
    if (
        segment.revision is None
        or segment.source_structure_changed
        or segment.math_placeholders
    ):
        return False

    writable_tokens = [
        token
        for token in tokens
        if token.element is not None
        and token.start < span.end
        and token.end > span.start
    ]
    if not writable_tokens or any(token.is_math or token.is_hyperlink for token in writable_tokens):
        return False

    anchor = writable_tokens[0]
    return bool(
        anchor.element is not None
        and anchor.element.tag == _qn("w", "t")
        and anchor.run_element is not None
        and anchor.run_element.tag == _qn("w", "r")
        and anchor.container_element is not None
        and anchor.container_element.tag == _qn("w", "p")
        and anchor.run_element in list(anchor.container_element)
    )


def _build_revision_marker(revision_key: str, marker_index: int) -> str:
    digest = sha1(f"{revision_key}:{marker_index}".encode("utf-8")).hexdigest()[:16]
    return f"{REVISION_MARKER_PREFIX}{digest}{REVISION_MARKER_SUFFIX}"


def _expand_word_revision_markers(
    tokens: list[TextToken],
    pending_markers: list[tuple[str, ExportSegment, str]],
) -> None:
    paragraphs: list[ET.Element] = []
    seen_paragraph_ids: set[int] = set()
    for token in tokens:
        paragraph = token.container_element
        if (
            paragraph is None
            or paragraph.tag != _qn("w", "p")
            or id(paragraph) in seen_paragraph_ids
        ):
            continue
        seen_paragraph_ids.add(id(paragraph))
        paragraphs.append(paragraph)

    for marker, segment, replacement in pending_markers:
        marker_context = _find_word_revision_marker_context(paragraphs, marker)
        if marker_context is None or segment.revision is None:
            continue

        parent, run_element, text_element = marker_context

        current_text = text_element.text or ""
        prefix, suffix = current_text.split(marker, 1)
        text_element.text = prefix
        _sync_word_text_space_attribute(text_element)

        insert_index = list(parent).index(run_element) + 1
        if not _word_run_has_visible_content(run_element):
            parent.remove(run_element)
            insert_index -= 1

        revision_nodes = _build_word_revision_nodes(
            segment,
            run_element,
            effective_after_text=replacement,
        )
        for node in revision_nodes:
            parent.insert(insert_index, node)
            insert_index += 1

        if suffix:
            for suffix_run in _build_inserted_word_runs(suffix, run_element):
                parent.insert(insert_index, suffix_run)
                insert_index += 1


def _find_word_revision_marker_context(
    paragraphs: list[ET.Element],
    marker: str,
) -> tuple[ET.Element, ET.Element, ET.Element] | None:
    for paragraph in paragraphs:
        for run_element in paragraph.findall("./w:r", NS):
            for text_element in run_element.findall("./w:t", NS):
                if marker in (text_element.text or ""):
                    return paragraph, run_element, text_element
    return None


def _sync_word_text_space_attribute(text_element: ET.Element) -> None:
    text = text_element.text or ""
    if _needs_space_preserve(text):
        text_element.set(XML_SPACE_ATTR, "preserve")
    else:
        text_element.attrib.pop(XML_SPACE_ATTR, None)


def _word_run_has_visible_content(run_element: ET.Element) -> bool:
    for child in list(run_element):
        if _local_name(child.tag) == "rPr":
            continue
        if _local_name(child.tag) in {"t", "delText", "instrText"}:
            if child.text:
                return True
            continue
        return True
    return False


def _build_word_revision_nodes(
    segment: ExportSegment,
    reference_run: ET.Element | None,
    effective_after_text: str | None = None,
) -> list[ET.Element]:
    revision = segment.revision
    if revision is None:
        return _build_inserted_word_runs(_resolve_segment_replacement_text(segment), reference_run)

    before_text = _resolve_revision_replacement_text(segment, revision.before_text)
    resolved_after_text = _resolve_revision_replacement_text(segment, revision.after_text)
    after_text = effective_after_text if effective_after_text is not None else resolved_after_text
    if (
        effective_after_text is not None
        and resolved_after_text
        and effective_after_text.endswith(resolved_after_text)
    ):
        # 多句共享同一 Word run 时，导出器可能为相邻英文句补前导空格。
        # 该空格属于排版边界，不应额外显示为一次插入修订。
        before_text = effective_after_text[:-len(resolved_after_text)] + before_text
    nodes: list[ET.Element] = []
    for part_index, part in enumerate(_compute_revision_diff(before_text, after_text)):
        if part.kind == "equal":
            nodes.extend(_build_inserted_word_runs(part.text, reference_run))
            continue

        wrapper = ET.Element(_qn("w", "del" if part.kind == "delete" else "ins"))
        wrapper.set(
            _qn("w", "id"),
            _build_word_revision_id(revision.revision_key, part_index, part.kind),
        )
        wrapper.set(_qn("w", "author"), revision.author)
        if revision.created_at:
            wrapper.set(_qn("w", "date"), revision.created_at)

        runs = _build_inserted_word_runs(part.text, reference_run)
        if part.kind == "delete":
            for run in runs:
                for text_element in run.findall(".//w:t", NS):
                    text_element.tag = _qn("w", "delText")
        for run in runs:
            wrapper.append(run)
        nodes.append(wrapper)
    return nodes


def _resolve_revision_replacement_text(segment: ExportSegment, text: str) -> str:
    return strip_automatic_numbering_prefix(
        text,
        source_text=segment.source_text,
        display_text=segment.display_text,
        numbering_text=segment.numbering_text,
        reference_texts=[segment.matched_source_text],
    )


def _compute_revision_diff(before_text: str, after_text: str) -> list[RevisionDiffPart]:
    before_tokens = REVISION_DIFF_TOKEN_RE.findall(before_text)
    after_tokens = REVISION_DIFF_TOKEN_RE.findall(after_text)
    matcher = SequenceMatcher(None, before_tokens, after_tokens, autojunk=False)
    parts: list[RevisionDiffPart] = []

    def append_part(kind: str, text: str) -> None:
        if not text:
            return
        if parts and parts[-1].kind == kind:
            previous = parts[-1]
            parts[-1] = RevisionDiffPart(kind=kind, text=previous.text + text)
        else:
            parts.append(RevisionDiffPart(kind=kind, text=text))

    for opcode, before_start, before_end, after_start, after_end in matcher.get_opcodes():
        if opcode == "equal":
            append_part("equal", "".join(before_tokens[before_start:before_end]))
        elif opcode == "delete":
            append_part("delete", "".join(before_tokens[before_start:before_end]))
        elif opcode == "insert":
            append_part("insert", "".join(after_tokens[after_start:after_end]))
        else:
            append_part("delete", "".join(before_tokens[before_start:before_end]))
            append_part("insert", "".join(after_tokens[after_start:after_end]))
    return parts


def _build_word_revision_id(revision_key: str, part_index: int, kind: str) -> str:
    digest = sha1(f"{revision_key}:{part_index}:{kind}".encode("utf-8")).digest()
    return str(int.from_bytes(digest[:4], "big") & 0x7FFFFFFF)


def _structure_text_key(text: str) -> str:
    """结构对齐时忽略空白差异，但保留实际字符和标点。"""
    return "".join(_normalize_segment_source_text(text).split())


def _should_replace_structurally_modified_block(
    source_spans: list[tuple[SentenceSpan, str]],
    segments: list[ExportSegment],
) -> bool:
    if not source_spans or not segments:
        return False
    if not any(segment.source_structure_changed for segment in segments):
        return False

    span_keys = [_structure_text_key(source_text) for _, source_text in source_spans]
    segment_keys = [_structure_text_key(segment.source_text) for segment in segments]
    if not all(span_keys) or not all(segment_keys):
        return False

    return len(span_keys) != len(segment_keys) or any(
        span_key != segment_key
        for span_key, segment_key in zip(span_keys, segment_keys, strict=True)
    )


def _tokens_have_structural_segment_change(
    tokens: list[TextToken],
    segments: list[ExportSegment],
) -> bool:
    if not tokens or not segments:
        return False
    _assign_token_offsets(tokens)
    display_text = "".join(token.display_text for token in tokens)
    spans = split_sentence_spans(display_text)
    if not spans and normalize_text(display_text):
        spans = [_build_trimmed_span(display_text)]
    source_spans = [
        (span, _normalize_segment_source_text(_collect_span_text(tokens, span, use_source=True)))
        for span in spans
    ]
    return _should_replace_structurally_modified_block(
        [(span, source_text) for span, source_text in source_spans if source_text],
        segments,
    )


def _replace_structurally_modified_block(
    *,
    tokens: list[TextToken],
    source_spans: list[tuple[SentenceSpan, str]],
    segments: list[ExportSegment],
    keep_source_when_empty: bool,
) -> None:
    """拆分或合并改变句段边界时，按当前顺序完整重建对应源文本区间。"""
    replacement = ""
    for segment in segments:
        current = _resolve_segment_replacement_text(segment)
        if not normalize_text(current):
            if _is_target_placeholder(segment.target_text):
                current = segment.target_text
            elif keep_source_when_empty:
                current = segment.source_text
            else:
                current = ""
        replacement = _append_structural_replacement(replacement, current)

    first_span = source_spans[0][0]
    last_span = source_spans[-1][0]
    _queue_sentence_replacement(
        tokens,
        SentenceSpan(start=first_span.start, end=last_span.end),
        replacement,
    )
    _apply_token_edits(tokens)


def _append_structural_replacement(previous: str, current: str) -> str:
    if not current:
        return previous
    if not previous or previous[-1].isspace() or current[0].isspace():
        return previous + current

    previous_char = previous[-1]
    current_char = current[0]
    needs_word_space = (
        previous_char.isascii()
        and previous_char.isalnum()
        and current_char.isascii()
        and current_char.isalnum()
    )
    needs_sentence_space = bool(
        ENGLISH_BOUNDARY_TRAILING_RE.search(previous)
        and ENGLISH_WORD_LEADING_RE.match(current)
    )
    separator = " " if needs_word_space or needs_sentence_space else ""
    return previous + separator + current


def _take_segments_matching_token_source(
    segments: list[ExportSegment],
    start_index: int,
    tokens: list[TextToken],
    fallback_count: int,
) -> tuple[list[ExportSegment], int]:
    """按源文本累计选取句段，使表格单元格中的拆分/合并不再按旧句数截断。"""
    expected_key = _structure_text_key("".join(token.source_text for token in tokens))
    if expected_key:
        accumulated_key = ""
        for end_index in range(start_index, len(segments)):
            accumulated_key += _structure_text_key(segments[end_index].source_text)
            if accumulated_key == expected_key:
                consumed_count = end_index - start_index + 1
                return segments[start_index : end_index + 1], consumed_count
            if not expected_key.startswith(accumulated_key):
                break

    remaining_segments = segments[start_index:]
    if any(segment.source_structure_changed for segment in remaining_segments):
        return remaining_segments, len(remaining_segments)

    fallback_segments = segments[start_index : start_index + fallback_count]
    return fallback_segments, len(fallback_segments)


def _find_export_segment_index_for_span_source(
    *,
    segments: list[ExportSegment],
    sentence_source: str,
    used_indexes: set[int],
    preferred_index: int,
) -> int | None:
    if (
        0 <= preferred_index < len(segments)
        and preferred_index not in used_indexes
        and _export_segment_matches_source_text(segments[preferred_index], sentence_source)
    ):
        return preferred_index

    matches = [
        index
        for index, segment in enumerate(segments)
        if index not in used_indexes and _export_segment_matches_source_text(segment, sentence_source)
    ]
    return matches[0] if len(matches) == 1 else None


def _next_unused_segment_index(
    segments: list[ExportSegment],
    used_indexes: set[int],
    start_index: int,
) -> int | None:
    index = max(start_index, 0)
    while index < len(segments):
        if index not in used_indexes:
            return index
        index += 1
    return None


def _export_segment_matches_source_text(segment: ExportSegment, sentence_source: str) -> bool:
    if not sentence_source:
        return False
    return bool(_segment_text_keys(sentence_source) & _export_segment_text_keys(segment))


def _is_target_placeholder(text: str | None) -> bool:
    return bool(text) and not normalize_text(text or "")


def _resolve_segment_replacement_text(segment: ExportSegment) -> str:
    if _is_target_placeholder(segment.target_text):
        return segment.target_text
    return strip_automatic_numbering_prefix(
        segment.target_text,
        source_text=segment.source_text,
        display_text=segment.display_text,
        numbering_text=segment.numbering_text,
        reference_texts=[segment.matched_source_text],
    )


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
                source_html=segment.source_html,
                math_placeholders=segment.math_placeholders,
                sequence_index=segment.sequence_index,
                source_structure_changed=segment.source_structure_changed,
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
        if (
            not MATH_PLACEHOLDER_RE.search(replacement)
            and _span_contains_only_math_placeholders(tokens, span, expected_math_placeholders)
        ):
            _replace_math_only_span_with_plain_text(tokens, span, replacement)
            return
        raise ValueError("导出失败：译文中的数学公式占位符顺序或数量与原文不一致。")

    sentence_tokens = _collect_tokens_overlapping_span(tokens, span)
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
    actual_placeholders = [_canonical_math_placeholder(match.group(0)) for match in matches]
    if actual_placeholders != expected_math_placeholders:
        return None

    text_parts: list[str] = []
    cursor = 0
    for match in matches:
        text_parts.append(replacement[cursor:match.start()])
        cursor = match.end()
    text_parts.append(replacement[cursor:])
    return text_parts


def _canonical_math_placeholder(placeholder: str) -> str:
    match = MATH_PLACEHOLDER_TOKEN_RE.match(placeholder)
    if not match:
        return placeholder
    return f"⟦{match.group(1)}⟧"


def _collect_tokens_overlapping_span(
    tokens: list[TextToken],
    span: SentenceSpan,
) -> list[TextToken]:
    return [
        token
        for token in tokens
        if token.start < span.end and token.end > span.start
    ]


def _span_contains_only_math_placeholders(
    tokens: list[TextToken],
    span: SentenceSpan,
    expected_math_placeholders: list[str],
) -> bool:
    span_tokens = _collect_tokens_overlapping_span(tokens, span)
    math_placeholders = [token.source_text for token in span_tokens if token.is_math]
    if math_placeholders != expected_math_placeholders:
        return False

    for token in span_tokens:
        if token.is_math:
            continue
        overlap_start = max(span.start, token.start)
        overlap_end = min(span.end, token.end)
        if overlap_end <= overlap_start:
            continue
        local_start = overlap_start - token.start
        local_end = overlap_end - token.start
        if normalize_text(token.display_text[local_start:local_end]):
            return False
    return bool(math_placeholders)


def _replace_math_only_span_with_plain_text(
    tokens: list[TextToken],
    span: SentenceSpan,
    replacement: str,
) -> None:
    math_tokens = [
        token
        for token in _collect_tokens_overlapping_span(tokens, span)
        if token.is_math and token.anchor_element is not None and token.container_element is not None
    ]
    if not math_tokens:
        return

    first_token = math_tokens[0]
    parent = first_token.container_element
    anchor = first_token.anchor_element
    if parent is None or anchor is None:
        return

    try:
        insert_index = list(parent).index(anchor)
    except ValueError:
        return
    for token in math_tokens:
        token_parent = token.container_element
        token_anchor = token.anchor_element
        if token_parent is None or token_anchor is None:
            continue
        try:
            token_parent.remove(token_anchor)
        except ValueError:
            continue

    if replacement:
        for run in _build_inserted_word_runs(replacement, None):
            parent.insert(insert_index, run)
            insert_index += 1


def _queue_text_region_replacement(
    tokens: list[TextToken],
    region_start: int,
    region_end: int,
    replacement_text: str,
    before_token: TextToken | None,
    after_token: TextToken | None,
) -> None:
    writable_overlaps: list[tuple[TextToken, int, int]] = []
    structural_line_break_tokens: list[TextToken] = []
    for token in tokens:
        if token.element is None:
            continue
        overlap_start = max(region_start, token.start)
        overlap_end = min(region_end, token.end)
        if overlap_end <= overlap_start:
            continue
        if _is_word_structural_line_break_token(token):
            structural_line_break_tokens.append(token)
            continue
        writable_overlaps.append((token, overlap_start - token.start, overlap_end - token.start))

    if writable_overlaps:
        _queue_text_range_edit(
            writable_overlaps,
            replacement_text,
            structural_line_break_tokens=structural_line_break_tokens,
        )
        return

    if not replacement_text:
        return

    _insert_text_run_between_tokens(replacement_text, before_token=before_token, after_token=after_token)


def _is_word_structural_line_break_token(token: TextToken) -> bool:
    return (
        token.display_text == "\n"
        and token.source_text == "\n"
        and token.element is not None
        and _local_name(token.element.tag) in {"br", "cr"}
        and _namespace_uri(token.element.tag) == NS["w"]
        and token.anchor_element is not None
        and token.container_element is not None
    )


def _queue_text_range_edit(
    writable_overlaps: list[tuple[TextToken, int, int]],
    replacement_text: str,
    structural_line_break_tokens: list[TextToken] | None = None,
) -> None:
    if not writable_overlaps:
        return
    structural_line_break_tokens = structural_line_break_tokens or []

    if ("\n" in replacement_text or structural_line_break_tokens) and _queue_structural_word_text_range_edit(
        writable_overlaps,
        replacement_text,
        structural_line_break_tokens,
    ):
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


def _queue_structural_word_text_range_edit(
    writable_overlaps: list[tuple[TextToken, int, int]],
    replacement_text: str,
    structural_line_break_tokens: list[TextToken],
) -> bool:
    replacement_token: TextToken | None = None
    for token, _, _ in writable_overlaps:
        if (
            token.run_element is not None
            and token.container_element is not None
            and token.anchor_element is not None
            and _namespace_uri(token.run_element.tag) == NS["w"]
        ):
            replacement_token = token
            break
    if replacement_token is None:
        return False

    parent = replacement_token.container_element
    anchor = replacement_token.anchor_element
    if parent is None or anchor is None:
        return False

    try:
        insert_index = list(parent).index(anchor)
    except ValueError:
        return False

    for run in _build_inserted_word_runs(replacement_text, replacement_token.run_element):
        parent.insert(insert_index, run)
        insert_index += 1

    for token, local_start, local_end in writable_overlaps:
        token.edits.append((local_start, local_end, ""))
        token.apply_export_font = False

    _remove_structural_line_break_tokens(structural_line_break_tokens)
    return True


def _remove_structural_line_break_tokens(tokens: list[TextToken]) -> None:
    removed: set[tuple[int, int]] = set()
    for token in tokens:
        parent = token.container_element
        anchor = token.anchor_element
        if parent is None or anchor is None:
            continue
        key = (id(parent), id(anchor))
        if key in removed:
            continue
        try:
            parent.remove(anchor)
        except ValueError:
            continue
        removed.add(key)


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
        for run in _build_inserted_word_runs(text, reference_run):
            parent.insert(index, run)
            index += 1
        return

    if before_token is not None and before_token.container_element is not None and before_token.anchor_element is not None:
        parent = before_token.container_element
        index = list(parent).index(before_token.anchor_element) + 1
        reference_run = _pick_reference_run(before_token, after_token)
        for run in _build_inserted_word_runs(text, reference_run):
            parent.insert(index, run)
            index += 1


def _pick_reference_run(
    before_token: TextToken | None,
    after_token: TextToken | None,
) -> ET.Element | None:
    for token in (before_token, after_token):
        if token is not None and token.run_element is not None:
            return token.run_element
    return None


def _build_inserted_word_runs(text: str, reference_run: ET.Element | None) -> list[ET.Element]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" not in normalized:
        return [_build_inserted_word_run(normalized, reference_run)] if normalized else []

    runs: list[ET.Element] = []
    lines = normalized.split("\n")
    for index, line in enumerate(lines):
        if line:
            runs.append(_build_inserted_word_run(line, reference_run))
        if index < len(lines) - 1:
            runs.append(_build_inserted_word_break_run(reference_run))
    return runs


def _build_word_run_shell(reference_run: ET.Element | None) -> ET.Element:
    if reference_run is not None and _namespace_uri(reference_run.tag) == NS["w"]:
        run_element = deepcopy(reference_run)
        for child in list(run_element):
            if _local_name(child.tag) != "rPr":
                run_element.remove(child)
    else:
        run_element = ET.Element(_qn("w", "r"))
    return run_element


def _build_inserted_word_run(text: str, reference_run: ET.Element | None) -> ET.Element:
    text = _sanitize_xml_text(text)
    run_element = _build_word_run_shell(reference_run)

    text_element = ET.Element(_qn("w", "t"))
    text_element.text = text
    if _needs_space_preserve(text):
        text_element.set(XML_SPACE_ATTR, "preserve")
    run_element.append(text_element)
    _apply_export_font(run_element)
    return run_element


def _build_inserted_word_break_run(reference_run: ET.Element | None) -> ET.Element:
    run_element = _build_word_run_shell(reference_run)
    run_element.append(ET.Element(_qn("w", "br")))
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
        anchor = first_token.anchor_element if first_token.anchor_element is not None else first_token.run_element
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

    _clear_explicit_format_run_properties(run_properties)

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


def _clear_explicit_format_run_properties(run_properties: ET.Element) -> None:
    for child in list(run_properties):
        if _namespace_uri(child.tag) == NS["w"] and _local_name(child.tag) in EXPLICIT_FORMAT_RUN_PROPERTIES:
            run_properties.remove(child)


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
    structural_line_break_tokens: list[TextToken] = []
    for token in tokens:
        if token.element is None:
            continue

        overlap_start = max(span.start, token.start)
        overlap_end = min(span.end, token.end)
        if overlap_end <= overlap_start:
            continue
        if _is_word_structural_line_break_token(token):
            structural_line_break_tokens.append(token)
            continue

        writable_overlaps.append(
            (token, overlap_start - token.start, overlap_end - token.start)
        )

    _queue_text_range_edit(
        writable_overlaps,
        replacement,
        structural_line_break_tokens=structural_line_break_tokens,
    )


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


def _collect_related_chart_part_names(stories: Iterable[StoryPart]) -> set[str]:
    part_names: set[str] = set()
    for story in stories:
        for part_name, _ in _iter_related_chart_parts(story.root, story):
            part_names.add(part_name)
    return part_names


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
    if block_type in {"table_cell", "textbox", "chart_text"}:
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
