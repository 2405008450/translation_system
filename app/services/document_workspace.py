from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from io import BytesIO
from itertools import count
import posixpath
import re
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from sqlalchemy.orm import Session

from app.services.matcher import MatchStats, match_sentences_with_stats
from app.services.normalizer import normalize_text
from app.services.sentence_splitter import SentenceSpan, split_sentence_spans


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "rels": "http://schemas.openxmlformats.org/package/2006/relationships",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}

TRUTHY_VALUES = {"1", "on", "true"}
FALSEY_VALUES = {"0", "false", "off", "none"}
NOTE_REFERENCE_CSS = "vertical-align: super; font-size: 0.75em;"
HIGHLIGHT_COLORS = {
    "black": "#000000",
    "blue": "#00b0f0",
    "cyan": "#00ffff",
    "darkBlue": "#00008b",
    "darkCyan": "#008b8b",
    "darkGray": "#a9a9a9",
    "darkGreen": "#006400",
    "darkMagenta": "#8b008b",
    "darkRed": "#8b0000",
    "darkYellow": "#b8860b",
    "green": "#00ff00",
    "lightGray": "#d3d3d3",
    "magenta": "#ff00ff",
    "red": "#ff0000",
    "white": "#ffffff",
    "yellow": "#ffff00",
}
PAGE_NUMBER_FIELD_NAMES = {"PAGE", "NUMPAGES", "SECTIONPAGES"}
NUMBERING_PLACEHOLDER_RE = re.compile(r"%(\d+)")
PAGE_NUMBER_WRAPPER_RE = re.compile(r"[\s\-\u2013\u2014\u2212\uff0d_./\\|:：()\[\]{}（）【】<>《》·•]+")
CELL_SENTENCE_END_CHARS = frozenset("。？！?!.；;:：")
CELL_SHORT_PARAGRAPH_MAX_CHARS = 20
CELL_NEXT_PARAGRAPH_MAX_CHARS = 50
CELL_GROUP_MAX_CHARS = 200
CELL_PARAGRAPH_BREAK_SENTINEL = "\uE000"


@dataclass(frozen=True)
class InlineFragment:
    display_text: str
    source_text: str
    css: str = ""
    href: str | None = None


CELL_PARAGRAPH_BREAK_FRAGMENT = InlineFragment(
    display_text="\n",
    source_text=CELL_PARAGRAPH_BREAK_SENTINEL,
)


@dataclass(frozen=True)
class RenderableSentence:
    span: SentenceSpan
    sentence_id: str | None
    display_text: str
    source_text: str


@dataclass
class TrackedField:
    instruction_parts: list[str] = field(default_factory=list)
    collecting_instruction: bool = True
    suppress_result: bool = False


@dataclass
class InlineParseState:
    field_stack: list[TrackedField] = field(default_factory=list)
    suppressed_page_number_field: bool = False


@dataclass
class ParsedParagraphRenderData:
    paragraph: ET.Element
    fragments: list[InlineFragment]
    textbox_html_parts: list[str]
    textbox_segments: list[dict]
    numbering_text: str
    paragraph_css: str
    suppressed_page_number_field: bool


@dataclass(frozen=True)
class StoryPart:
    kind: str
    label: str
    part_name: str
    root: ET.Element
    rels: dict[str, str]


@dataclass(frozen=True)
class NumberingLevel:
    ilvl: int
    num_fmt: str
    lvl_text: str
    start: int = 1
    suffix: str = "tab"
    p_style: str | None = None


@dataclass(frozen=True)
class NumberingInstance:
    num_id: str
    abstract_num_id: str
    level_overrides: dict[int, NumberingLevel]
    start_overrides: dict[int, int]


@dataclass(frozen=True)
class StyleDefinition:
    style_id: str
    based_on: str | None
    num_id: str | None
    ilvl: int | None


@dataclass(frozen=True)
class NumberingSchema:
    abstract_levels: dict[str, dict[int, NumberingLevel]]
    instances: dict[str, NumberingInstance]
    styles: dict[str, StyleDefinition]
    style_numbering_map: dict[str, tuple[str, int]]


@dataclass
class NumberingState:
    schema: NumberingSchema
    counters: dict[str, dict[int, int]] = field(default_factory=dict)


class DocxPackage:
    def __init__(self, raw_bytes: bytes):
        try:
            self._archive = ZipFile(BytesIO(raw_bytes))
        except BadZipFile as exc:
            raise ValueError("Invalid DOCX package.") from exc
        self._xml_cache: dict[str, ET.Element | None] = {}
        self._rels_cache: dict[str, dict[str, str]] = {}

    def read_xml(self, part_name: str) -> ET.Element | None:
        normalized_name = part_name.lstrip("/")
        if normalized_name in self._xml_cache:
            return self._xml_cache[normalized_name]

        try:
            xml_bytes = self._archive.read(normalized_name)
        except KeyError:
            self._xml_cache[normalized_name] = None
            return None

        root = ET.fromstring(xml_bytes)
        self._xml_cache[normalized_name] = root
        return root

    def read_relationships(self, part_name: str) -> dict[str, str]:
        normalized_name = part_name.lstrip("/")
        if normalized_name in self._rels_cache:
            return self._rels_cache[normalized_name]

        directory = posixpath.dirname(normalized_name)
        basename = posixpath.basename(normalized_name)
        rels_name = posixpath.join(directory, "_rels", f"{basename}.rels")
        root = self.read_xml(rels_name)
        if root is None:
            self._rels_cache[normalized_name] = {}
            return {}

        rels: dict[str, str] = {}
        for rel in root.findall("rels:Relationship", NS):
            rel_id = rel.get("Id")
            target = rel.get("Target")
            target_mode = rel.get("TargetMode")
            if not rel_id or not target:
                continue
            if target_mode == "External":
                rels[rel_id] = target
                continue
            rels[rel_id] = posixpath.normpath(posixpath.join(directory, target))

        self._rels_cache[normalized_name] = rels
        return rels


def _build_numbering_schema(package: DocxPackage) -> NumberingSchema:
    abstract_levels: dict[str, dict[int, NumberingLevel]] = {}
    instances: dict[str, NumberingInstance] = {}
    styles = _parse_style_definitions(package.read_xml("word/styles.xml"))

    numbering_root = package.read_xml("word/numbering.xml")
    if numbering_root is None:
        return NumberingSchema(
            abstract_levels={},
            instances={},
            styles=styles,
            style_numbering_map={},
        )

    for abstract_num in numbering_root.findall("./w:abstractNum", NS):
        abstract_num_id = abstract_num.get(_qn("w", "abstractNumId"))
        if not abstract_num_id:
            continue
        levels: dict[int, NumberingLevel] = {}
        for level in abstract_num.findall("./w:lvl", NS):
            parsed_level = _parse_numbering_level(level)
            if parsed_level is None:
                continue
            levels[parsed_level.ilvl] = parsed_level
        abstract_levels[abstract_num_id] = levels

    for num in numbering_root.findall("./w:num", NS):
        num_id = num.get(_qn("w", "numId"))
        abstract_num_id_element = num.find("./w:abstractNumId", NS)
        abstract_num_id = (
            None if abstract_num_id_element is None else abstract_num_id_element.get(_qn("w", "val"))
        )
        if not num_id or not abstract_num_id:
            continue

        level_overrides: dict[int, NumberingLevel] = {}
        start_overrides: dict[int, int] = {}
        for level_override in num.findall("./w:lvlOverride", NS):
            ilvl_value = level_override.get(_qn("w", "ilvl"))
            if ilvl_value is None or not ilvl_value.isdigit():
                continue
            ilvl = int(ilvl_value)

            start_override = level_override.find("./w:startOverride", NS)
            start_value = None if start_override is None else start_override.get(_qn("w", "val"))
            if start_value and start_value.isdigit():
                start_overrides[ilvl] = int(start_value)

            level_element = level_override.find("./w:lvl", NS)
            parsed_level = _parse_numbering_level(level_element)
            if parsed_level is not None:
                level_overrides[ilvl] = parsed_level

        instances[num_id] = NumberingInstance(
            num_id=num_id,
            abstract_num_id=abstract_num_id,
            level_overrides=level_overrides,
            start_overrides=start_overrides,
        )

    style_numbering_map: dict[str, tuple[str, int]] = {}
    for num_id, instance in instances.items():
        for ilvl, level in _iter_instance_levels(instance, abstract_levels):
            if level.p_style and level.p_style not in style_numbering_map:
                style_numbering_map[level.p_style] = (num_id, ilvl)

    return NumberingSchema(
        abstract_levels=abstract_levels,
        instances=instances,
        styles=styles,
        style_numbering_map=style_numbering_map,
    )


