from dataclasses import dataclass
from html import escape
from io import BytesIO
from itertools import count
from typing import Iterator

from docx import Document
from docx.document import Document as DocxDocument
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph
from sqlalchemy.orm import Session

from app.services.matcher import MatchStats, match_sentences_with_stats
from app.services.normalizer import normalize_text
from app.services.sentence_splitter import SENTENCE_ENDINGS


TRAILING_SENTENCE_CLOSERS = "\"'》】）)]」』"


@dataclass(frozen=True)
class SentenceSpan:
    start: int
    end: int


def build_docx_workspace(
    db: Session,
    raw_bytes: bytes,
    similarity_threshold: float = 0.6,
) -> dict:
    document = Document(BytesIO(raw_bytes))
    sentence_counter = count(1)

    html_parts: list[str] = []
    segments: list[dict] = []

    for block_index, block in enumerate(_iter_block_items(document)):
        if isinstance(block, Paragraph):
            paragraph_html, paragraph_segments = _render_paragraph(
                text=block.text,
                sentence_counter=sentence_counter,
                block_index=block_index,
                block_type="paragraph",
            )
            html_parts.append(paragraph_html)
            segments.extend(paragraph_segments)
            continue

        if isinstance(block, Table):
            table_html, table_segments = _render_table(
                table=block,
                sentence_counter=sentence_counter,
                block_index=block_index,
            )
            html_parts.append(table_html)
            segments.extend(table_segments)

    match_stats = _build_empty_match_stats()
    if segments:
        match_results, match_stats = match_sentences_with_stats(
            db=db,
            sentences=[segment["source_text"] for segment in segments],
            similarity_threshold=similarity_threshold,
        )
        for segment, match in zip(segments, match_results, strict=False):
            segment["status"] = match.status
            segment["score"] = match.score
            segment["matched_source_text"] = match.matched_source_text
            segment["target_text"] = match.target_text or ""

    return {
        "document_html": "".join(html_parts) or '<p class="doc-paragraph doc-empty"><br></p>',
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


def _render_table(
    table: Table,
    sentence_counter,
    block_index: int,
) -> tuple[str, list[dict]]:
    row_html_parts: list[str] = []
    table_segments: list[dict] = []

    for row_index, row in enumerate(table.rows):
        cell_html_parts: list[str] = []

        for cell_index, cell in enumerate(row.cells):
            cell_paragraphs: list[str] = []

            for paragraph in cell.paragraphs:
                paragraph_html, paragraph_segments = _render_paragraph(
                    text=paragraph.text,
                    sentence_counter=sentence_counter,
                    block_index=block_index,
                    block_type="table_cell",
                    row_index=row_index,
                    cell_index=cell_index,
                )
                cell_paragraphs.append(paragraph_html)
                table_segments.extend(paragraph_segments)

            if not cell_paragraphs:
                cell_paragraphs.append('<p class="doc-paragraph doc-empty"><br></p>')

            cell_html_parts.append(
                f'<td class="doc-table-cell">{"".join(cell_paragraphs)}</td>'
            )

        row_html_parts.append(f'<tr>{"".join(cell_html_parts)}</tr>')

    return f'<table class="doc-table"><tbody>{"".join(row_html_parts)}</tbody></table>', table_segments


def _render_paragraph(
    text: str,
    sentence_counter,
    block_index: int,
    block_type: str,
    row_index: int | None = None,
    cell_index: int | None = None,
) -> tuple[str, list[dict]]:
    spans = _split_sentence_spans(text)
    if not spans:
        if normalize_text(text):
            spans = [_trimmed_span(text)]
        else:
            return '<p class="doc-paragraph doc-empty"><br></p>', []

    html_fragments: list[str] = []
    segments: list[dict] = []
    cursor = 0

    for span in spans:
        if span.start > cursor:
            html_fragments.append(escape(text[cursor:span.start]))

        display_text = text[span.start:span.end]
        source_text = normalize_text(display_text)
        if not source_text:
            cursor = span.end
            continue

        sentence_id = f"sent-{next(sentence_counter):05d}"
        html_fragments.append(
            f'<span class="doc-sentence" id="{sentence_id}" data-sentence-id="{sentence_id}">'
            f"{escape(display_text)}"
            "</span>"
        )
        segments.append(
            {
                "sentence_id": sentence_id,
                "source_text": source_text,
                "display_text": display_text,
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
        cursor = span.end

    if cursor < len(text):
        html_fragments.append(escape(text[cursor:]))

    paragraph_class = "doc-paragraph"
    if block_type == "table_cell":
        paragraph_class += " doc-table-paragraph"

    return f'<p class="{paragraph_class}">{"".join(html_fragments)}</p>', segments


def _split_sentence_spans(text: str) -> list[SentenceSpan]:
    if not text:
        return []

    spans: list[SentenceSpan] = []
    start: int | None = None
    index = 0

    while index < len(text):
        current_char = text[index]

        if start is None and not current_char.isspace():
            start = index

        if start is None:
            index += 1
            continue

        if current_char in SENTENCE_ENDINGS:
            end = index + 1
            while end < len(text) and text[end] in SENTENCE_ENDINGS:
                end += 1
            while end < len(text) and text[end] in TRAILING_SENTENCE_CLOSERS:
                end += 1

            spans.append(SentenceSpan(start=start, end=end))
            start = None
            index = end
            continue

        index += 1

    if start is not None:
        spans.append(_trimmed_span(text, start))

    return [span for span in spans if normalize_text(text[span.start:span.end])]


def _trimmed_span(text: str, start: int | None = None) -> SentenceSpan:
    left = 0 if start is None else start
    right = len(text)

    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1

    return SentenceSpan(start=left, end=right)


def _iter_block_items(parent: DocxDocument | _Cell) -> Iterator[Paragraph | Table]:
    if isinstance(parent, DocxDocument):
        parent_element = parent.element.body
    else:
        parent_element = parent._tc

    for child in parent_element.iterchildren():
        if child.tag.endswith("}p"):
            yield Paragraph(child, parent)
            continue
        if child.tag.endswith("}tbl"):
            yield Table(child, parent)


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
    """从数据库 segments 重建预览 HTML"""
    if not segments:
        return ""

    html_parts: list[str] = []
    current_block_index = -1
    current_block_type = None
    current_table_row = -1
    paragraph_buffer: list[str] = []
    table_rows: list[list[list[str]]] = []  # rows -> cells -> paragraphs

    def flush_paragraph():
        nonlocal paragraph_buffer
        if paragraph_buffer:
            html_parts.append(f'<p class="doc-paragraph">{"".join(paragraph_buffer)}</p>')
            paragraph_buffer = []

    def flush_table():
        nonlocal table_rows
        if table_rows:
            row_html = []
            for row in table_rows:
                cell_html = []
                for cell_paragraphs in row:
                    if cell_paragraphs:
                        cell_content = "".join(f'<p class="doc-paragraph">{p}</p>' for p in cell_paragraphs)
                    else:
                        cell_content = '<p class="doc-paragraph doc-empty"><br></p>'
                    cell_html.append(f'<td class="doc-table-cell">{cell_content}</td>')
                row_html.append(f'<tr>{"".join(cell_html)}</tr>')
            html_parts.append(f'<table class="doc-table"><tbody>{"".join(row_html)}</tbody></table>')
            table_rows = []

    for seg in segments:
        block_index = seg.block_index
        block_type = seg.block_type
        sentence_id = seg.sentence_id
        display_text = escape(seg.display_text)

        sentence_html = f'<span class="doc-sentence" id="{sentence_id}" data-sentence-id="{sentence_id}">{display_text}</span>'

        if block_type == "paragraph":
            if current_block_type == "table_cell":
                flush_table()
            if block_index != current_block_index:
                flush_paragraph()
            paragraph_buffer.append(sentence_html)
            current_block_index = block_index
            current_block_type = "paragraph"

        elif block_type == "table_cell":
            if current_block_type == "paragraph":
                flush_paragraph()

            row_index = seg.row_index or 0
            cell_index = seg.cell_index or 0

            # 确保 table_rows 有足够的行和列
            while len(table_rows) <= row_index:
                table_rows.append([])
            while len(table_rows[row_index]) <= cell_index:
                table_rows[row_index].append([])

            # 同一个 cell 的内容追加
            if current_block_index == block_index and current_table_row == row_index:
                if table_rows[row_index][cell_index]:
                    table_rows[row_index][cell_index][-1] += sentence_html
                else:
                    table_rows[row_index][cell_index].append(sentence_html)
            else:
                table_rows[row_index][cell_index].append(sentence_html)

            current_block_index = block_index
            current_block_type = "table_cell"
            current_table_row = row_index

    # 清空缓冲区
    flush_paragraph()
    flush_table()

    return "".join(html_parts) or '<p class="doc-paragraph doc-empty"><br></p>'
