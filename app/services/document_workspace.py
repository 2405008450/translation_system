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


TRAILING_SENTENCE_CLOSERS = "\"'”’》】）)]」』"


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


def build_workspace_with_adapters(
    db: Session,
    raw_bytes: bytes,
    filename: str,
    similarity_threshold: float = 0.6,
) -> dict:
    """使用适配器系统构建翻译工作台
    
    支持多种文档格式：TXT、DOCX、PDF、PPTX、DITA、SVG 等。
    
    Args:
        db: 数据库会话
        raw_bytes: 文件字节内容
        filename: 文件名
        similarity_threshold: 模糊匹配阈值
        
    Returns:
        dict: 包含 document_html、segments、match_stats 的字典
    """
    from pathlib import Path
    from app.services.adapters import get_registry, ParseError, FileTooLargeError
    
    extension = Path(filename).suffix.lower()
    
    # 对于 DOCX，使用原有的专用解析器以保持完整的格式支持
    if extension == ".docx":
        return build_docx_workspace(
            db=db,
            raw_bytes=raw_bytes,
            similarity_threshold=similarity_threshold,
        )
    
    # 其他格式使用适配器系统
    try:
        registry = get_registry()
        adapter = registry.get_adapter(filename)
        result = adapter.parse_with_validation(raw_bytes, filename)
    except (ParseError, FileTooLargeError) as e:
        raise ValueError(str(e)) from e
    except Exception as e:
        # 捕获其他异常（如 OCRRequiredError）
        raise ValueError(f"解析文件失败: {str(e)}") from e
    
    # 构建 HTML 和 segments
    sentence_counter = count(1)
    html_parts: list[str] = []
    segments: list[dict] = []
    
    for block_index, node in enumerate(result.ast.nodes):
        node_html, node_segments = _render_ast_node(
            node=node,
            sentence_counter=sentence_counter,
            block_index=block_index,
        )
        html_parts.append(node_html)
        segments.extend(node_segments)
    
    # 执行 TM 匹配
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


def _render_ast_node(
    node,
    sentence_counter,
    block_index: int,
    block_type: str = "paragraph",
) -> tuple[str, list[dict]]:
    """渲染 AST 节点为 HTML
    
    Args:
        node: BlockNode 节点
        sentence_counter: 句子计数器
        block_index: 块索引
        block_type: 块类型
        
    Returns:
        tuple[str, list[dict]]: (HTML 字符串, segments 列表)
    """
    from app.services.adapters.models import NodeType
    
    html_parts: list[str] = []
    segments: list[dict] = []
    
    # 处理文本内容
    if node.text_content:
        paragraph_html, paragraph_segments = _render_paragraph(
            text=node.text_content,
            sentence_counter=sentence_counter,
            block_index=block_index,
            block_type=block_type,
        )
        html_parts.append(paragraph_html)
        segments.extend(paragraph_segments)
    
    # 处理子节点
    if node.children:
        if node.node_type == NodeType.TABLE:
            # 表格特殊处理
            table_html, table_segments = _render_ast_table(
                node=node,
                sentence_counter=sentence_counter,
                block_index=block_index,
            )
            html_parts.append(table_html)
            segments.extend(table_segments)
        else:
            # 其他容器节点
            for child in node.children:
                child_html, child_segments = _render_ast_node(
                    node=child,
                    sentence_counter=sentence_counter,
                    block_index=block_index,
                    block_type=block_type,
                )
                html_parts.append(child_html)
                segments.extend(child_segments)
    
    return "".join(html_parts), segments


def _render_ast_table(
    node,
    sentence_counter,
    block_index: int,
) -> tuple[str, list[dict]]:
    """渲染 AST 表格节点为 HTML
    
    Args:
        node: 表格 BlockNode 节点
        sentence_counter: 句子计数器
        block_index: 块索引
        
    Returns:
        tuple[str, list[dict]]: (HTML 字符串, segments 列表)
    """
    from app.services.adapters.models import NodeType
    
    row_html_parts: list[str] = []
    table_segments: list[dict] = []
    
    if not node.children:
        return "", []
    
    for row_index, row_node in enumerate(node.children):
        if row_node.node_type != NodeType.TABLE_ROW:
            continue
            
        cell_html_parts: list[str] = []
        
        if row_node.children:
            for cell_index, cell_node in enumerate(row_node.children):
                cell_paragraphs: list[str] = []
                
                # 处理单元格内容
                if cell_node.text_content:
                    paragraph_html, paragraph_segments = _render_paragraph(
                        text=cell_node.text_content,
                        sentence_counter=sentence_counter,
                        block_index=block_index,
                        block_type="table_cell",
                        row_index=row_index,
                        cell_index=cell_index,
                    )
                    cell_paragraphs.append(paragraph_html)
                    table_segments.extend(paragraph_segments)
                
                # 处理单元格子节点
                if cell_node.children:
                    for child in cell_node.children:
                        if child.text_content:
                            paragraph_html, paragraph_segments = _render_paragraph(
                                text=child.text_content,
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
    
    if not row_html_parts:
        return "", []
    
    return f'<table class="doc-table"><tbody>{"".join(row_html_parts)}</tbody></table>', table_segments