def _parse_style_definitions(styles_root: ET.Element | None) -> dict[str, StyleDefinition]:
    if styles_root is None:
        return {}

    styles: dict[str, StyleDefinition] = {}
    for style in styles_root.findall("./w:style", NS):
        if style.get(_qn("w", "type")) != "paragraph":
            continue

        style_id = style.get(_qn("w", "styleId"))
        if not style_id:
            continue

        based_on = None
        based_on_element = style.find("./w:basedOn", NS)
        if based_on_element is not None:
            based_on = based_on_element.get(_qn("w", "val"))

        num_id = None
        ilvl = None
        num_pr = style.find("./w:pPr/w:numPr", NS)
        if num_pr is not None:
            num_id_element = num_pr.find("./w:numId", NS)
            ilvl_element = num_pr.find("./w:ilvl", NS)
            if num_id_element is not None:
                num_id = num_id_element.get(_qn("w", "val"))
            if ilvl_element is not None:
                ilvl_value = ilvl_element.get(_qn("w", "val"))
                if ilvl_value and ilvl_value.isdigit():
                    ilvl = int(ilvl_value)

        styles[style_id] = StyleDefinition(
            style_id=style_id,
            based_on=based_on,
            num_id=num_id,
            ilvl=ilvl,
        )

    return styles


def _parse_numbering_level(level: ET.Element | None) -> NumberingLevel | None:
    if level is None:
        return None

    ilvl_value = level.get(_qn("w", "ilvl"))
    if ilvl_value is None or not ilvl_value.isdigit():
        return None

    start_value = 1
    start_element = level.find("./w:start", NS)
    if start_element is not None:
        raw_start = start_element.get(_qn("w", "val"))
        if raw_start and raw_start.isdigit():
            start_value = int(raw_start)

    num_fmt = "decimal"
    num_fmt_element = level.find("./w:numFmt", NS)
    if num_fmt_element is not None:
        num_fmt = num_fmt_element.get(_qn("w", "val"), "decimal")

    lvl_text = f"%{int(ilvl_value) + 1}."
    lvl_text_element = level.find("./w:lvlText", NS)
    if lvl_text_element is not None:
        lvl_text = lvl_text_element.get(_qn("w", "val"), lvl_text)

    suffix = "tab"
    suffix_element = level.find("./w:suff", NS)
    if suffix_element is not None:
        suffix = suffix_element.get(_qn("w", "val"), "tab")

    p_style = None
    p_style_element = level.find("./w:pStyle", NS)
    if p_style_element is not None:
        p_style = p_style_element.get(_qn("w", "val"))

    return NumberingLevel(
        ilvl=int(ilvl_value),
        num_fmt=num_fmt,
        lvl_text=lvl_text,
        start=start_value,
        suffix=suffix,
        p_style=p_style,
    )


def _iter_instance_levels(
    instance: NumberingInstance,
    abstract_levels: dict[str, dict[int, NumberingLevel]],
):
    levels = abstract_levels.get(instance.abstract_num_id, {})
    all_levels = set(levels) | set(instance.level_overrides)
    for ilvl in sorted(all_levels):
        resolved_level = instance.level_overrides.get(ilvl) or levels.get(ilvl)
        if resolved_level is not None:
            yield ilvl, resolved_level


def build_docx_workspace(
    db: Session,
    raw_bytes: bytes,
    similarity_threshold: float = 0.6,
    include_matches: bool = True,
) -> dict:
    parsed_workspace = parse_docx_workspace(raw_bytes)
    segments = parsed_workspace["segments"]
    match_stats = _build_empty_match_stats()

    if include_matches and segments:
        match_results, match_stats = match_sentences_with_stats(
            db=db,
            sentences=[segment["source_text"] for segment in segments],
            auxiliary_sentences=[
                _build_auxiliary_match_sentence(segment.get("numbering_text", ""), segment["source_text"])
                for segment in segments
            ],
            similarity_threshold=similarity_threshold,
        )
        for segment, match in zip(segments, match_results):
            segment["status"] = match.status
            segment["score"] = match.score
            segment["matched_source_text"] = match.matched_source_text
            segment["target_text"] = match.target_text or ""

    return {
        "document_html": parsed_workspace["document_html"],
        "segments": segments,
        "match_stats": {
            "total_input_sentences": match_stats.total_input_sentences,
            "prepared_sentences": match_stats.prepared_sentences,
            "unique_sentences": match_stats.unique_sentences,
            "exact_hits": match_stats.exact_hits,
            "fuzzy_hits": match_stats.fuzzy_hits,
            "none_hits": match_stats.none_hits,
            "exact_phase_ms": match_stats.exact_phase_ms,
            "fuzzy_phase_ms": match_stats.fuzzy_phase_ms,
            "total_match_ms": match_stats.total_match_ms,
            "fuzzy_candidates_evaluated": match_stats.fuzzy_candidates_evaluated,
        },
    }


def parse_docx_workspace(raw_bytes: bytes) -> dict:
    package = DocxPackage(raw_bytes)
    numbering_schema = _build_numbering_schema(package)
    sentence_counter = count(1)
    block_counter = count(0)

    html_parts: list[str] = []
    segments: list[dict] = []
    for story in _build_story_parts(package):
        numbering_state = NumberingState(schema=numbering_schema)
        story_html_parts, story_segments = _render_block_sequence(
            container=story.root,
            story=story,
            sentence_counter=sentence_counter,
            block_counter=block_counter,
            numbering_state=numbering_state,
        )
        if not story_html_parts:
            continue

        story_html = "".join(story_html_parts)
        if story.kind == "body":
            html_parts.append(f'<section class="doc-story doc-story-body">{story_html}</section>')
        else:
            html_parts.append(
                f'<section class="doc-story doc-story-{story.kind}">'
                f'<div class="doc-story-label">{escape(story.label)}</div>'
                f"{story_html}"
                "</section>"
            )
        segments.extend(story_segments)

    return {
        "document_html": "".join(html_parts) or '<p class="doc-paragraph doc-empty"><br></p>',
        "segments": segments,
    }


def build_docx_preview_html(raw_bytes: bytes) -> str:
    return parse_docx_workspace(raw_bytes)["document_html"]


def _build_story_parts(package: DocxPackage) -> list[StoryPart]:
    document_part_name = "word/document.xml"
    document_root = package.read_xml(document_part_name)
    if document_root is None:
        raise ValueError("word/document.xml is missing.")

    document_rels = package.read_relationships(document_part_name)
    body = document_root.find("w:body", NS)
    if body is None:
        raise ValueError("word/document.xml does not contain a body.")

    stories: list[StoryPart] = []
    header_parts = _collect_story_reference_parts(body, document_rels, "headerReference")
    footer_parts = _collect_story_reference_parts(body, document_rels, "footerReference")

    for index, part_name in enumerate(header_parts, start=1):
        root = package.read_xml(part_name)
        if root is None:
            continue
        stories.append(
            StoryPart(
                kind="header",
                label=f"Header {index}",
                part_name=part_name,
                root=root,
                rels=package.read_relationships(part_name),
            )
        )

    stories.append(
        StoryPart(
            kind="body",
            label="Body",
            part_name=document_part_name,
            root=body,
            rels=document_rels,
        )
    )
    stories.extend(_build_note_story_parts(package, "word/footnotes.xml", "footnote", "Footnote"))
    stories.extend(_build_note_story_parts(package, "word/endnotes.xml", "endnote", "Endnote"))

    for index, part_name in enumerate(footer_parts, start=1):
        root = package.read_xml(part_name)
        if root is None:
            continue
        stories.append(
            StoryPart(
                kind="footer",
                label=f"Footer {index}",
                part_name=part_name,
                root=root,
                rels=package.read_relationships(part_name),
            )
        )

    return stories


def _collect_story_reference_parts(
    container: ET.Element,
    relationships: dict[str, str],
    reference_tag: str,
) -> list[str]:
    part_names: list[str] = []
    seen_parts: set[str] = set()

    for reference in container.findall(f".//w:sectPr/w:{reference_tag}", NS):
        rel_id = reference.get(_qn("r", "id"))
        if not rel_id:
            continue

        part_name = relationships.get(rel_id)
        if not part_name or part_name in seen_parts:
            continue

        seen_parts.add(part_name)
        part_names.append(part_name)

    return part_names


def _build_note_story_parts(
    package: DocxPackage,
    part_name: str,
    kind: str,
    label_prefix: str,
) -> list[StoryPart]:
    root = package.read_xml(part_name)
    if root is None:
        return []

    rels = package.read_relationships(part_name)
    note_type_attr = _qn("w", "type")
    note_id_attr = _qn("w", "id")
    stories: list[StoryPart] = []
    for note in root.findall(f"./w:{kind}", NS):
        if note.get(note_type_attr):
            continue

        note_id = note.get(note_id_attr, "?")
        stories.append(
            StoryPart(
                kind=kind,
                label=f"{label_prefix} {note_id}",
                part_name=part_name,
                root=note,
                rels=rels,
            )
        )
    return stories


def _render_block_sequence(
    container: ET.Element,
    story: StoryPart,
    sentence_counter,
    block_counter,
    numbering_state: NumberingState,
    default_block_type: str = "paragraph",
    fixed_block_index: int | None = None,
    row_index: int | None = None,
    cell_index: int | None = None,
) -> tuple[list[str], list[dict]]:
    html_parts: list[str] = []
    segments: list[dict] = []

    for block in _iter_block_nodes(container):
        block_html, block_segments = _render_block(
            block=block,
            story=story,
            sentence_counter=sentence_counter,
            block_counter=block_counter,
            numbering_state=numbering_state,
            default_block_type=default_block_type,
            fixed_block_index=fixed_block_index,
            row_index=row_index,
            cell_index=cell_index,
        )
        if block_html:
            html_parts.append(block_html)
        segments.extend(block_segments)

    return html_parts, segments


def _iter_block_nodes(container: ET.Element):
    for child in list(container):
        child_name = _local_name(child.tag)
        if child_name in {"p", "tbl"}:
            yield child
            continue

        if child_name == "sdt":
            content = child.find("w:sdtContent", NS)
            if content is not None:
                yield from _iter_block_nodes(content)
            continue

        if child_name in {"customXml", "ins", "moveFrom", "moveTo", "smartTag"}:
            yield from _iter_block_nodes(child)
            continue

        if child_name == "AlternateContent":
            for alternate_child in list(child):
                yield from _iter_block_nodes(alternate_child)


def _render_block(
    block: ET.Element,
    story: StoryPart,
    sentence_counter,
    block_counter,
    numbering_state: NumberingState,
    default_block_type: str,
    fixed_block_index: int | None,
    row_index: int | None,
    cell_index: int | None,
) -> tuple[str, list[dict]]:
    block_name = _local_name(block.tag)
    if block_name == "p":
        block_index = fixed_block_index if fixed_block_index is not None else next(block_counter)
        return _render_paragraph(
            paragraph=block,
            story=story,
            sentence_counter=sentence_counter,
            block_counter=block_counter,
            numbering_state=numbering_state,
            block_index=block_index,
            block_type=default_block_type,
            row_index=row_index,
            cell_index=cell_index,
        )

    if block_name == "tbl":
        return _render_table(
            table=block,
            story=story,
            sentence_counter=sentence_counter,
            block_counter=block_counter,
            numbering_state=numbering_state,
        )

    return "", []


def _normalize_segment_source_text(text: str) -> str:
    if not text:
        return ""
    return normalize_text(text.replace(CELL_PARAGRAPH_BREAK_SENTINEL, ""))


def _build_paragraph_classes(story_kind: str, block_type: str) -> list[str]:
    paragraph_classes = ["doc-paragraph"]
    if block_type == "table_cell":
        paragraph_classes.append("doc-table-paragraph")
    if block_type == "textbox":
        paragraph_classes.append("doc-textbox-paragraph")
    if story_kind != "body":
        paragraph_classes.append(f"doc-{story_kind}-paragraph")
    return paragraph_classes


def _collect_paragraph_render_data(
    paragraph: ET.Element,
    story: StoryPart,
    sentence_counter,
    block_counter,
    numbering_state: NumberingState,
) -> ParsedParagraphRenderData:
    parse_state = InlineParseState()
    numbering_text = _resolve_paragraph_numbering(paragraph, numbering_state)
    fragments, textbox_html_parts, textbox_segments = _collect_inline_content(
        node=paragraph,
        story=story,
        sentence_counter=sentence_counter,
        block_counter=block_counter,
        numbering_state=numbering_state,
        parse_state=parse_state,
    )
    if numbering_text:
        fragments = [
            InlineFragment(
                display_text=numbering_text,
                source_text="".join(" " if not char.isspace() else char for char in numbering_text),
            ),
            *fragments,
        ]

    return ParsedParagraphRenderData(
        paragraph=paragraph,
        fragments=fragments,
        textbox_html_parts=textbox_html_parts,
        textbox_segments=textbox_segments,
        numbering_text=numbering_text,
        paragraph_css=_build_paragraph_css(paragraph),
        suppressed_page_number_field=parse_state.suppressed_page_number_field,
    )


def _cell_paragraph_text(fragments: list[InlineFragment]) -> str:
    return normalize_text("".join(fragment.display_text for fragment in fragments))


def _cell_paragraph_text_length(fragments: list[InlineFragment]) -> int:
    return len(_cell_paragraph_text(fragments))


def _cell_paragraph_looks_incomplete(fragments: list[InlineFragment]) -> bool:
    text = "".join(fragment.display_text for fragment in fragments).rstrip()
    if not text:
        return False
    return text[-1] not in CELL_SENTENCE_END_CHARS


def _should_merge_cell_paragraphs(
    current: ParsedParagraphRenderData,
    next_paragraph: ParsedParagraphRenderData,
) -> bool:
    if not current.fragments or not next_paragraph.fragments:
        return False
    if current.textbox_html_parts or current.textbox_segments:
        return False
    if next_paragraph.textbox_html_parts or next_paragraph.textbox_segments:
        return False
    if next_paragraph.numbering_text:
        return False
    if not _cell_paragraph_looks_incomplete(current.fragments):
        return False

    next_length = _cell_paragraph_text_length(next_paragraph.fragments)
    if next_length == 0 or next_length > CELL_NEXT_PARAGRAPH_MAX_CHARS:
        return False
    if next_length <= CELL_SHORT_PARAGRAPH_MAX_CHARS:
        return True

    return _cell_paragraph_text_length(current.fragments) + next_length <= CELL_GROUP_MAX_CHARS


def _group_cell_paragraphs(
    paragraphs: list[ParsedParagraphRenderData],
) -> list[ParsedParagraphRenderData]:
    grouped_paragraphs: list[ParsedParagraphRenderData] = []
    current_group: ParsedParagraphRenderData | None = None

    for paragraph in paragraphs:
        paragraph_group = ParsedParagraphRenderData(
            paragraph=paragraph.paragraph,
            fragments=list(paragraph.fragments),
            textbox_html_parts=list(paragraph.textbox_html_parts),
            textbox_segments=list(paragraph.textbox_segments),
            numbering_text=paragraph.numbering_text,
            paragraph_css=paragraph.paragraph_css,
            suppressed_page_number_field=paragraph.suppressed_page_number_field,
        )
        if current_group is None:
            current_group = paragraph_group
            continue

        if _should_merge_cell_paragraphs(current_group, paragraph_group):
            current_group.fragments.extend((CELL_PARAGRAPH_BREAK_FRAGMENT, *paragraph_group.fragments))
            current_group.suppressed_page_number_field = (
                current_group.suppressed_page_number_field or paragraph_group.suppressed_page_number_field
            )
            continue

        grouped_paragraphs.append(current_group)
        current_group = paragraph_group

    if current_group is not None:
        grouped_paragraphs.append(current_group)

    return grouped_paragraphs


def _render_table_cell(
    cell: ET.Element,
    story: StoryPart,
    sentence_counter,
    block_counter,
    numbering_state: NumberingState,
    block_index: int,
    row_index: int,
    cell_index: int,
) -> tuple[list[str], list[dict]]:
    cell_inner_html_parts: list[str] = []
    cell_segments: list[dict] = []
    paragraph_buffer: list[ParsedParagraphRenderData] = []
    paragraph_classes = _build_paragraph_classes(story.kind, "table_cell")
    segment_block_type = _resolve_segment_block_type(story.kind, "table_cell")

    def flush_paragraphs() -> None:
        nonlocal paragraph_buffer
        if not paragraph_buffer:
            return

        for grouped_paragraph in _group_cell_paragraphs(paragraph_buffer):
            paragraph_html, paragraph_segments = _render_paragraph_from_fragments(
                fragments=grouped_paragraph.fragments,
                sentence_counter=sentence_counter,
                block_index=block_index,
                block_type=segment_block_type,
                paragraph_classes=paragraph_classes,
                paragraph_css=grouped_paragraph.paragraph_css,
                row_index=row_index,
                cell_index=cell_index,
                suppressed_page_number_field=grouped_paragraph.suppressed_page_number_field,
                numbering_text=grouped_paragraph.numbering_text,
            )
            if paragraph_html:
                cell_inner_html_parts.append(paragraph_html)
            cell_inner_html_parts.extend(grouped_paragraph.textbox_html_parts)
            cell_segments.extend(paragraph_segments)
            cell_segments.extend(grouped_paragraph.textbox_segments)

        paragraph_buffer = []

    for block in _iter_block_nodes(cell):
        block_name = _local_name(block.tag)
        if block_name == "p":
            paragraph_buffer.append(
                _collect_paragraph_render_data(
                    paragraph=block,
                    story=story,
                    sentence_counter=sentence_counter,
                    block_counter=block_counter,
                    numbering_state=numbering_state,
                )
            )
            continue

        if block_name == "tbl":
            flush_paragraphs()
            nested_table_html, nested_table_segments = _render_table(
                table=block,
                story=story,
                sentence_counter=sentence_counter,
                block_counter=block_counter,
                numbering_state=numbering_state,
            )
            if nested_table_html:
                cell_inner_html_parts.append(nested_table_html)
            cell_segments.extend(nested_table_segments)

    flush_paragraphs()
    return cell_inner_html_parts, cell_segments


def _render_table(
    table: ET.Element,
    story: StoryPart,
    sentence_counter,
    block_counter,
    numbering_state: NumberingState,
) -> tuple[str, list[dict]]:
    block_index = next(block_counter)
    row_html_parts: list[str] = []
    table_segments: list[dict] = []

    for row_index, row in enumerate(table.findall("./w:tr", NS)):
        cell_html_parts: list[str] = []

        for cell_index, cell in enumerate(row.findall("./w:tc", NS)):
            cell_inner_html_parts, cell_segments = _render_table_cell(
                cell=cell,
                story=story,
                sentence_counter=sentence_counter,
                block_counter=block_counter,
                numbering_state=numbering_state,
                block_index=block_index,
                row_index=row_index,
                cell_index=cell_index,
            )
            table_segments.extend(cell_segments)

            if not cell_inner_html_parts:
                cell_inner_html_parts.append('<p class="doc-paragraph doc-empty"><br></p>')

            cell_style = _build_cell_css(cell)
            cell_style_attr = f' style="{cell_style}"' if cell_style else ""
            cell_span_attrs = _build_cell_span_attrs(cell)
            cell_html_parts.append(
                f'<td class="doc-table-cell"{cell_span_attrs}{cell_style_attr}>'
                f'{"".join(cell_inner_html_parts)}'
                "</td>"
            )

        row_html_parts.append(f'<tr>{"".join(cell_html_parts)}</tr>')

    return f'<table class="doc-table"><tbody>{"".join(row_html_parts)}</tbody></table>', table_segments


def _render_paragraph(
    paragraph: ET.Element,
    story: StoryPart,
    sentence_counter,
    block_counter,
    numbering_state: NumberingState,
    block_index: int,
    block_type: str,
    row_index: int | None,
    cell_index: int | None,
) -> tuple[str, list[dict]]:
    paragraph_data = _collect_paragraph_render_data(
        paragraph=paragraph,
        story=story,
        sentence_counter=sentence_counter,
        block_counter=block_counter,
        numbering_state=numbering_state,
    )
    paragraph_html, paragraph_segments = _render_paragraph_from_fragments(
        fragments=paragraph_data.fragments,
        sentence_counter=sentence_counter,
        block_index=block_index,
        block_type=_resolve_segment_block_type(story.kind, block_type),
        paragraph_classes=_build_paragraph_classes(story.kind, block_type),
        paragraph_css=paragraph_data.paragraph_css,
        row_index=row_index,
        cell_index=cell_index,
        suppressed_page_number_field=paragraph_data.suppressed_page_number_field,
        numbering_text=paragraph_data.numbering_text,
    )

    html_parts: list[str] = []
    if paragraph_html:
        html_parts.append(paragraph_html)
    html_parts.extend(paragraph_data.textbox_html_parts)

    return "".join(html_parts), paragraph_segments + paragraph_data.textbox_segments


def _render_paragraph_from_fragments(
    fragments: list[InlineFragment],
    sentence_counter,
    block_index: int,
    block_type: str,
    paragraph_classes: list[str],
    paragraph_css: str = "",
    row_index: int | None = None,
    cell_index: int | None = None,
    suppressed_page_number_field: bool = False,
    numbering_text: str = "",
) -> tuple[str, list[dict]]:
    display_text = "".join(fragment.display_text for fragment in fragments)
    if not display_text:
        return "", []
    if suppressed_page_number_field and _is_page_number_wrapper_only(display_text, block_type):
        return "", []

    spans = split_sentence_spans(display_text)
    if not spans and normalize_text(display_text):
        spans = [_build_trimmed_span(display_text)]

    renderable_sentences: list[RenderableSentence] = []
    segments: list[dict] = []
    numbering_available = bool(numbering_text)
    for span in spans:
        sentence_display = _collect_span_text(fragments, span, use_source=False)
        sentence_source = _normalize_segment_source_text(_collect_span_text(fragments, span, use_source=True))
        sentence_id = None
        segment_numbering_text = numbering_text if numbering_available else ""
        if sentence_source:
            sentence_id = f"sent-{next(sentence_counter):05d}"
            segments.append(
                {
                    "sentence_id": sentence_id,
                    "source_text": sentence_source,
                    "display_text": sentence_display,
                    "numbering_text": segment_numbering_text,
                    "status": "none",
                    "score": 0.0,
                    "matched_source_text": None,
                    "target_text": "",
                    "block_type": block_type,
                    "block_index": block_index,
                    "row_index": row_index,
                    "cell_index": cell_index,
                }
            )
            numbering_available = False

        renderable_sentences.append(
            RenderableSentence(
                span=span,
                sentence_id=sentence_id,
                display_text=sentence_display,
                source_text=sentence_source,
            )
        )

    html_content = _render_fragments_with_sentences(fragments, renderable_sentences)
    if not html_content:
        html_content = "<br>"

    class_attr = " ".join(paragraph_classes)
    style_attr = f' style="{paragraph_css}"' if paragraph_css else ""
    return f'<p class="{class_attr}"{style_attr}>{html_content}</p>', segments


def _resolve_paragraph_numbering(
    paragraph: ET.Element,
    numbering_state: NumberingState,
) -> str:
    numbering_reference = _resolve_paragraph_numbering_reference(paragraph, numbering_state.schema)
    if numbering_reference is None:
        return ""

    num_id, ilvl = numbering_reference
    instance = numbering_state.schema.instances.get(num_id)
    if instance is None:
        return ""

    level = _resolve_numbering_level(numbering_state.schema, instance, ilvl)
    if level is None:
        return ""

    counters = numbering_state.counters.setdefault(num_id, {})
    for deeper_level in [value for value in counters if value > ilvl]:
        del counters[deeper_level]

    start_value = instance.start_overrides.get(ilvl, level.start)
    current_value = counters.get(ilvl, start_value - 1) + 1
    counters[ilvl] = current_value

    rendered_text = NUMBERING_PLACEHOLDER_RE.sub(
        lambda match: _render_numbering_placeholder(
            numbering_state.schema,
            instance,
            counters,
            match.group(1),
        ),
        level.lvl_text,
    )
    if not rendered_text:
        return ""

    return f"{rendered_text}{_numbering_suffix_text(level.suffix)}"


def _resolve_paragraph_numbering_reference(
    paragraph: ET.Element,
    numbering_schema: NumberingSchema,
) -> tuple[str, int] | None:
    direct_reference = _extract_numpr_reference(paragraph.find("./w:pPr/w:numPr", NS))
    if direct_reference is not None:
        return direct_reference

    paragraph_style_id = _get_paragraph_style_id(paragraph)
    if not paragraph_style_id:
        return None

    style_reference = _resolve_style_numbering_reference(paragraph_style_id, numbering_schema.styles)
    if style_reference is not None:
        return style_reference

    return numbering_schema.style_numbering_map.get(paragraph_style_id)


def _extract_numpr_reference(num_pr: ET.Element | None) -> tuple[str, int] | None:
    if num_pr is None:
        return None

    num_id_element = num_pr.find("./w:numId", NS)
    if num_id_element is None:
        return None

    num_id = num_id_element.get(_qn("w", "val"))
    if not num_id or num_id == "0":
        return None

    ilvl = 0
    ilvl_element = num_pr.find("./w:ilvl", NS)
    if ilvl_element is not None:
        ilvl_value = ilvl_element.get(_qn("w", "val"))
        if ilvl_value and ilvl_value.isdigit():
            ilvl = int(ilvl_value)

    return num_id, ilvl


def _get_paragraph_style_id(paragraph: ET.Element) -> str | None:
    style_element = paragraph.find("./w:pPr/w:pStyle", NS)
    if style_element is None:
        return None
    return style_element.get(_qn("w", "val"))


def _resolve_style_numbering_reference(
    style_id: str,
    styles: dict[str, StyleDefinition],
) -> tuple[str, int] | None:
    current_style_id = style_id
    visited: set[str] = set()

    while current_style_id and current_style_id not in visited:
        visited.add(current_style_id)
        style = styles.get(current_style_id)
        if style is None:
            return None
        if style.num_id:
            return style.num_id, style.ilvl or 0
        current_style_id = style.based_on

    return None


def _resolve_numbering_level(
    numbering_schema: NumberingSchema,
    instance: NumberingInstance,
    ilvl: int,
) -> NumberingLevel | None:
    if ilvl in instance.level_overrides:
        return instance.level_overrides[ilvl]
    return numbering_schema.abstract_levels.get(instance.abstract_num_id, {}).get(ilvl)


def _render_numbering_placeholder(
    numbering_schema: NumberingSchema,
    instance: NumberingInstance,
    counters: dict[int, int],
    placeholder_text: str,
) -> str:
    if not placeholder_text.isdigit():
        return ""

    target_level = int(placeholder_text) - 1
    if target_level < 0:
        return ""

    level = _resolve_numbering_level(numbering_schema, instance, target_level)
    value = counters.get(target_level)
    if level is None or value is None:
        return ""

    return _format_numbering_value(value, level.num_fmt, level.lvl_text)


def _numbering_suffix_text(suffix: str) -> str:
    if suffix == "space":
        return " "
    if suffix == "nothing":
        return ""
    return "\t"


def _format_numbering_value(value: int, num_fmt: str, lvl_text: str) -> str:
    if num_fmt in {"bullet", "none"}:
        return ""
    if num_fmt == "decimalZero":
        return f"{value:02d}"
    if num_fmt in {"upperLetter", "lowerLetter"}:
        rendered = _to_alpha_sequence(value)
        return rendered.upper() if num_fmt == "upperLetter" else rendered.lower()
    if num_fmt in {"upperRoman", "lowerRoman"}:
        rendered = _to_roman(value)
        return rendered.upper() if num_fmt == "upperRoman" else rendered.lower()
    if num_fmt in {
        "chineseCounting",
        "chineseLegalSimplified",
        "ideographDigital",
        "chineseCountingThousand",
    }:
        return _to_simplified_chinese_number(value)
    return str(value)


def _to_alpha_sequence(value: int) -> str:
    if value <= 0:
        return str(value)

    letters: list[str] = []
    current = value
    while current > 0:
        current -= 1
        letters.append(chr(ord("A") + (current % 26)))
        current //= 26
    return "".join(reversed(letters))


def _to_roman(value: int) -> str:
    if value <= 0:
        return str(value)

    numerals = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    current = value
    parts: list[str] = []
    for arabic, roman in numerals:
        while current >= arabic:
            parts.append(roman)
            current -= arabic
    return "".join(parts)


def _to_simplified_chinese_number(value: int) -> str:
    if value <= 0:
        return str(value)
    if value >= 100000:
        return str(value)

    digits = "零一二三四五六七八九"
    units = ("", "十", "百", "千", "万")
    parts: list[str] = []
    zero_pending = False

    for index, digit_char in enumerate(str(value)):
        digit = int(digit_char)
        place = len(str(value)) - index - 1
        if digit == 0:
            zero_pending = bool(parts)
            continue
        if zero_pending:
            parts.append("零")
            zero_pending = False
        if not (digit == 1 and place == 1 and not parts):
            parts.append(digits[digit])
        parts.append(units[place])

    return "".join(parts) or str(value)


def _collect_inline_content(
    node: ET.Element,
    story: StoryPart,
    sentence_counter,
    block_counter,
    numbering_state: NumberingState,
    parse_state: InlineParseState,
    hyperlink: str | None = None,
    inherited_css: str = "",
) -> tuple[list[InlineFragment], list[str], list[dict]]:
    node_name = _local_name(node.tag)
    if node_name in {"pPr", "rPr", "tblPr", "tblGrid", "trPr", "tcPr", "sectPr"}:
        return [], [], []

    if node_name == "fldSimple":
        instruction = node.get(_qn("w", "instr"), "")
        if _should_suppress_page_number_field(instruction, story.kind):
            parse_state.suppressed_page_number_field = True
            return [], [], []

    if node_name == "hyperlink":
        hyperlink = _resolve_hyperlink_target(node, story.rels) or hyperlink

    if node_name == "r":
        inherited_css = _merge_css(inherited_css, _build_run_css(node))

    if node_name == "fldChar":
        _update_field_state(node, story.kind, parse_state)
        return [], [], []

    if node_name == "instrText":
        if parse_state.field_stack and parse_state.field_stack[-1].collecting_instruction:
            parse_state.field_stack[-1].instruction_parts.append(node.text or "")
        return [], [], []

    if _is_inside_suppressed_page_number_field(parse_state.field_stack):
        if node_name in {"t", "tab", "br", "cr", "noBreakHyphen", "sym", "footnoteReference", "endnoteReference", "drawing", "pict"}:
            return [], [], []

    if node_name == "t":
        text_value = node.text or ""
        return [InlineFragment(display_text=text_value, source_text=text_value, css=inherited_css, href=hyperlink)], [], []

    if node_name == "tab":
        return [InlineFragment(display_text="\t", source_text="\t", css=inherited_css, href=hyperlink)], [], []

    if node_name in {"br", "cr"}:
        return [InlineFragment(display_text="\n", source_text="\n", css=inherited_css, href=hyperlink)], [], []

    if node_name == "noBreakHyphen":
        return [InlineFragment(display_text="-", source_text="-", css=inherited_css, href=hyperlink)], [], []

    if node_name == "sym":
        symbol_text = _decode_symbol(node)
        if not symbol_text:
            return [], [], []
        return [InlineFragment(display_text=symbol_text, source_text=symbol_text, css=inherited_css, href=hyperlink)], [], []

    if node_name in {"footnoteReference", "endnoteReference"}:
        return [_build_note_reference_fragment(node, inherited_css)], [], []

    if node_name in {"drawing", "pict"}:
        textbox_html_parts, textbox_segments = _render_embedded_textboxes(
            node=node,
            story=story,
            sentence_counter=sentence_counter,
            block_counter=block_counter,
            numbering_state=numbering_state,
        )
        return [], textbox_html_parts, textbox_segments

    fragments: list[InlineFragment] = []
    textbox_html_parts: list[str] = []
    textbox_segments: list[dict] = []
    for child in list(node):
        child_fragments, child_textbox_html_parts, child_textbox_segments = _collect_inline_content(
            node=child,
            story=story,
            sentence_counter=sentence_counter,
            block_counter=block_counter,
            numbering_state=numbering_state,
            parse_state=parse_state,
            hyperlink=hyperlink,
            inherited_css=inherited_css,
        )
        fragments.extend(child_fragments)
        textbox_html_parts.extend(child_textbox_html_parts)
        textbox_segments.extend(child_textbox_segments)

    return fragments, textbox_html_parts, textbox_segments


def _render_embedded_textboxes(
    node: ET.Element,
    story: StoryPart,
    sentence_counter,
    block_counter,
    numbering_state: NumberingState,
) -> tuple[list[str], list[dict]]:
    textbox_html_parts: list[str] = []
    textbox_segments: list[dict] = []

    for textbox_content in node.findall(".//w:txbxContent", NS):
        inner_html_parts, inner_segments = _render_block_sequence(
            container=textbox_content,
            story=story,
            sentence_counter=sentence_counter,
            block_counter=block_counter,
            numbering_state=numbering_state,
            default_block_type="textbox",
        )
        if not inner_html_parts:
            continue

        textbox_html_parts.append(f'<div class="doc-textbox">{"".join(inner_html_parts)}</div>')
        textbox_segments.extend(inner_segments)

    if textbox_html_parts:
        return textbox_html_parts, textbox_segments

    fallback_parts = [element.text for element in node.findall(".//a:t", NS) if element.text]
    fallback_text = "\n".join(part for part in fallback_parts if part)
    if not normalize_text(fallback_text):
        return [], []

    paragraph_html, paragraph_segments = _render_paragraph_from_fragments(
        fragments=[InlineFragment(display_text=fallback_text, source_text=fallback_text)],
        sentence_counter=sentence_counter,
        block_index=next(block_counter),
        block_type=_resolve_segment_block_type(story.kind, "textbox"),
        paragraph_classes=["doc-paragraph", "doc-textbox-paragraph"],
    )
    if not paragraph_html:
        return [], []

    return [f'<div class="doc-textbox">{paragraph_html}</div>'], paragraph_segments


def _update_field_state(
    node: ET.Element,
    story_kind: str,
    parse_state: InlineParseState,
) -> None:
    field_type = node.get(_qn("w", "fldCharType"))
    if field_type == "begin":
        parse_state.field_stack.append(TrackedField())
        return

    if not parse_state.field_stack:
        return

    current_field = parse_state.field_stack[-1]
    if field_type == "separate":
        current_field.collecting_instruction = False
        current_field.suppress_result = _should_suppress_page_number_field(
            "".join(current_field.instruction_parts),
            story_kind,
        )
        if current_field.suppress_result:
            parse_state.suppressed_page_number_field = True
        return

    if field_type == "end":
        parse_state.field_stack.pop()


def _should_suppress_page_number_field(instruction: str, story_kind: str) -> bool:
    if story_kind not in {"header", "footer"}:
        return False

    normalized = " ".join(instruction.upper().split())
    if not normalized:
        return False

    field_name_match = re.match(r"[A-Z]+", normalized)
    if not field_name_match:
        return False

    return field_name_match.group(0) in PAGE_NUMBER_FIELD_NAMES


def _is_inside_suppressed_page_number_field(field_stack: list[TrackedField]) -> bool:
    return any(field.suppress_result and not field.collecting_instruction for field in field_stack)


def _is_page_number_wrapper_only(text: str, block_type: str) -> bool:
    if block_type not in {"header", "footer"}:
        return False

    compact_text = PAGE_NUMBER_WRAPPER_RE.sub("", text).lower()
    if not compact_text:
        return True

    for token in ("pages", "page", "of", "第", "页", "共"):
        compact_text = compact_text.replace(token, "")

    return compact_text == ""


def _render_fragments_with_sentences(
    fragments: list[InlineFragment],
    sentences: list[RenderableSentence],
) -> str:
    if not fragments:
        return ""

    html_parts: list[str] = []
    sentence_index = 0
    current_sentence = sentences[sentence_index] if sentence_index < len(sentences) else None
    cursor = 0

    for fragment in fragments:
        fragment_length = len(fragment.display_text)
        local_start = 0

        while local_start < fragment_length:
            absolute_start = cursor + local_start

            while current_sentence is not None and absolute_start >= current_sentence.span.end:
                if current_sentence.sentence_id:
                    html_parts.append("</span>")
                sentence_index += 1
                current_sentence = sentences[sentence_index] if sentence_index < len(sentences) else None

            next_boundary = cursor + fragment_length
            if current_sentence is not None:
                if absolute_start < current_sentence.span.start:
                    next_boundary = min(next_boundary, current_sentence.span.start)
                else:
                    if absolute_start == current_sentence.span.start and current_sentence.sentence_id:
                        html_parts.append(
                            f'<span class="doc-sentence" id="{current_sentence.sentence_id}" '
                            f'data-sentence-id="{current_sentence.sentence_id}">'
                        )
                    next_boundary = min(next_boundary, current_sentence.span.end)

            piece_length = next_boundary - absolute_start
            piece = fragment.display_text[local_start : local_start + piece_length]
            html_parts.append(_render_fragment_piece(fragment, piece))
            local_start += piece_length

            if current_sentence is not None and cursor + local_start >= current_sentence.span.end:
                if current_sentence.sentence_id:
                    html_parts.append("</span>")
                sentence_index += 1
                current_sentence = sentences[sentence_index] if sentence_index < len(sentences) else None

        cursor += fragment_length

    return "".join(html_parts)


def _render_fragment_piece(fragment: InlineFragment, piece: str) -> str:
    if not piece:
        return ""

    piece_html = escape(piece.expandtabs(4))
    if fragment.css:
        piece_html = f'<span class="doc-run" style="{fragment.css}">{piece_html}</span>'
    if fragment.href:
        href = escape(fragment.href, quote=True)
        piece_html = (
            f'<a href="{href}" target="_blank" rel="noopener noreferrer">'
            f"{piece_html}"
            "</a>"
        )
    return piece_html


def _collect_span_text(
    fragments: list[InlineFragment],
    span: SentenceSpan,
    use_source: bool,
) -> str:
    pieces: list[str] = []
    cursor = 0

    for fragment in fragments:
        next_cursor = cursor + len(fragment.display_text)
        overlap_start = max(span.start, cursor)
        overlap_end = min(span.end, next_cursor)
        if overlap_end > overlap_start:
            local_start = overlap_start - cursor
            local_end = overlap_end - cursor
            base_text = fragment.source_text if use_source else fragment.display_text
            pieces.append(base_text[local_start:local_end])
        cursor = next_cursor

    return "".join(pieces)


def _build_trimmed_span(text: str) -> SentenceSpan:
    start = 0
    end = len(text)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return SentenceSpan(start=start, end=end)


def _build_auxiliary_match_sentence(numbering_text: str, source_text: str) -> str:
    if not numbering_text:
        return ""
    return normalize_text(f"{numbering_text} {source_text}")


def _build_note_reference_fragment(node: ET.Element, inherited_css: str) -> InlineFragment:
    note_id = node.get(_qn("w", "id"), "")
    marker = f"[{note_id}]" if note_id else "[*]"
    return InlineFragment(
        display_text=marker,
        source_text=" " * len(marker),
        css=_merge_css(inherited_css, NOTE_REFERENCE_CSS),
    )


def _resolve_hyperlink_target(node: ET.Element, relationships: dict[str, str]) -> str | None:
    rel_id = node.get(_qn("r", "id"))
    if rel_id and rel_id in relationships:
        return relationships[rel_id]

    anchor = node.get(_qn("w", "anchor"))
    if anchor:
        return f"#{anchor}"

    return None


def _decode_symbol(node: ET.Element) -> str:
    char_value = node.get(_qn("w", "char"))
    if not char_value:
        return ""
    try:
        return chr(int(char_value, 16))
    except ValueError:
        return ""


def _build_run_css(run: ET.Element) -> str:
    properties = run.find("w:rPr", NS)
    if properties is None:
        return ""

    styles: list[str] = []
    if _is_enabled(properties, "b"):
        styles.append("font-weight: 700")
    if _is_enabled(properties, "i"):
        styles.append("font-style: italic")

    text_decorations: list[str] = []
    underline = properties.find("w:u", NS)
    if underline is not None and underline.get(_qn("w", "val"), "single") != "none":
        text_decorations.append("underline")
    if _is_enabled(properties, "strike") or _is_enabled(properties, "dstrike"):
        text_decorations.append("line-through")
    if text_decorations:
        styles.append(f"text-decoration: {' '.join(text_decorations)}")

    color = properties.find("w:color", NS)
    color_value = None if color is None else color.get(_qn("w", "val"))
    if color_value and color_value not in {"auto", "000000"}:
        styles.append(f"color: #{color_value}")

    shading = properties.find("w:shd", NS)
    shading_fill = None if shading is None else shading.get(_qn("w", "fill"))
    if shading_fill and shading_fill.lower() != "auto":
        styles.append(f"background-color: #{shading_fill}")

    highlight = properties.find("w:highlight", NS)
    highlight_value = None if highlight is None else highlight.get(_qn("w", "val"))
    if highlight_value and highlight_value in HIGHLIGHT_COLORS:
        styles.append(f"background-color: {HIGHLIGHT_COLORS[highlight_value]}")

    font_size = properties.find("w:sz", NS)
    font_size_value = None if font_size is None else font_size.get(_qn("w", "val"))
    if font_size_value and font_size_value.isdigit():
        styles.append(f"font-size: {int(font_size_value) / 2:.1f}pt")

    fonts = properties.find("w:rFonts", NS)
    if fonts is not None:
        font_family = (
            fonts.get(_qn("w", "ascii"))
            or fonts.get(_qn("w", "hAnsi"))
            or fonts.get(_qn("w", "eastAsia"))
            or fonts.get(_qn("w", "cs"))
        )
        if font_family:
            safe_font_family = font_family.replace("'", r"\'")
            styles.append(f"font-family: '{safe_font_family}'")

    vertical_align = properties.find("w:vertAlign", NS)
    vertical_align_value = None if vertical_align is None else vertical_align.get(_qn("w", "val"))
    if vertical_align_value == "superscript":
        styles.append("vertical-align: super")
        styles.append("font-size: 0.75em")
    elif vertical_align_value == "subscript":
        styles.append("vertical-align: sub")
        styles.append("font-size: 0.75em")

    if _is_enabled(properties, "smallCaps"):
        styles.append("font-variant: small-caps")
    if _is_enabled(properties, "caps"):
        styles.append("text-transform: uppercase")

    return "; ".join(styles)


def _build_paragraph_css(paragraph: ET.Element) -> str:
    properties = paragraph.find("w:pPr", NS)
    if properties is None:
        return ""

    styles: list[str] = []

    alignment = properties.find("w:jc", NS)
    alignment_value = None if alignment is None else alignment.get(_qn("w", "val"))
    alignment_mapping = {
        "both": "justify",
        "center": "center",
        "distribute": "justify",
        "justify": "justify",
        "left": "left",
        "right": "right",
    }
    if alignment_value in alignment_mapping:
        styles.append(f"text-align: {alignment_mapping[alignment_value]}")

    indentation = properties.find("w:ind", NS)
    if indentation is not None:
        left = indentation.get(_qn("w", "left"))
        right = indentation.get(_qn("w", "right"))
        first_line = indentation.get(_qn("w", "firstLine"))
        hanging = indentation.get(_qn("w", "hanging"))
        if left and left.isdigit():
            styles.append(f"margin-left: {_twips_to_points(left):.2f}pt")
        if right and right.isdigit():
            styles.append(f"margin-right: {_twips_to_points(right):.2f}pt")
        if first_line and first_line.isdigit():
            styles.append(f"text-indent: {_twips_to_points(first_line):.2f}pt")
        elif hanging and hanging.isdigit():
            styles.append(f"text-indent: -{_twips_to_points(hanging):.2f}pt")

    spacing = properties.find("w:spacing", NS)
    if spacing is not None:
        before = spacing.get(_qn("w", "before"))
        after = spacing.get(_qn("w", "after"))
        if before and before.isdigit():
            styles.append(f"margin-top: {_twips_to_points(before):.2f}pt")
        if after and after.isdigit():
            styles.append(f"margin-bottom: {_twips_to_points(after):.2f}pt")

    shading = properties.find("w:shd", NS)
    shading_fill = None if shading is None else shading.get(_qn("w", "fill"))
    if shading_fill and shading_fill.lower() != "auto":
        styles.append(f"background-color: #{shading_fill}")

    return "; ".join(styles)


def _build_cell_css(cell: ET.Element) -> str:
    properties = cell.find("w:tcPr", NS)
    if properties is None:
        return ""

    styles: list[str] = []
    shading = properties.find("w:shd", NS)
    shading_fill = None if shading is None else shading.get(_qn("w", "fill"))
    if shading_fill and shading_fill.lower() != "auto":
        styles.append(f"background-color: #{shading_fill}")

    vertical_align = properties.find("w:vAlign", NS)
    vertical_align_value = None if vertical_align is None else vertical_align.get(_qn("w", "val"))
    if vertical_align_value in {"top", "center", "bottom"}:
        css_value = "middle" if vertical_align_value == "center" else vertical_align_value
        styles.append(f"vertical-align: {css_value}")

    return "; ".join(styles)


def _build_cell_span_attrs(cell: ET.Element) -> str:
    properties = cell.find("w:tcPr", NS)
    if properties is None:
        return ""

    grid_span = properties.find("w:gridSpan", NS)
    grid_span_value = None if grid_span is None else grid_span.get(_qn("w", "val"))
    if grid_span_value and grid_span_value.isdigit() and int(grid_span_value) > 1:
        return f' colspan="{int(grid_span_value)}"'

    return ""


def _resolve_segment_block_type(story_kind: str, block_type: str) -> str:
    if block_type in {"table_cell", "textbox"}:
        return block_type
    if story_kind in {"header", "footer", "footnote", "endnote"}:
        return story_kind
    return "paragraph"


def _is_enabled(properties: ET.Element, tag_name: str) -> bool:
    element = properties.find(f"w:{tag_name}", NS)
    if element is None:
        return False

    value = element.get(_qn("w", "val"))
    if value is None:
        return True

    normalized_value = value.strip().lower()
    if normalized_value in FALSEY_VALUES:
        return False
    if normalized_value in TRUTHY_VALUES:
        return True
    return True


def _twips_to_points(value: str) -> float:
    return int(value) / 20.0


def _merge_css(*values: str) -> str:
    return "; ".join(value for value in values if value)


def _qn(prefix: str, tag_name: str) -> str:
    return f"{{{NS[prefix]}}}{tag_name}"


def _local_name(tag: str) -> str:
    if "}" not in tag:
        return tag
    return tag.rsplit("}", 1)[-1]


def _build_empty_match_stats() -> MatchStats:
    return MatchStats(
        total_input_sentences=0,
        prepared_sentences=0,
        unique_sentences=0,
        exact_hits=0,
        fuzzy_hits=0,
        none_hits=0,
        exact_phase_ms=0.0,
        fuzzy_phase_ms=0.0,
        total_match_ms=0.0,
        fuzzy_candidates_evaluated=0,
    )


def build_document_html_from_segments(segments: list) -> str:
    if not segments:
        return ""

    html_parts: list[str] = []
    paragraph_buffer: list[str] = []
    current_paragraph_key = None
    current_table_index = None
    table_rows: list[list[list[str]]] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_buffer, current_paragraph_key
        if paragraph_buffer:
            html_parts.append(f'<p class="doc-paragraph">{"".join(paragraph_buffer)}</p>')
            paragraph_buffer = []
        current_paragraph_key = None

    def flush_table() -> None:
        nonlocal table_rows, current_table_index
        if table_rows:
            row_html: list[str] = []
            for row in table_rows:
                cell_html: list[str] = []
                for cell_sentences in row:
                    if cell_sentences:
                        cell_content = f'<p class="doc-paragraph">{"".join(cell_sentences)}</p>'
                    else:
                        cell_content = '<p class="doc-paragraph doc-empty"><br></p>'
                    cell_html.append(f'<td class="doc-table-cell">{cell_content}</td>')
                row_html.append(f'<tr>{"".join(cell_html)}</tr>')
            html_parts.append(f'<table class="doc-table"><tbody>{"".join(row_html)}</tbody></table>')
            table_rows = []
        current_table_index = None

    for segment in segments:
        block_type = getattr(segment, "block_type", "paragraph") or "paragraph"
        sentence_id = getattr(segment, "sentence_id", "")
        display_text = escape(getattr(segment, "display_text", ""))
        sentence_html = (
            f'<span class="doc-sentence" id="{sentence_id}" data-sentence-id="{sentence_id}">'
            f"{display_text}"
            "</span>"
        )

        if block_type == "table_cell":
            flush_paragraph()
            table_index = getattr(segment, "block_index", 0)
            if current_table_index is None:
                current_table_index = table_index
            elif current_table_index != table_index:
                flush_table()
                current_table_index = table_index

            row_index = getattr(segment, "row_index", 0) or 0
            cell_index = getattr(segment, "cell_index", 0) or 0
            while len(table_rows) <= row_index:
                table_rows.append([])
            while len(table_rows[row_index]) <= cell_index:
                table_rows[row_index].append([])
            table_rows[row_index][cell_index].append(sentence_html)
            continue

        flush_table()
        paragraph_key = (getattr(segment, "block_index", 0), block_type)
        if current_paragraph_key != paragraph_key:
            flush_paragraph()
            current_paragraph_key = paragraph_key
        paragraph_buffer.append(sentence_html)

    flush_paragraph()
    flush_table()

    return "".join(html_parts) or '<p class="doc-paragraph doc-empty"><br></p>'
